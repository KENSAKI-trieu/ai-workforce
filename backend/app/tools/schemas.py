"""Pydantic contracts accepted by the internal tool gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class AuditMetadata(BaseModel):
    """Caller-supplied trace data; never used as an authorization source."""

    correlation_id: UUID
    conversation_id: UUID | None = None
    workflow_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class TenantToolInput(BaseModel):
    tenant_id: UUID
    audit: AuditMetadata


class IdempotentToolInput(TenantToolInput):
    @model_validator(mode="after")
    def require_idempotency_key(self) -> "IdempotentToolInput":
        if not self.audit.idempotency_key:
            raise ValueError("audit.idempotency_key is required for mutating tools")
        return self


class RAGSearchInput(TenantToolInput):
    query: str = Field(min_length=2, max_length=5000)
    top_k: int = Field(default=5, ge=1, le=20)
    collections: list[str] | None = Field(default=None, max_length=20)


class EmployeeLookupInput(TenantToolInput):
    employee_id: UUID
    sections: list[Literal["BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"]] = Field(
        default_factory=lambda: ["BASIC"], min_length=1, max_length=5
    )
    purpose: Literal[
        "SELF_SERVICE",
        "DIRECTORY_LOOKUP",
        "LEAVE_MANAGEMENT",
        "CONTRACT_RENEWAL",
        "PAYROLL_PROCESSING",
        "HR_OPERATIONS",
        "EMPLOYEE_SUPPORT",
        "EXECUTIVE_REVIEW",
    ] = "DIRECTORY_LOOKUP"


class LeaveLookupInput(TenantToolInput):
    employee_id: UUID | None = None
    year: int | None = Field(default=None, ge=2000, le=2100)


class CreateTaskInput(IdempotentToolInput):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    assignee_id: UUID | None = None
    ai_agent_id: UUID | None = None
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    due_date: datetime | None = None


class ExpenseLookupInput(TenantToolInput):
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    breakdown: Literal["SUMMARY", "AGENT", "EMPLOYEE", "DEPARTMENT", "WORKFLOW"] = "SUMMARY"


class GenerateLegalDocumentInput(IdempotentToolInput):
    document_type: str = Field(min_length=2, max_length=100)
    output_format: Literal["docx", "pdf"] = "docx"
    fields: dict[str, Any]

    @field_validator("document_type")
    @classmethod
    def normalize_document_type(cls, value: str) -> str:
        return value.strip().upper()


class SubmitApprovalInput(IdempotentToolInput):
    title: str = Field(min_length=2, max_length=255)
    action_type: Literal[
        "GENERAL_APPROVAL",
        "TASK_APPROVAL",
        "EXPENSE_APPROVAL",
        "CONTENT_APPROVAL",
        "EXTERNAL_ACTION_APPROVAL",
    ]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    payload: dict[str, Any]
    approver_id: UUID | None = None
    expires_at: datetime | None = None

    @field_validator("action_type", mode="before")
    @classmethod
    def normalize_action_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("expires_at")
    @classmethod
    def require_future_expiry(cls, value: datetime | None) -> datetime | None:
        """An already-past deadline creates a gate that is EXPIRED before anyone sees it."""
        if value is None:
            return value
        deadline = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value


class ContractRiskReviewInput(TenantToolInput):
    """Review contract text the user sent in this conversation.

    Neither the text nor the user's side is an argument. The backend reads both from the
    user's own messages, so what is reviewed is exactly what they sent, from the side they
    said they act for: a model given the side as a parameter filled in NEUTRAL for a user
    who had not answered.
    """

    from_user_message: int = Field(
        default=0,
        ge=0,
        le=5,
        description=(
            "Which of the user's messages holds the contract: 0 is the current message, "
            "1 the one before it, and so on. Use 1 when the current message only answers "
            "which side the user represents."
        ),
    )
    document_scope: Literal["FULL", "EXCERPT"] | None = Field(
        default=None,
        description=(
            "FULL for a whole contract, EXCERPT for a clause or passage. An excerpt is not "
            "faulted for clauses it does not contain. Omit to let the backend decide."
        ),
    )
