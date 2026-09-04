"""
Pydantic schemas for Authentication endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    tenant_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Company/workspace name. Public registration always creates a new workspace.",
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    refresh_token: str


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
    refresh_token: str
    token_type: str = "bearer"
    user: UserInToken
