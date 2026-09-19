"""
Pydantic schemas for Authentication endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    validate_password,
)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    tenant_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Company/workspace name. Public registration always creates a new workspace.",
    )

    _check_password = field_validator("password")(validate_password)


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    _check_password = field_validator("new_password")(validate_password)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# `TokenResponse` and `TokenRefreshRequest` used to live here. Both carried a
# refresh_token field, and both are gone: the refresh token is never serialised to a
# client any more. It exists only as an HttpOnly cookie scoped to /api/v1/auth, so
# script running on the page cannot read it and cannot send it anywhere.


# ---------------------------------------------------------------------------
# User in response
# ---------------------------------------------------------------------------
class UserInToken(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    # The company's own name for this job. `role` stays for guards that branch on it.
    position_name: Optional[str] = None
    department: str
    tenant_id: UUID
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInToken
