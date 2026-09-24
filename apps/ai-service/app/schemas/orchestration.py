"""Request and response bodies of the /v1/orchestration endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class OrchestrationRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=50000)
    # Always named by the backend: the user talks to one agent, and the AI service
    # does not guess one.
    requested_agent: str = Field(min_length=1, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    denied_tools: list[str] = Field(default_factory=list, max_length=100)


class OrchestrationResumeRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    agent_role: str = Field(min_length=1, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    denied_tools: list[str] = Field(default_factory=list, max_length=100)
    resume: Any


class OrchestrationResponse(BaseModel):
    thread_id: str
    status: Literal["COMPLETED", "AWAITING_APPROVAL"]
    state: dict[str, Any]
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
