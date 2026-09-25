"""Role → compiled agent graph, and the engine the orchestration API runs turns on.

The user always talks to one agent, and the backend names it, so there is no parent
graph guessing between agents. The role comes from the trusted runtime context; an
unknown role is refused rather than sent to some default agent.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.types import Command

from app.agents.base.graph import build_agent_graph, public_phase
from app.agents.base.nodes import OrchestrationRuntimeContext
from app.agents.base.state import WorkforceAgentState
from app.agents.ceo.agent import POLICY as CEO_POLICY
from app.agents.customer_support.agent import POLICY as CUSTOMER_SUPPORT_POLICY
from app.agents.finance.agent import POLICY as FINANCE_POLICY
from app.agents.hr.graph import build_graph as build_hr_graph
from app.agents.knowledge.graph import build_graph as build_knowledge_graph
from app.agents.legal.graph import build_graph as build_legal_graph

GRAPH_BUILDERS: dict[str, Any] = {
    "HR": build_hr_graph,
    "LEGAL": build_legal_graph,
    "KNOWLEDGE": build_knowledge_graph,
    # Under development: the backend never routes these roles here yet.
    "FINANCE": lambda: build_agent_graph(FINANCE_POLICY),
    "CUSTOMER_SUPPORT": lambda: build_agent_graph(CUSTOMER_SUPPORT_POLICY),
    "CEO": lambda: build_agent_graph(CEO_POLICY),
}
AGENTS = frozenset(GRAPH_BUILDERS)
# Backend AI Employees served by another agent's graph.
ROLE_ALIASES = {"IT": "CUSTOMER_SUPPORT", "SALES": "CUSTOMER_SUPPORT"}


def resolve_agent(role: str | None) -> str:
    """The graph a backend role runs on; raises ValueError for a role with none."""
    normalized = str(role or "").strip().upper()
    agent = ROLE_ALIASES.get(normalized, normalized)
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent role: {role!r}")
    return agent


class LangGraphEngine:
    def __init__(self, *, checkpointer: Any | None = None) -> None:
        # One checkpointer for every graph. A thread is one conversation, and a
        # conversation belongs to one agent, so threads never cross graphs.
        self.checkpointer = checkpointer or InMemorySaver()
        self.graphs = {
            agent: builder().compile(checkpointer=self.checkpointer)
            for agent, builder in GRAPH_BUILDERS.items()
        }

    def graph_for(self, context: OrchestrationRuntimeContext):
        return self.graphs[resolve_agent(context.security.agent_role)]

    @staticmethod
    def _check_requested_agent(state: WorkforceAgentState, context: OrchestrationRuntimeContext) -> None:
        # The state's requested agent is caller data; the runtime role is the trusted one.
        requested = state.get("requested_agent")
        if requested and resolve_agent(requested) != resolve_agent(context.security.agent_role):
            raise PermissionError("Requested agent does not match the trusted runtime role")

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

    def invoke(
        self,
        state: WorkforceAgentState,
        *,
        context: OrchestrationRuntimeContext,
        thread_id: str,
    ) -> dict[str, Any]:
        self._check_requested_agent(state, context)
        graph = self.graph_for(context)
        config = self._config(thread_id)
        if graph.get_state(config).next:
            raise ValueError("This orchestration is suspended and must be resumed before a new run")
        return graph.invoke(state, config=config, context=context)

    def stream(
        self,
        state: WorkforceAgentState,
        *,
        context: OrchestrationRuntimeContext,
        thread_id: str,
    ) -> Iterator[dict[str, Any]]:
        """Stream only public progress phases and the validated final answer.

        Debug graph events are consumed inside the trusted AI service. Node names,
        prompts, model decisions and intermediate state are never emitted.
        """
        self._check_requested_agent(state, context)
        graph = self.graph_for(context)
        config = self._config(thread_id)
        if graph.get_state(config).next:
            raise ValueError("This orchestration is suspended and must be resumed before a new run")

        current_phase: str | None = None
        for event in graph.stream(state, config=config, context=context, stream_mode="debug"):
            if event.get("type") != "task":
                continue
            phase = public_phase(str((event.get("payload") or {}).get("name") or ""))
            if phase and phase != current_phase:
                current_phase = phase
                yield {"event": "status", "phase": phase}

        snapshot = graph.get_state(config)
        result = dict(snapshot.values or {})
        interrupts = tuple(
            interrupt_item
            for task in snapshot.tasks
            for interrupt_item in getattr(task, "interrupts", ())
        )
        if interrupts:
            yield {"event": "status", "phase": "WAITING_APPROVAL"}
        else:
            answer = str(result.get("final_answer") or "")
            for token in re.findall(r"\S+\s*|\s+", answer):
                yield {"event": "token", "delta": token}
            yield {"event": "status", "phase": "COMPLETED"}
        result["__interrupt__"] = interrupts
        yield {"event": "result", "result": result}

    def resume(
        self,
        resume_value: Any,
        *,
        context: OrchestrationRuntimeContext,
        thread_id: str,
    ) -> dict[str, Any]:
        graph = self.graph_for(context)
        config = self._config(thread_id)
        snapshot = graph.get_state(config)
        checkpoint_state = snapshot.values or {}
        security = context.security
        expected_identity = (
            str(security.tenant_id),
            str(security.user_id),
            str(security.conversation_id),
            str(security.workflow_id),
        )
        checkpoint_identity = (
            str(checkpoint_state.get("tenant_id") or ""),
            str(checkpoint_state.get("user_id") or ""),
            str(checkpoint_state.get("conversation_id") or ""),
            str(checkpoint_state.get("workflow_id") or "None"),
        )
        if not snapshot.next:
            raise ValueError("No suspended orchestration exists for this thread")
        if checkpoint_identity != expected_identity:
            raise PermissionError("Checkpoint identity does not match trusted runtime context")
        return graph.invoke(Command(resume=resume_value), config=config, context=context)


def agent_graph_builder(agent: str) -> StateGraph:
    """The uncompiled graph of one agent, for inspection and tests."""
    return GRAPH_BUILDERS[resolve_agent(agent)]()
