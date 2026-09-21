#!/usr/bin/env bash
# Creates the EC2 instance that runs the stack. Idempotent: re-running reuses
# an existing instance and security group instead of creating duplicates.
#
#   AWS_PROFILE=<profile> ./deploy/aws/provision-ec2.sh
#
# No Elastic IP is allocated. The Cloudflare Tunnel dials out, so the instance
# never needs a stable inbound address - which also means the public IP changing
# on every start/stop costs us nothing.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
NAME="${NAME:-ai-workforce-test}"
# t4g is Graviton (arm64). Every image in this stack is a native arm64 build -
# including ankane/pgvector - so nothing runs under emulation.
# Unlimited CPU credits: the stack idles near 0% CPU, so the only burst is the
# one-off image build, whose surplus-credit surcharge is a few cents.
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.large}"   # 2 vCPU / 8 GB, Graviton (arm64)
VOLUME_GB="${VOLUME_GB:-80}"
KEY_NAME="${KEY_NAME:-}"
SSH_CIDR="${SSH_CIDR:-}"

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
aws_() { aws --region "${REGION}" "$@"; }

[ -n "${KEY_NAME}" ] || fail "set KEY_NAME to an EC2 key pair name (aws ec2 describe-key-pairs)"
aws_ sts get-caller-identity >/dev/null 2>&1 || fail "no valid AWS credentials - run: aws sso login"

if [ -z "${SSH_CIDR}" ]; then
  SSH_CIDR="$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')/32"
  log "Restricting SSH to your current address ${SSH_CIDR}"
fi

# Ubuntu 24.04 arm64. Canonical publishes the current AMI id as a public SSM
# parameter, so we never hardcode an id that goes stale.
log "Resolving the latest Ubuntu 24.04 arm64 AMI"
AMI_ID=$(aws_ ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)
[ -n "${AMI_ID}" ] && [ "${AMI_ID}" != "None" ] || fail "could not resolve the Ubuntu AMI id"
echo "    ${AMI_ID}"

log "Ensuring security group '${NAME}-sg'"
VPC_ID=$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
[ "${VPC_ID}" != "None" ] || fail "no default VPC in ${REGION} - create one or set it manually"

SG_ID=$(aws_ ec2 describe-security-groups \
  --filters "Name=group-name,Values=${NAME}-sg" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [ "${SG_ID}" = "None" ] || [ -z "${SG_ID}" ]; then
  SG_ID=$(aws_ ec2 create-security-group --group-name "${NAME}-sg" --vpc-id "${VPC_ID}" \
    --description "ai-workforce test host: SSH in, everything else via Cloudflare Tunnel" \
    --query 'GroupId' --output text)
fi
echo "    ${SG_ID}"

# Only SSH. The application is published through the tunnel, which is an
# outbound connection, so no inbound application port is ever opened.
aws_ ec2 authorize-security-group-ingress --group-id "${SG_ID}" \
  --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${SSH_CIDR},Description=admin-ssh}]" \
  >/dev/null 2>&1 || echo "    (SSH rule already present)"

INSTANCE_ID=$(aws_ ec2 describe-instances \
  --filters "Name=tag:Name,Values=${NAME}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")

if [ "${INSTANCE_ID}" = "None" ] || [ -z "${INSTANCE_ID}" ]; then
  log "Launching ${INSTANCE_TYPE} (${VOLUME_GB} GB gp3)"
  INSTANCE_ID=$(aws_ ec2 run-instances \
    --image-id "${AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids "${SG_ID}" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${VOLUME_GB},VolumeType=gp3,DeleteOnTermination=true,Encrypted=true}" \
    --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
    --credit-specification "CpuCredits=unlimited" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=Project,Value=ai-workforce}]" \
    --query 'Instances[0].InstanceId' --output text)
else
  log "Reusing existing instance ${INSTANCE_ID}"
  state=$(aws_ ec2 describe-instances --instance-ids "${INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)
  [ "${state}" = "running" ] || aws_ ec2 start-instances --instance-ids "${INSTANCE_ID}" >/dev/null
fi

log "Waiting for the instance to run"
aws_ ec2 wait instance-running --instance-ids "${INSTANCE_ID}"
PUBLIC_IP=$(aws_ ec2 describe-instances --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

cat <<EOF

  Instance : ${INSTANCE_ID}
  Public IP: ${PUBLIC_IP}   (changes on every stop/start - that is fine)

  Next:
    ssh ubuntu@${PUBLIC_IP} 'sudo bash -s' < deploy/bootstrap-vps.sh
    ./deploy/aws/schedule-start-stop.sh ${INSTANCE_ID}
EOF
