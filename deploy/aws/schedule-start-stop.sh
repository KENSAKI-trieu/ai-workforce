#!/usr/bin/env bash
# Runs the instance on weekday business hours only. This is what makes the
# AWS option cost roughly a third of a 24/7 instance: EC2 bills per second while
# running, and a stopped instance bills nothing for compute.
#
#   ./deploy/aws/schedule-start-stop.sh i-0123456789abcdef0
#
# The EBS volume is still billed while stopped - that is what preserves the
# database and uploaded documents between sessions.
set -euo pipefail

INSTANCE_ID="${1:-}"
REGION="${AWS_REGION:-ap-northeast-1}"
TZ_NAME="${TZ_NAME:-Asia/Tokyo}"
START_CRON="${START_CRON:-cron(0 9 ? * MON-FRI *)}"   # 09:00 on weekdays
STOP_CRON="${STOP_CRON:-cron(0 21 ? * MON-FRI *)}"    # 21:00 on weekdays
ROLE_NAME="${ROLE_NAME:-ai-workforce-scheduler}"

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
aws_() { aws --region "${REGION}" "$@"; }

[ -n "${INSTANCE_ID}" ] || fail "usage: $0 <instance-id>"
ACCOUNT_ID=$(aws_ sts get-caller-identity --query Account --output text) \
  || fail "no valid AWS credentials - run: aws sso login"

log "Ensuring IAM role ${ROLE_NAME}"
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam create-role --role-name "${ROLE_NAME}" \
    --description "Lets EventBridge Scheduler start and stop the ai-workforce test host" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "scheduler.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  # IAM is eventually consistent; a brand new role is not immediately assumable.
  sleep 10
fi

aws iam put-role-policy --role-name "${ROLE_NAME}" --policy-name start-stop \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [\"ec2:StartInstances\", \"ec2:StopInstances\"],
      \"Resource\": \"arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}\"
    }]
  }" >/dev/null

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

put_schedule() {
  local name="$1" cron="$2" action="$3"
  local args=(
    --name "${name}"
    --schedule-expression "${cron}"
    --schedule-expression-timezone "${TZ_NAME}"
    --flexible-time-window '{"Mode":"OFF"}'
    --target "{
        \"Arn\": \"arn:aws:scheduler:::aws-sdk:ec2:${action}\",
        \"RoleArn\": \"${ROLE_ARN}\",
        \"Input\": \"{\\\"InstanceIds\\\":[\\\"${INSTANCE_ID}\\\"]}\"
      }"
  )
  if aws_ scheduler get-schedule --name "${name}" >/dev/null 2>&1; then
    aws_ scheduler update-schedule "${args[@]}" >/dev/null
    echo "    updated ${name}"
  else
    aws_ scheduler create-schedule "${args[@]}" >/dev/null
    echo "    created ${name}"
  fi
}

log "Creating schedules (${TZ_NAME})"
put_schedule "ai-workforce-start" "${START_CRON}" "startInstances"
put_schedule "ai-workforce-stop"  "${STOP_CRON}"  "stopInstances"

cat <<EOF

  Start: ${START_CRON}  ${TZ_NAME}
  Stop : ${STOP_CRON}  ${TZ_NAME}

  The stack comes up on its own: every compose service uses 'restart: always',
  so Docker restarts them when the instance boots.

  Run it outside those hours with:
    aws --region ${REGION} ec2 start-instances --instance-ids ${INSTANCE_ID}
EOF
