from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, Optional
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

import bcrypt

# ---------------------------------------------------------------------------
# Password Hashing (Direct bcrypt to avoid passlib bug with bcrypt 5.x)
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


# ---------------------------------------------------------------------------
# Token Extraction (Authorization: Bearer only)
# ---------------------------------------------------------------------------
def get_token_from_request(
    header_token: Optional[str] = Depends(oauth2_scheme),
) -> str:
    """Read the access token from the Authorization header, and only from there.

    This used to fall back to an `access_token` cookie that the backend never sets.
    The branch was unreachable, but it left the API one line away from cookie-borne
    authentication -- and with CORS running `allow_credentials=True` and no CSRF token
    anywhere, that line would have made every state-changing endpoint forgeable from
    another site. Only the refresh flow uses a cookie, and it is scoped to /api/v1/auth.
    """
    if header_token:
        return header_token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# JWT Token Creation
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str | UUID,
    role: str,
    tenant_id: str | UUID,
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(subject),
        "role": role,
        "tenant_id": str(tenant_id),
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


class IssuedRefreshToken(NamedTuple):
    """A refresh token and the identity the server has to store to be able to revoke it."""

    token: str
    jti: UUID
    family_id: UUID
    expires_at: datetime


def create_refresh_token(
    subject: str | UUID,
    *,
    family_id: Optional[UUID] = None,
    expires_delta: Optional[timedelta] = None,
) -> IssuedRefreshToken:
    """Mint a refresh token that the server can later recognise and revoke.

    `jti` identifies this exact token and `family_id` the login it descends from; both
    are recorded in `refresh_tokens`. Reusing an already-rotated token means the
    credential exists in two places at once, and the family is what gets revoked.
    """
    issued_at = datetime.now(timezone.utc)
    expire = issued_at + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    jti = uuid4()
    family = family_id or uuid4()
    payload = {
        "sub": str(subject),
        "type": "refresh",
        "jti": str(jti),
        "family_id": str(family),
        "exp": expire,
        "iat": issued_at,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return IssuedRefreshToken(token=token, jti=jti, family_id=family, expires_at=expire)


def create_internal_tool_token(
    user: Any,
    *,
    agent_role: str | None = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a short-lived, backend-issued identity token for tool execution.

    `actor_kind` states whether an AI Employee's configuration applies to this token. The
    gateway used to infer that from a missing `agent_role`, which meant a token without the
    claim silently skipped the per-agent tool grants; saying it outright turns that into a
    decision the minting side has to make.
    """
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=5))
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "type": "internal_tool",
        "iss": "ai-workforce-backend",
        "aud": "internal-tool-gateway",
        "jti": str(uuid4()),
        "agent_role": agent_role,
        "actor_kind": "AGENT" if agent_role else "USER",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_internal_tool_token(token: str) -> dict[str, Any]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal tool credential",
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience="internal-tool-gateway",
            issuer="ai-workforce-backend",
        )
    except JWTError as exc:
        raise credentials_exception from exc
    if (
        payload.get("type") != "internal_tool"
        or not payload.get("sub")
        or not payload.get("tenant_id")
        or not payload.get("jti")
    ):
        raise credentials_exception
    return payload


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token, raising 401 on failure."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise credentials_exception


# ---------------------------------------------------------------------------
# Current User Dependency
# ---------------------------------------------------------------------------
def get_current_user(
    token: str = Depends(get_token_from_request),
    db: Session = Depends(get_db),
):
    """Decode JWT and return the current authenticated user ORM object."""
    from app.models.models import User  # avoid circular import

    payload = decode_token(token)
    user_id: str = payload.get("sub")
    token_type: str = payload.get("type")

    if not user_id or token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user = db.query(User).filter(User.id == user_id).first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def get_current_active_user(current_user=Depends(get_current_user)):
    """Ensure the user account is active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    return current_user


# ---------------------------------------------------------------------------
# RBAC Role Checker
# ---------------------------------------------------------------------------
class RoleRequired:
    """Dependency factory to restrict access by role.

    Legacy. New guards should use PermissionRequired, which asks what the user may do
    rather than what their job is called, so a company can rename its own positions.
    """

    def __init__(self, *allowed_roles: str):
        self.allowed_roles = set(allowed_roles)

    async def __call__(self, current_user=Depends(get_current_active_user)):
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(self.allowed_roles)}",
            )
        return current_user


class PermissionRequired:
    """Dependency factory to restrict access by position permission.

    Holding ANY of the supplied codes is enough, matching how the role sets it replaces
    behaved (membership in any listed role granted access).
    """

    def __init__(self, *codes: str):
        self.codes = tuple(codes)

    async def __call__(
        self,
        current_user=Depends(get_current_active_user),
        db: Session = Depends(get_db),
    ):
        from app.domains.platform.position_service import user_permissions

        granted = user_permissions(db, current_user)
        if not any(code in granted for code in self.codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required permission: {', '.join(self.codes)}",
            )
        return current_user
