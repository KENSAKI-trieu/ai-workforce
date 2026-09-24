"""Real LangGraph parent graph with governed business subgraphs and interrupts."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.guardrails.input_guard import validate_input
from app.guardrails.tool_permission import is_tool_allowed
from app.guardrails.output_guard import validate_grounded_output
from app.middleware.context import AgentRuntimeContext
from app.middleware.redaction import redact_sensitive_data, redact_text
from app.orchestration.decision import DecisionProvider
from app.orchestration.state import WorkforceAgentState
from app.orchestration.subgraphs import build_business_subgraphs


AGENTS = {"LEGAL", "HR", "FINANCE", "CUSTOMER_SUPPORT", "KNOWLEDGE", "CEO"}
REQUESTED_AGENT_ALIASES = {
    "IT": "CUSTOMER_SUPPORT",
    "SALES": "CUSTOMER_SUPPORT",
}
CITATION_PATTERN = re.compile(r"\[Citation:\s*([^\]]+)\]", re.IGNORECASE)

PUBLIC_PHASE_BY_NODE = {
    "input_guard": "ANALYZING",
    "intent_router": "ANALYZING",
    "agent_selector": "ANALYZING",
    "legal": "ANALYZING",
    "hr": "ANALYZING",
    "finance": "ANALYZING",
    "customer_support": "ANALYZING",
    "knowledge": "ANALYZING",
    "ceo": "ANALYZING",
    "retrieve_context": "SEARCHING",
    "model_decision": "ANALYZING",
    "execute_read_tool": "TOOL_CALLING",
    "approval_interrupt": "TOOL_CALLING",
    "output_validation": "ANALYZING",
    "citation_verification": "ANALYZING",
}


@dataclass(frozen=True)
class OrchestrationRuntimeContext:
    security: AgentRuntimeContext
    decision_provider: DecisionProvider
    tools: dict[str, BaseTool]
    approval_registrar: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    max_model_iterations: int = 4


def _latest_user_text(state: WorkforceAgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if str(message.get("role", "")).lower() in {"user", "human"}:
            return str(message.get("content") or "")
    return ""


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return " ".join(
        "".join(character for character in value if unicodedata.category(character) != "Mn").split()
    )


def _trace(state: WorkforceAgentState, node: str, status: str = "COMPLETED") -> list[dict[str, Any]]:
    return [*(state.get("execution_trace") or []), {"node": node, "status": status}]


def _input_guard(state: WorkforceAgentState, runtime: Runtime[OrchestrationRuntimeContext]) -> dict[str, Any]:
    security = runtime.context.security
    if state.get("tenant_id") != str(security.tenant_id) or state.get("user_id") != str(security.user_id):
        raise PermissionError("Graph state identity does not match trusted runtime context")
    if state.get("workflow_id") != (
        str(security.workflow_id) if security.workflow_id else None
    ):
        raise PermissionError("Graph workflow does not match trusted runtime context")
    text = redact_text(validate_input(_latest_user_text(state)))
    messages = list(state.get("messages") or [])
    if messages:
        messages[-1] = {**messages[-1], "content": text}
    return {
        "messages": messages,
        "errors": list(state.get("errors") or []),
        "retrieved_context": list(state.get("retrieved_context") or []),
        "tool_calls": list(state.get("tool_calls") or []),
        "citations": list(state.get("citations") or []),
        "available_tools": sorted(security.allowed_tools - security.denied_tools),
        "model_iterations": 0,
        "execution_trace": _trace(state, "input_guard"),
    }


def _intent_router(state: WorkforceAgentState) -> dict[str, Any]:
    requested = str(state.get("requested_agent") or "").upper()
    requested = REQUESTED_AGENT_ALIASES.get(requested, requested)
    if requested in AGENTS:
        intent = f"{requested}_REQUEST"
    else:
        text = _normalized(_latest_user_text(state))
        rules = (
            ("CEO_ORCHESTRATION", ("ceo", "dieu phoi", "chien luoc", "cross department")),
            ("LEGAL_REVIEW", ("hop dong", "phap ly", "nda", "legal", "contract")),
            ("HR_QUERY", ("nghi phep", "nhan su", "luong", "onboarding", "employee")),
            ("FINANCE_QUERY", ("chi phi", "ngan sach", "hoa don", "cost", "finance", "invoice")),
            ("SUPPORT_REQUEST", ("khach hang", "ho tro", "ticket", "support", "complaint")),
        )
        intent = next(
            (candidate for candidate, markers in rules if any(marker in text for marker in markers)),
            "KNOWLEDGE_QUERY",
        )
    return {"intent": intent, "execution_trace": _trace(state, "intent_router")}


def _agent_selector(state: WorkforceAgentState) -> dict[str, Any]:
    intent = state.get("intent", "KNOWLEDGE_QUERY")
    mapping = {
        "CEO_ORCHESTRATION": "CEO",
        "LEGAL_REVIEW": "LEGAL",
        "HR_QUERY": "HR",
        "FINANCE_QUERY": "FINANCE",
        "SUPPORT_REQUEST": "CUSTOMER_SUPPORT",
        "KNOWLEDGE_QUERY": "KNOWLEDGE",
    }
    requested = str(state.get("requested_agent") or "").upper()
    requested = REQUESTED_AGENT_ALIASES.get(requested, requested)
    selected = requested if requested in AGENTS else mapping.get(intent, "KNOWLEDGE")
    return {"selected_agent": selected, "execution_trace": _trace(state, "agent_selector")}


def _selected_subgraph(state: WorkforceAgentState) -> str:
    return str(state.get("selected_agent") or "KNOWLEDGE").lower()


def _tool_payload(
    state: WorkforceAgentState,
    runtime: OrchestrationRuntimeContext,
    name: str,
    arguments: dict[str, Any],
    call_id: str,
) -> dict[str, Any]:
    security = runtime.security
    payload = dict(arguments)
    payload["tenant_id"] = str(security.tenant_id)
    audit: dict[str, Any] = {
        "correlation_id": str(security.correlation_id),
        "conversation_id": str(security.conversation_id) if security.conversation_id else None,
        "workflow_id": str(security.workflow_id) if security.workflow_id else None,
    }
    tool = runtime.tools[name]
    if str((tool.metadata or {}).get("action", "READ_ONLY")) != "READ_ONLY":
        audit["idempotency_key"] = f"graph:{security.correlation_id}:{call_id}"[:200]
    payload["audit"] = {key: value for key, value in audit.items() if value is not None}
    return payload


def _retrieve_context(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    if "rag_search" not in set(state.get("available_tools") or []) or "rag_search" not in runtime.context.tools:
        return {"retrieved_context": [], "execution_trace": _trace(state, "retrieve_context", "SKIPPED")}
    call_id = f"retrieve-{state['conversation_id']}"
    payload = _tool_payload(
        state,
        runtime.context,
        "rag_search",
        {"query": _latest_user_text(state), "top_k": 5},
        call_id,
    )
    try:
        result = redact_sensitive_data(runtime.context.tools["rag_search"].invoke(payload))
        context = result if isinstance(result, list) else []
        return {"retrieved_context": context, "execution_trace": _trace(state, "retrieve_context")}
    except Exception as exc:
        return {
            "retrieved_context": [],
            "errors": [*(state.get("errors") or []), {"node": "retrieve_context", "error": type(exc).__name__}],
            "execution_trace": _trace(state, "retrieve_context", "FAILED"),
        }


def _model_decision(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    iteration = int(state.get("model_iterations") or 0) + 1
    if iteration > runtime.context.max_model_iterations:
        return {
            "pending_tool_call": None,
            "final_answer": "The orchestration limit was reached before a safe answer was produced.",
            "errors": [*(state.get("errors") or []), {"node": "model_decision", "error": "MODEL_ITERATION_LIMIT"}],
            "model_iterations": iteration,
            "execution_trace": _trace(state, "model_decision", "LIMITED"),
        }
    try:
        decision = runtime.context.decision_provider.decide(state)
    except Exception as exc:
        return {
            "pending_tool_call": None,
            "final_answer": "The decision model could not produce a validated result.",
            "errors": [*(state.get("errors") or []), {"node": "model_decision", "error": type(exc).__name__}],
            "model_iterations": iteration,
            "execution_trace": _trace(state, "model_decision", "FAILED"),
        }
    pending = None
    if decision.tool_name:
        name = decision.tool_name
        if name not in set(state.get("available_tools") or []) or name not in runtime.context.tools:
            return {
                "pending_tool_call": None,
                "final_answer": "The requested tool is not available in this governed context.",
                "errors": [*(state.get("errors") or []), {"node": "model_decision", "error": "TOOL_NOT_ALLOWED", "tool": name}],
                "model_iterations": iteration,
                "execution_trace": _trace(state, "model_decision", "DENIED"),
            }
        # Generated here so it is checkpointed before an approval node starts.
        # The UUID remains stable when that node restarts, while separate user
        # turns in the same conversation cannot collide with an older approval.
        call_id = str(uuid.uuid4())
        tool = runtime.context.tools[name]
        pending = {
            "id": call_id,
            "name": name,
            "args": redact_sensitive_data(decision.tool_args),
            "action": str((tool.metadata or {}).get("action", "READ_ONLY")),
            "terminal": bool((tool.metadata or {}).get("terminal")),
            "reason": decision.reason,
        }
    return {
        "pending_tool_call": pending,
        "final_answer": decision.final_answer,
        "citations": decision.citations or state.get("citations") or [],
        "model_iterations": iteration,
        "execution_trace": _trace(state, "model_decision"),
    }


def _after_decision(state: WorkforceAgentState) -> str:
    pending = state.get("pending_tool_call")
    if not pending:
        return "output_validation"
    return "execute_read_tool" if pending.get("action") == "READ_ONLY" else "approval_interrupt"


def _execute_tool(
    state: WorkforceAgentState,
    runtime: OrchestrationRuntimeContext,
    *,
    approved: bool,
) -> dict[str, Any]:
    pending = state.get("pending_tool_call") or {}
    name = str(pending.get("name") or "")
    call_id = str(pending.get("id") or uuid.uuid4())
    if not approved:
        return {
            "pending_tool_call": None,
            "final_answer": "The requested action was rejected by the human approver.",
            "tool_calls": [
                *(state.get("tool_calls") or []),
                {**pending, "status": "REJECTED", "result": None},
            ],
        }
    permitted = is_tool_allowed(
        name, runtime.security.allowed_tools, runtime.security.denied_tools
    )
    if not permitted or name not in runtime.tools:
        return {
            "pending_tool_call": None,
            "final_answer": "The action was not executed because its authorization is no longer valid.",
            "tool_calls": [
                *(state.get("tool_calls") or []),
                {**pending, "status": "DENIED", "result": None},
            ],
            "errors": [
                *(state.get("errors") or []),
                {"node": "tool_execution", "tool": name, "error": "TOOL_NOT_ALLOWED"},
            ],
        }
    payload = _tool_payload(state, runtime, name, dict(pending.get("args") or {}), call_id)
    try:
        result = redact_sensitive_data(runtime.tools[name].invoke(payload))
        if pending.get("terminal") and isinstance(result, dict) and result.get("reply"):
            final_answer = str(result["reply"])
        elif pending.get("action") != "READ_ONLY":
            final_answer = json.dumps(result, ensure_ascii=False, default=str)
        else:
            final_answer = None
        return {
            "pending_tool_call": None,
            "tool_calls": [
                *(state.get("tool_calls") or []),
                {**pending, "args": payload, "status": "SUCCESS", "result": result},
            ],
            "final_answer": final_answer,
        }
    except Exception as exc:
        return {
            "pending_tool_call": None,
            "final_answer": "The governed tool failed and no action result was accepted.",
            "tool_calls": [
                *(state.get("tool_calls") or []),
                {**pending, "args": payload, "status": "FAILED", "result": None},
            ],
            "errors": [*(state.get("errors") or []), {"node": "tool_execution", "tool": name, "error": type(exc).__name__}],
        }


def _after_read_tool(state: WorkforceAgentState) -> str:
    """Back to the model for its next step, unless a terminal tool just answered."""
    calls = state.get("tool_calls") or []
    latest = calls[-1] if calls else {}
    if latest.get("terminal") and latest.get("status") == "SUCCESS" and state.get("final_answer"):
        return "output_validation"
    return "model_decision"


def _execute_read_tool(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    return {**_execute_tool(state, runtime.context, approved=True), "execution_trace": _trace(state, "execute_read_tool")}


def _approval_interrupt(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    pending = state.get("pending_tool_call") or {}
    interrupt_payload = {
        "type": "ACTION_TOOL_APPROVAL",
        "conversation_id": state["conversation_id"],
        "workflow_id": state.get("workflow_id"),
        "tool_call_id": pending.get("id"),
        "tool_name": pending.get("name"),
        "action": pending.get("action"),
        "reason": pending.get("reason"),
        "arguments": pending.get("args"),
    }
    if runtime.context.approval_registrar is not None:
        security = runtime.context.security
        registration = runtime.context.approval_registrar({
            "tenant_id": str(security.tenant_id),
            "workflow_id": str(security.workflow_id),
            "conversation_id": state["conversation_id"],
            "interrupt_id": str(pending.get("id")),
            "agent_role": security.agent_role,
            "tool_name": str(pending.get("name")),
            "action": str(pending.get("action")),
            "reason": pending.get("reason"),
            "arguments": pending.get("args") or {},
        })
        interrupt_payload["approval_id"] = registration.get("approval_id")
    review = interrupt(interrupt_payload)
    approved = bool(review.get("approved")) if isinstance(review, dict) else bool(review)
    approval_id = (
        str(review.get("approval_id"))
        if isinstance(review, dict) and review.get("approval_id")
        else f"graph-approval:{pending.get('id')}"
    )
    return {
        **_execute_tool(state, runtime.context, approved=approved),
        "approval_id": approval_id,
        "execution_trace": _trace(state, "approval_interrupt", "APPROVED" if approved else "REJECTED"),
    }


def _output_validation(state: WorkforceAgentState) -> dict[str, Any]:
    try:
        answer = redact_text(validate_grounded_output(
            str(state.get("final_answer") or ""),
            has_context=bool(state.get("retrieved_context")),
        ))
        return {"final_answer": answer, "execution_trace": _trace(state, "output_validation")}
    except ValueError as exc:
        return {
            "final_answer": "A validated answer could not be produced.",
            "errors": [*(state.get("errors") or []), {"node": "output_validation", "error": str(exc)}],
            "execution_trace": _trace(state, "output_validation", "FAILED"),
        }


def _allowed_citation_sources(context: list[dict[str, Any]]) -> set[str]:
    return {
        str(value).casefold()
        for item in context
        for value in (
            item.get("document_title"), item.get("document_name"), item.get("document_id"), item.get("id")
        )
        if value
    }


def _supplied_citations(state: WorkforceAgentState) -> set[str]:
    text_citations = {value.strip().casefold() for value in CITATION_PATTERN.findall(state.get("final_answer") or "")}
    structured_sources = {
        str(citation.get("source") or citation.get("document_title") or citation.get("document_id") or citation.get("chunk_id") or "").casefold()
        for citation in state.get("citations") or []
    } - {""}
    return text_citations | structured_sources


def _withhold_answer(state: WorkforceAgentState, error: str) -> dict[str, Any]:
    return {
        "final_answer": "The answer was withheld because its citations could not be verified.",
        "citations": [],
        "errors": [*(state.get("errors") or []), {"node": "citation_verification", "error": error}],
        "execution_trace": _trace(state, "citation_verification", "FAILED"),
    }


def _citation_verification(state: WorkforceAgentState) -> dict[str, Any]:
    if state.get("tool_calls") or not state.get("citation_required"):
        return {"execution_trace": _trace(state, "citation_verification", "SKIPPED")}
    context = state.get("retrieved_context") or []
    supplied = _supplied_citations(state)
    if not context:
        # Retrieval found nothing, so there is no source any citation could be checked
        # against. Letting the answer through unread was the safe half of that; letting it
        # through while it still *claims* a source was not -- with an empty allow-set every
        # citation here is invented, and this branch returned before the check that would
        # have caught it. An answer with no citation is still allowed: a governed domain
        # has to be able to say something without pretending it read a document.
        if supplied:
            return _withhold_answer(state, "CITATION_WITHOUT_CONTEXT")
        return {"citations": [], "execution_trace": _trace(state, "citation_verification", "NO_CONTEXT")}
    if not supplied or not supplied.issubset(_allowed_citation_sources(context)):
        return _withhold_answer(state, "UNVERIFIED_CITATION")
    return {"execution_trace": _trace(state, "citation_verification")}


def _response(state: WorkforceAgentState) -> dict[str, Any]:
    messages = [
        *(state.get("messages") or []),
        {"role": "assistant", "content": state.get("final_answer") or ""},
    ]
    return {"messages": messages, "is_complete": True, "execution_trace": _trace(state, "response")}


class LangGraphEngine:
    def __init__(self, *, checkpointer: Any | None = None) -> None:
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build_graph().compile(checkpointer=self.checkpointer)

    @staticmethod
    def _build_graph() -> StateGraph:
        builder = StateGraph(WorkforceAgentState, context_schema=OrchestrationRuntimeContext)
        builder.add_node("input_guard", _input_guard)
        builder.add_node("intent_router", _intent_router)
        builder.add_node("agent_selector", _agent_selector)
        subgraphs = build_business_subgraphs()
        for name, subgraph in subgraphs.items():
            builder.add_node(name.lower(), subgraph)
        builder.add_node("retrieve_context", _retrieve_context)
        builder.add_node("model_decision", _model_decision)
        builder.add_node("execute_read_tool", _execute_read_tool)
        builder.add_node("approval_interrupt", _approval_interrupt)
        builder.add_node("output_validation", _output_validation)
        builder.add_node("citation_verification", _citation_verification)
        builder.add_node("response", _response)

        builder.add_edge(START, "input_guard")
        builder.add_edge("input_guard", "intent_router")
        builder.add_edge("intent_router", "agent_selector")
        builder.add_conditional_edges(
            "agent_selector",
            _selected_subgraph,
            {name.lower(): name.lower() for name in AGENTS},
        )
        for name in AGENTS:
            builder.add_edge(name.lower(), "retrieve_context")
        builder.add_edge("retrieve_context", "model_decision")
        builder.add_conditional_edges(
            "model_decision",
            _after_decision,
            {
                "execute_read_tool": "execute_read_tool",
                "approval_interrupt": "approval_interrupt",
                "output_validation": "output_validation",
            },
        )
        builder.add_conditional_edges(
            "execute_read_tool",
            _after_read_tool,
            {"model_decision": "model_decision", "output_validation": "output_validation"},
        )
        builder.add_edge("approval_interrupt", "output_validation")
        builder.add_edge("output_validation", "citation_verification")
        builder.add_edge("citation_verification", "response")
        builder.add_edge("response", END)
        return builder

    def invoke(
        self,
        state: WorkforceAgentState,
        *,
        context: OrchestrationRuntimeContext,
        thread_id: str,
    ) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        snapshot = self.graph.get_state(config)
        if snapshot.next:
            raise ValueError("This orchestration is suspended and must be resumed before a new run")
        return self.graph.invoke(
            state,
            config=config,
            context=context,
        )

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
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        snapshot = self.graph.get_state(config)
        if snapshot.next:
            raise ValueError("This orchestration is suspended and must be resumed before a new run")

        current_phase: str | None = None
        for event in self.graph.stream(
            state,
            config=config,
            context=context,
            stream_mode="debug",
        ):
            if event.get("type") != "task":
                continue
            node_name = str((event.get("payload") or {}).get("name") or "")
            phase = PUBLIC_PHASE_BY_NODE.get(node_name)
            if phase and phase != current_phase:
                current_phase = phase
                yield {"event": "status", "phase": phase}

        snapshot = self.graph.get_state(config)
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
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        snapshot = self.graph.get_state(config)
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
        return self.graph.invoke(
            Command(resume=resume_value),
            config=config,
            context=context,
        )
