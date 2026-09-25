"""Request and response bodies of the /v1/orchestration endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class DisabledTool(BaseModel):
    """A tool the agent's role has but the organisation turned off."""

    name: str = Field(min_length=1, max_length=100)
    # What the user is told the feature is, in the product's language.
    label: str = Field(min_length=1, max_length=200)


class OrchestrationRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=50000)
    # Earlier turns of the conversation, oldest first, already trimmed by the backend.
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
    # Always named by the backend: the user talks to one agent, and the AI service
    # does not guess one.
    requested_agent: str = Field(min_length=1, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    denied_tools: list[str] = Field(default_factory=list, max_length=100)
    # Never bound or run; named to the model only so a request needing one is answered
    # with "this feature is turned off" instead of an attempt from general knowledge.
    disabled_tools: list[DisabledTool] = Field(default_factory=list, max_length=100)
    # What the tenant added to this agent's reply prompt; resolved by the backend's
    # plugin resolver. Tone and terminology only -- the graph places it after its rules.
    tenant_instructions: str = Field(default="", max_length=8000)


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
    disabled_tools: list[DisabledTool] = Field(default_factory=list, max_length=100)
    tenant_instructions: str = Field(default="", max_length=8000)
    resume: Any


class OrchestrationResponse(BaseModel):
    thread_id: str
    status: Literal["COMPLETED", "AWAITING_APPROVAL"]
    state: dict[str, Any]
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
