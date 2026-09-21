#!/usr/bin/env bash
# Gives the test host permission to call Claude on Bedrock, in this same account.
#
#   AWS_PROFILE=kensaki ./deploy/aws/setup-bedrock-role.sh i-064511f6de854ec31
#
# Creates an EC2 role + instance profile and attaches it. boto3 inside the
# ai-service container then picks the credentials up from the instance metadata
# service, so no key or secret is ever written to disk.
set -euo pipefail

INSTANCE_ID="${1:-}"
REGION="${AWS_REGION:-ap-northeast-1}"
ROLE_NAME="${ROLE_NAME:-ai-workforce-ec2}"
PROFILE_NAME="${PROFILE_NAME:-ai-workforce-ec2}"

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
aws_() { aws --region "${REGION}" "$@"; }

[ -n "${INSTANCE_ID}" ] || fail "usage: $0 <instance-id>"
ACCOUNT_ID=$(aws_ sts get-caller-identity --query Account --output text) \
  || fail "no valid credentials - run: aws sso login"

log "Ensuring role ${ROLE_NAME}"
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam create-role --role-name "${ROLE_NAME}" \
    --description "Lets the ai-workforce test host invoke Claude on Bedrock" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  echo "    created"
else
  echo "    already exists"
fi

# Both resource types are required: the request names the inference profile, and
# Bedrock separately authorises the foundation model the profile routes to.
log "Attaching the Bedrock invoke policy"
aws iam put-role-policy --role-name "${ROLE_NAME}" --policy-name invoke-claude \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [\"bedrock:InvokeModel\", \"bedrock:InvokeModelWithResponseStream\"],
      \"Resource\": [
        \"arn:aws:bedrock:*::foundation-model/anthropic.*\",
        \"arn:aws:bedrock:*:${ACCOUNT_ID}:inference-profile/*.anthropic.*\"
      ]
    }]
  }" >/dev/null

log "Ensuring instance profile ${PROFILE_NAME}"
if ! aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null
  aws iam add-role-to-instance-profile \
    --instance-profile-name "${PROFILE_NAME}" --role-name "${ROLE_NAME}" >/dev/null
  # IAM is eventually consistent; attaching too soon fails with "Invalid IAM Instance Profile".
  sleep 12
  echo "    created"
else
  echo "    already exists"
fi

log "Attaching to ${INSTANCE_ID}"
existing=$(aws_ ec2 describe-iam-instance-profile-associations \
  --filters "Name=instance-id,Values=${INSTANCE_ID}" \
  --query 'IamInstanceProfileAssociations[?State!=`disassociated`].AssociationId' \
  --output text 2>/dev/null || true)
if [ -n "${existing}" ] && [ "${existing}" != "None" ]; then
  aws_ ec2 replace-iam-instance-profile-association \
    --association-id "${existing}" \
    --iam-instance-profile "Name=${PROFILE_NAME}" >/dev/null
  echo "    replaced existing association"
else
  aws_ ec2 associate-iam-instance-profile --instance-id "${INSTANCE_ID}" \
    --iam-instance-profile "Name=${PROFILE_NAME}" >/dev/null
  echo "    associated"
fi

echo
echo "  Role: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "  The instance can now call Bedrock without any stored credentials."
