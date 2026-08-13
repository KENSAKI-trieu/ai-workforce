"""Trusted per-run context supplied by the backend, never by the model."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentRuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    user_id: UUID
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    agent_role: str = Field(min_length=1, max_length=50)
    correlation_id: UUID
    conversation_id: UUID | None = None
    workflow_id: UUID | None = None
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    denied_tools: frozenset[str] = Field(default_factory=frozenset)
    citation_required: bool = False
    allowed_citation_sources: frozenset[str] = Field(default_factory=frozenset)
    complexity_hint: Literal["simple", "standard", "complex"] | None = None

    def is_tool_allowed(self, name: str) -> bool:
        return name in self.allowed_tools and name not in self.denied_tools
