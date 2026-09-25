"""Boto3 clients for the AWS-backed providers.

Kept apart from the provider itself so the credential handling -- which is the
only genuinely fiddly part of talking to Bedrock -- has one home.
"""

from functools import lru_cache
from typing import Any

from app.core.config import settings


def _assumed_role_session(role_arn: str) -> Any:
    """Return a boto3 Session whose credentials refresh themselves.

    A plain `sts.assume_role` hands back credentials that expire in an hour, which
    would take the service down mid-shift. Wiring the call into botocore's
    refreshable-credential machinery instead means the SDK re-assumes the role on
    its own, before expiry, for as long as the process lives.
    """
    import boto3
    from botocore.credentials import DeferredRefreshableCredentials
    from botocore.session import get_session

    sts = boto3.client("sts", region_name=settings.BEDROCK_REGION)

    def _refresh() -> dict[str, str]:
        response = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=settings.BEDROCK_ROLE_SESSION_NAME,
        )["Credentials"]
        return {
            "access_key": response["AccessKeyId"],
            "secret_key": response["SecretAccessKey"],
            "token": response["SessionToken"],
            "expiry_time": response["Expiration"].isoformat(),
        }

    botocore_session = get_session()
    botocore_session._credentials = DeferredRefreshableCredentials(
        refresh_using=_refresh,
        method="sts-assume-role",
    )
    return boto3.Session(botocore_session=botocore_session)


@lru_cache(maxsize=1)
def bedrock_runtime_client() -> Any:
    """Return the shared bedrock-runtime client.

    Without BEDROCK_ASSUME_ROLE_ARN this is the default boto3 credential chain, so
    an EC2 instance profile, ambient environment variables or a mounted AWS config
    all work without the service knowing which one it got.
    """
    import boto3

    role_arn = settings.BEDROCK_ASSUME_ROLE_ARN
    session = _assumed_role_session(role_arn) if role_arn else boto3.Session()
    return session.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
