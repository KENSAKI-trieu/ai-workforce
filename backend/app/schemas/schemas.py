"""Pydantic schemas for User and AIAgent endpoints."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, computed_field

from app.core.agent_status import is_under_development


# ---------------------------------------------------------------------------
# User schemas
# ---------------------------------------------------------------------------
class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: str = "Employee"
    department: str = "ALL"
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    tenant_id: UUID


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class UserResponse(UserBase):
    id: UUID
    tenant_id: UUID
    # The company's own name for this job, read off the assigned position.
    position_name: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfilePreferences(BaseModel):
    hobbies: list[str] = Field(default_factory=list, max_length=20)
    preferred_language: str = Field(default="vi", min_length=2, max_length=10)
    timezone: str = Field(default="Asia/Ho_Chi_Minh", min_length=2, max_length=60)
    work_style: Optional[str] = Field(None, max_length=100)
    theme: Literal["light", "dark", "system"] = "system"
    communication_channels: list[str] = Field(default_factory=list, max_length=10)


class SelfProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    address: Optional[str] = Field(None, max_length=1000)
    city: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    date_of_birth: Optional[date] = None
    gender: Optional[Literal["male", "female", "non_binary", "prefer_not_to_say"]] = None
    bio: Optional[str] = Field(None, max_length=2000)
    emergency_contact_name: Optional[str] = Field(None, max_length=255)
    emergency_contact_phone: Optional[str] = Field(None, max_length=30)
    preferences: Optional[ProfilePreferences] = None


class EmploymentProfileUpdate(BaseModel):
    job_title: Optional[str] = Field(None, max_length=150)
    employee_code: Optional[str] = Field(None, max_length=50)
    hire_date: Optional[date] = None
    monthly_salary: Optional[Decimal] = Field(None, ge=0, max_digits=18, decimal_places=2)
    salary_currency: str = Field(default="VND", min_length=3, max_length=3)
    leave_total_days: Optional[float] = Field(None, ge=0, le=365)
    leave_used_days: Optional[float] = Field(None, ge=0, le=365)


# ---------------------------------------------------------------------------
# AI Agent schemas
# ---------------------------------------------------------------------------
class AIAgentBase(BaseModel):
    name: str
    role_code: str
    system_prompt: str
    prompt_overlay: Optional[str] = None
    model_name: str = "gpt-4o"
    is_active: bool = True
    tools_access: list = Field(default_factory=list)
    allowed_actions: list = Field(default_factory=list)
    disallowed_actions: list = Field(default_factory=list)
    knowledge_access: list = Field(default_factory=list)
    avatar_emoji: Optional[str] = None
    description: Optional[str] = None


class AIAgentCreate(AIAgentBase):
    tenant_id: UUID


class AIAgentResponse(AIAgentBase):
    id: UUID
    tenant_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def under_development(self) -> bool:
        """Listed so the product shows where it is heading, but not open for chat yet."""
        return is_under_development(self.role_code)


# ---------------------------------------------------------------------------
# Workflow schemas
# ---------------------------------------------------------------------------
class WorkflowResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    initiator_id: UUID
    title: str
    status: str
    current_step: int
    dag_plan: Optional[dict] = None
    thread_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalActionRequest(BaseModel):
    action: str = Field(..., pattern="^(APPROVE|REJECT)$")
    comments: Optional[str] = None
