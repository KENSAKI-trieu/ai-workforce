"""Shared parent/subgraph state contract."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class WorkforceAgentState(TypedDict):
    tenant_id: str
    user_id: str
    role: str
    department: str
    conversation_id: str
    workflow_id: str | None
    messages: list[dict[str, Any]]
    intent: str
    selected_agent: str
    retrieved_context: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    approval_id: str | None
    final_answer: str | None
    errors: list[dict[str, Any]]
    requested_agent: NotRequired[str | None]
    available_tools: NotRequired[list[str]]
    domain_prompt: NotRequired[str]
    citation_required: NotRequired[bool]
    pending_tool_call: NotRequired[dict[str, Any] | None]
    model_iterations: NotRequired[int]
    execution_trace: NotRequired[list[dict[str, Any]]]
    is_complete: NotRequired[bool]
