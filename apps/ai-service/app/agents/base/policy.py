"""What defines one agent's domain: its tool ceiling, its prompt and its citation rule."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.base.state import WorkforceAgentState


@dataclass(frozen=True)
class DomainPolicy:
    agent: str
    # The ceiling: tools this agent may ever be offered. The backend's grant for the
    # AI Employee is intersected with it, so a grant outside the domain is never bound.
    tools: tuple[str, ...]
    prompt: str
    citation_required: bool = True

    @property
    def node_prefix(self) -> str:
        return self.agent.lower()


def _trace(state: WorkforceAgentState, node: str) -> list[dict[str, Any]]:
    return [*(state.get("execution_trace") or []), {"node": node, "status": "COMPLETED"}]


def policy_node(policy: DomainPolicy):
    """Apply the domain's prompt and citation rule to the turn."""

    def apply_policy(state: WorkforceAgentState) -> dict[str, Any]:
        return {
            "selected_agent": policy.agent,
            "intent": f"{policy.agent}_REQUEST",
            "domain_prompt": policy.prompt,
            "citation_required": policy.citation_required,
            "execution_trace": _trace(state, f"{policy.node_prefix}_policy"),
        }

    return apply_policy


def tool_scope_node(policy: DomainPolicy):
    """Narrow the turn's granted tools to the domain's ceiling."""

    def scope_tools(state: WorkforceAgentState) -> dict[str, Any]:
        requested = set(state.get("available_tools") or [])
        return {
            "available_tools": [tool for tool in policy.tools if tool in requested],
            "execution_trace": _trace(state, f"{policy.node_prefix}_tool_scope"),
        }

    return scope_tools
