"""The nodes every agent graph is built from.

Each function is one step of a governed turn: guard the input, retrieve context, let the
model decide, run a tool (or stop for approval), validate the output and verify its
citations. ``agents/base/graph.py`` wires them into one graph per agent.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agents.base import notices
from app.agents.base.decision import DecisionProvider
from app.agents.base.state import WorkforceAgentState
from app.governance.guardrails import is_tool_allowed, validate_grounded_output, validate_input
from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.redaction import redact_sensitive_data, redact_text

CITATION_PATTERN = re.compile(r"\[Citation:\s*([^\]]+)\]", re.IGNORECASE)
CHUNK_REFERENCE_PATTERN = re.compile(r";?\s*chunk\s*=\s*([^\s;,\]]+)", re.IGNORECASE)


@dataclass(frozen=True)
class OrchestrationRuntimeContext:
    security: AgentRuntimeContext
    decision_provider: DecisionProvider
    tools: dict[str, BaseTool]
    approval_registrar: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    max_model_iterations: int = 4
    # Tools the agent's role has but the organisation turned off, name -> label. Never
    # bound or run; the model may only name one so the graph can say it is off.
    disabled_tools: dict[str, str] = field(default_factory=dict)


def _latest_user_text(state: WorkforceAgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if str(message.get("role", "")).lower() in {"user", "human"}:
            return str(message.get("content") or "")
    return ""


def _trace(state: WorkforceAgentState, node: str, status: str = "COMPLETED") -> list[dict[str, Any]]:
    return [*(state.get("execution_trace") or []), {"node": node, "status": status}]


def input_guard(state: WorkforceAgentState, runtime: Runtime[OrchestrationRuntimeContext]) -> dict[str, Any]:
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


def retrieve_context(
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


def model_decision(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    iteration = int(state.get("model_iterations") or 0) + 1
    if iteration > runtime.context.max_model_iterations:
        return {
            "pending_tool_call": None,
            "final_answer": notices.ITERATION_LIMIT,
            "errors": [*(state.get("errors") or []), {"node": "model_decision", "error": "MODEL_ITERATION_LIMIT"}],
            "model_iterations": iteration,
            "execution_trace": _trace(state, "model_decision", "LIMITED"),
        }
    try:
        decision = runtime.context.decision_provider.decide(state)
    except Exception as exc:
        return {
            "pending_tool_call": None,
            "final_answer": notices.DECISION_FAILED,
            "errors": [*(state.get("errors") or []), {"node": "model_decision", "error": type(exc).__name__}],
            "model_iterations": iteration,
            "execution_trace": _trace(state, "model_decision", "FAILED"),
        }
    pending = None
    if decision.tool_name:
        name = decision.tool_name
        disabled_label = runtime.context.disabled_tools.get(name)
        if disabled_label is not None and name not in runtime.context.tools:
            # Recorded under its own node, not model_decision: the model did its job, so
            # the backend must not treat this as an outage and rerun the turn through the
            # deterministic flow, which would refuse again in its own words.
            return {
                "pending_tool_call": None,
                "final_answer": notices.tool_disabled(disabled_label),
                "citations": [],
                "errors": [*(state.get("errors") or []), {"node": "tool_policy", "error": "TOOL_DISABLED_BY_TENANT", "tool": name}],
                "model_iterations": iteration,
                "execution_trace": _trace(state, "model_decision", "DENIED"),
            }
        if name not in set(state.get("available_tools") or []) or name not in runtime.context.tools:
            return {
                "pending_tool_call": None,
                "final_answer": notices.TOOL_NOT_AVAILABLE,
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


def after_decision(state: WorkforceAgentState) -> str:
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
            "final_answer": notices.ACTION_REJECTED,
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
            "final_answer": notices.AUTHORIZATION_EXPIRED,
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
            "final_answer": notices.TOOL_FAILED,
            "tool_calls": [
                *(state.get("tool_calls") or []),
                {**pending, "args": payload, "status": "FAILED", "result": None},
            ],
            "errors": [*(state.get("errors") or []), {"node": "tool_execution", "tool": name, "error": type(exc).__name__}],
        }


def after_read_tool(state: WorkforceAgentState) -> str:
    """Back to the model for its next step, unless a terminal tool just answered."""
    calls = state.get("tool_calls") or []
    latest = calls[-1] if calls else {}
    if latest.get("terminal") and latest.get("status") == "SUCCESS" and state.get("final_answer"):
        return "output_validation"
    return "model_decision"


def execute_read_tool(
    state: WorkforceAgentState,
    runtime: Runtime[OrchestrationRuntimeContext],
) -> dict[str, Any]:
    return {**_execute_tool(state, runtime.context, approved=True), "execution_trace": _trace(state, "execute_read_tool")}


def approval_interrupt(
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


def output_validation(state: WorkforceAgentState) -> dict[str, Any]:
    try:
        answer = redact_text(validate_grounded_output(
            str(state.get("final_answer") or ""),
            has_context=bool(state.get("retrieved_context")),
        ))
        return {"final_answer": answer, "execution_trace": _trace(state, "output_validation")}
    except ValueError as exc:
        return {
            "final_answer": notices.OUTPUT_INVALID,
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


def _citation_text(value: Any) -> str:
    text = str(value or "").strip()
    wrapped = CITATION_PATTERN.fullmatch(text)
    return (wrapped.group(1) if wrapped else text).strip().casefold()


def _supplied_citations(state: WorkforceAgentState) -> set[str]:
    text_citations = {value.strip().casefold() for value in CITATION_PATTERN.findall(state.get("final_answer") or "")}
    structured_sources = {
        _citation_text(
            citation.get("source")
            or citation.get("document_title")
            or citation.get("document_id")
            or citation.get("chunk_id")
            or citation.get("citation_tag")
        )
        for citation in state.get("citations") or []
    } - {""}
    return text_citations | structured_sources


def _citation_is_verified(citation: str, context: list[dict[str, Any]]) -> bool:
    """Whether one supplied citation points at a retrieved source.

    Besides a bare title, name or id, a citation may repeat the tag retrieval hands
    the model -- "<document>, v<version>, <section>; chunk=<chunk id>". Its document
    must be retrieved, and a chunk it names must be a retrieved chunk of that same
    document, so a real chunk id cannot vouch for an invented document. The version
    and section in between describe the source; they do not identify it.
    """
    if citation in _allowed_citation_sources(context):
        return True
    chunk = CHUNK_REFERENCE_PATTERN.search(citation)
    document = CHUNK_REFERENCE_PATTERN.sub("", citation).split(",", 1)[0].strip()
    if chunk is None:
        return bool(document) and document in _allowed_citation_sources(context)
    owners = [item for item in context if str(item.get("id") or "").casefold() == chunk.group(1).casefold()]
    if not document:
        return bool(owners)
    return any(document in _allowed_citation_sources([item]) for item in owners)


def _withhold_answer(state: WorkforceAgentState, error: str) -> dict[str, Any]:
    return {
        "final_answer": notices.CITATION_UNVERIFIED,
        "citations": [],
        "errors": [*(state.get("errors") or []), {"node": "citation_verification", "error": error}],
        "execution_trace": _trace(state, "citation_verification", "FAILED"),
    }


def citation_verification(state: WorkforceAgentState) -> dict[str, Any]:
    if state.get("tool_calls") or not state.get("citation_required"):
        return {"execution_trace": _trace(state, "citation_verification", "SKIPPED")}
    if any(item.get("node") in {"model_decision", "tool_policy"} for item in state.get("errors") or []):
        # The answer is the engine's own notice, not model output, so there is nothing to
        # verify. Checking it anyway replaced the real cause -- a provider outage, a denied
        # or disabled tool -- with "citations could not be verified".
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
    if not supplied or not all(_citation_is_verified(citation, context) for citation in supplied):
        return _withhold_answer(state, "UNVERIFIED_CITATION")
    return {"execution_trace": _trace(state, "citation_verification")}


def response(state: WorkforceAgentState) -> dict[str, Any]:
    messages = [
        *(state.get("messages") or []),
        {"role": "assistant", "content": state.get("final_answer") or ""},
    ]
    return {"messages": messages, "is_complete": True, "execution_trace": _trace(state, "response")}
