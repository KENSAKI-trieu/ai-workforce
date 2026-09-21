#!/usr/bin/env bash
# Creates the AIWorkforceDeployer permission set in an existing IAM Identity
# Center instance and assigns it to a user for one AWS account.
#
#   ACCOUNT_ID=123456789012 PRINCIPAL=takeru ./deploy/aws/setup-permission-set.sh
#
# Run it with credentials that can administer Identity Center (e.g. the
# AdministratorAccess permission set). Idempotent: re-running updates the
# policy and re-provisions rather than creating duplicates.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"          # must be the instance's region
ACCOUNT_ID="${ACCOUNT_ID:-}"
PRINCIPAL="${PRINCIPAL:-}"                       # Identity Center user name
PRINCIPAL_TYPE="${PRINCIPAL_TYPE:-USER}"         # or GROUP
PS_NAME="${PS_NAME:-AIWorkforceDeployer}"
SESSION_DURATION="${SESSION_DURATION:-PT12H}"    # one working day per login
POLICY_FILE="$(dirname "$0")/deployer-policy.json"

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
aws_() { aws --region "${REGION}" "$@"; }

[ -n "${ACCOUNT_ID}" ] || fail "set ACCOUNT_ID to the target AWS account (12 digits)"
[ -n "${PRINCIPAL}" ] || fail "set PRINCIPAL to an Identity Center user or group name"
[ -f "${POLICY_FILE}" ] || fail "missing ${POLICY_FILE}"
aws_ sts get-caller-identity >/dev/null 2>&1 || fail "no valid credentials - run: aws sso login"

log "Locating the Identity Center instance"
read -r INSTANCE_ARN STORE_ID < <(aws_ sso-admin list-instances \
  --query 'Instances[0].[InstanceArn,IdentityStoreId]' --output text)
[ -n "${INSTANCE_ARN}" ] && [ "${INSTANCE_ARN}" != "None" ] \
  || fail "no Identity Center instance visible in ${REGION}"
echo "    ${INSTANCE_ARN}"

log "Ensuring permission set ${PS_NAME}"
PS_ARN=""
for arn in $(aws_ sso-admin list-permission-sets --instance-arn "${INSTANCE_ARN}" \
               --query 'PermissionSets[]' --output text); do
  name=$(aws_ sso-admin describe-permission-set --instance-arn "${INSTANCE_ARN}" \
           --permission-set-arn "${arn}" --query 'PermissionSet.Name' --output text)
  if [ "${name}" = "${PS_NAME}" ]; then PS_ARN="${arn}"; break; fi
done

if [ -z "${PS_ARN}" ]; then
  PS_ARN=$(aws_ sso-admin create-permission-set \
    --instance-arn "${INSTANCE_ARN}" --name "${PS_NAME}" \
    --description "Deploy and operate the ai-workforce test host" \
    --session-duration "${SESSION_DURATION}" \
    --query 'PermissionSet.PermissionSetArn' --output text)
  echo "    created ${PS_ARN}"
else
  aws_ sso-admin update-permission-set --instance-arn "${INSTANCE_ARN}" \
    --permission-set-arn "${PS_ARN}" --session-duration "${SESSION_DURATION}" >/dev/null
  echo "    reusing ${PS_ARN}"
fi

log "Attaching the inline policy"
aws_ sso-admin put-inline-policy-to-permission-set \
  --instance-arn "${INSTANCE_ARN}" --permission-set-arn "${PS_ARN}" \
  --inline-policy "file://${POLICY_FILE}"

log "Resolving ${PRINCIPAL_TYPE} '${PRINCIPAL}'"
if [ "${PRINCIPAL_TYPE}" = "USER" ]; then
  PRINCIPAL_ID=$(aws_ identitystore list-users --identity-store-id "${STORE_ID}" \
    --filters "AttributePath=UserName,AttributeValue=${PRINCIPAL}" \
    --query 'Users[0].UserId' --output text)
else
  PRINCIPAL_ID=$(aws_ identitystore list-groups --identity-store-id "${STORE_ID}" \
    --filters "AttributePath=DisplayName,AttributeValue=${PRINCIPAL}" \
    --query 'Groups[0].GroupId' --output text)
fi
[ -n "${PRINCIPAL_ID}" ] && [ "${PRINCIPAL_ID}" != "None" ] \
  || fail "no ${PRINCIPAL_TYPE} named '${PRINCIPAL}' in identity store ${STORE_ID}"
echo "    ${PRINCIPAL_ID}"

log "Assigning to account ${ACCOUNT_ID}"
REQ_STATUS=$(aws_ sso-admin create-account-assignment \
  --instance-arn "${INSTANCE_ARN}" --permission-set-arn "${PS_ARN}" \
  --target-id "${ACCOUNT_ID}" --target-type AWS_ACCOUNT \
  --principal-type "${PRINCIPAL_TYPE}" --principal-id "${PRINCIPAL_ID}" \
  --query 'AccountAssignmentCreationStatus.Status' --output text)
echo "    ${REQ_STATUS}"

# An inline-policy change only reaches the account's IAM role after a
# re-provision, so do it explicitly rather than hoping the assignment covers it.
log "Re-provisioning so the policy change reaches the account"
aws_ sso-admin provision-permission-set --instance-arn "${INSTANCE_ARN}" \
  --permission-set-arn "${PS_ARN}" --target-id "${ACCOUNT_ID}" \
  --target-type AWS_ACCOUNT --query 'PermissionSetProvisioningStatus.Status' --output text

cat <<EOF

  Permission set : ${PS_NAME} (${SESSION_DURATION} sessions)
  Account        : ${ACCOUNT_ID}
  Principal      : ${PRINCIPAL_TYPE} ${PRINCIPAL}

  Add the CLI profile (reuses the existing sso-session):

    aws configure sso --profile kensaki
      SSO session name : spd-ai-dev
      account          : ${ACCOUNT_ID}
      role             : ${PS_NAME}

  Then: aws sso login --profile kensaki && aws sts get-caller-identity --profile kensaki
EOF
