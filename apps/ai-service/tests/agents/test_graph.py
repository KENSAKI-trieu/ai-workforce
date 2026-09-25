from __future__ import annotations

import uuid
from collections import deque
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.governance.middleware.context import AgentRuntimeContext
from app.agents.base import notices
from app.agents.base.decision import DeterministicDecisionProvider, GraphDecision
from app.agents.base.nodes import OrchestrationRuntimeContext
from app.agents.registry import LangGraphEngine


class FakeTool:
    def __init__(self, name: str, action: str, result: Any) -> None:
        self.name = name
        self.metadata = {
            "action": action,
            "allowed_roles": ["*"],
            "allowed_departments": ["*"],
        }
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def invoke(self, payload: dict[str, Any]) -> Any:
        self.calls.append(payload)
        return self.result


class SequenceDecisionProvider:
    def __init__(self, *decisions: GraphDecision) -> None:
        self.decisions = deque(decisions)

    def decide(self, state):
        return self.decisions.popleft()


def _security(
    *,
    allowed_tools: set[str],
    role: str = "Employee",
    department: str = "HR",
    workflow_id: uuid.UUID | None = None,
    agent_role: str = "HR",
):
    return AgentRuntimeContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role=role,
        department=department,
        agent_role=agent_role,
        correlation_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        workflow_id=workflow_id,
        allowed_tools=frozenset(allowed_tools),
    )


def _state(security: AgentRuntimeContext, message: str, *, requested_agent: str | None = None):
    return {
        "tenant_id": str(security.tenant_id),
        "user_id": str(security.user_id),
        "role": security.role,
        "department": security.department,
        "conversation_id": str(security.conversation_id),
        "workflow_id": str(security.workflow_id) if security.workflow_id else None,
        "messages": [{"role": "user", "content": message}],
        "intent": "",
        "selected_agent": "",
        "retrieved_context": [],
        "tool_calls": [],
        "citations": [],
        "approval_id": None,
        "final_answer": None,
        "errors": [],
        "requested_agent": requested_agent,
    }


def test_graph_routes_through_knowledge_subgraph_and_verifies_citation() -> None:
    security = _security(allowed_tools={"rag_search"}, department="ALL", agent_role="KNOWLEDGE")
    rag = FakeTool("rag_search", "READ_ONLY", [{
        "id": "chunk-1",
        "document_id": "policy-1",
        "document_title": "Leave Policy",
        "content": "Employees receive 12 annual leave days. Contact hr@example.com.",
    }])
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=DeterministicDecisionProvider(),
        tools={"rag_search": rag},
    )
    result = LangGraphEngine().invoke(
        _state(security, "How many leave days?", requested_agent="KNOWLEDGE"),
        context=context,
        thread_id="knowledge-thread",
    )
    assert result["is_complete"] is True
    assert result["selected_agent"] == "KNOWLEDGE"
    assert "[Citation: Leave Policy]" in result["final_answer"]
    assert "hr@example.com" not in result["final_answer"]
    assert "[REDACTED_EMAIL]" in result["final_answer"]
    assert result["errors"] == []
    trace_nodes = [item["node"] for item in result["execution_trace"]]
    assert "knowledge_policy" in trace_nodes
    assert "knowledge_tool_scope" in trace_nodes
    assert trace_nodes[-3:] == ["output_validation", "citation_verification", "response"]


def test_stream_exposes_only_public_phases_and_validated_answer_tokens() -> None:
    security = _security(allowed_tools=set(), department="ALL", agent_role="KNOWLEDGE")
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=DeterministicDecisionProvider(),
        tools={},
    )
    events = list(LangGraphEngine().stream(
        _state(security, "Private user request", requested_agent="KNOWLEDGE"),
        context=context,
        thread_id="stream-thread",
    ))

    public_events = [item for item in events if item["event"] != "result"]
    assert {item["event"] for item in public_events} <= {"status", "token"}
    assert all("node" not in item and "prompt" not in item for item in public_events)
    assert any(item == {"event": "status", "phase": "SEARCHING"} for item in events)
    assert events[-2] == {"event": "status", "phase": "COMPLETED"}
    answer = events[-1]["result"]["final_answer"]
    assert "".join(item["delta"] for item in events if item["event"] == "token") == answer


def test_read_only_tool_executes_then_returns_to_model_decision() -> None:
    security = _security(allowed_tools={"rag_search", "leave_lookup"})
    rag = FakeTool("rag_search", "READ_ONLY", [])
    leave = FakeTool("leave_lookup", "READ_ONLY", {"remaining_days": 7})
    decisions = SequenceDecisionProvider(
        GraphDecision(tool_name="leave_lookup", reason="Read authoritative balance."),
        GraphDecision(final_answer="You have 7 leave days remaining.", reason="Synthesize tool result."),
    )
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=decisions,
        tools={"rag_search": rag, "leave_lookup": leave},
    )
    result = LangGraphEngine().invoke(
        _state(security, "Check my leave balance", requested_agent="HR"),
        context=context,
        thread_id="read-thread",
    )
    assert result["final_answer"] == "You have 7 leave days remaining."
    assert len(leave.calls) == 1
    assert leave.calls[0]["tenant_id"] == str(security.tenant_id)
    assert leave.calls[0]["audit"]["correlation_id"] == str(security.correlation_id)
    assert result["tool_calls"][0]["action"] == "READ_ONLY"
    assert result["model_iterations"] == 2


def test_action_tool_interrupts_and_executes_only_after_approval() -> None:
    security = _security(allowed_tools={"rag_search", "create_task"})
    rag = FakeTool("rag_search", "READ_ONLY", [])
    task = FakeTool("create_task", "WRITE", {"task_id": "task-1"})
    decision = SequenceDecisionProvider(GraphDecision(
        tool_name="create_task",
        tool_args={"title": "Prepare report"},
        reason="User explicitly requested a task.",
    ))
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=decision,
        tools={"rag_search": rag, "create_task": task},
    )
    engine = LangGraphEngine()
    first = engine.invoke(
        _state(security, "Create a report task", requested_agent="HR"),
        context=context,
        thread_id="action-thread",
    )
    assert "__interrupt__" in first
    assert first["__interrupt__"][0].value["tool_name"] == "create_task"
    assert task.calls == []

    final = engine.resume(
        {"approved": True, "approval_id": "approval-123"},
        context=context,
        thread_id="action-thread",
    )
    assert final["approval_id"] == "approval-123"
    assert final["is_complete"] is True
    assert len(task.calls) == 1
    assert task.calls[0]["audit"]["idempotency_key"].startswith("graph:")
    assert final["tool_calls"][0]["status"] == "SUCCESS"


def test_approval_is_registered_idempotently_before_interrupt() -> None:
    workflow_id = uuid.uuid4()
    security = _security(
        allowed_tools={"create_task"},
        workflow_id=workflow_id,
    )
    task = FakeTool("create_task", "WRITE", {"task_id": "task-registered"})
    registrations: list[dict[str, Any]] = []

    def register(payload: dict[str, Any]) -> dict[str, Any]:
        registrations.append(payload)
        return {"approval_id": "approval-persisted"}

    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(GraphDecision(
            tool_name="create_task",
            tool_args={"title": "Persist first"},
            reason="Approval must exist before pause.",
        )),
        tools={"create_task": task},
        approval_registrar=register,
    )
    engine = LangGraphEngine()
    first = engine.invoke(
        _state(security, "Create task", requested_agent="HR"),
        context=context,
        thread_id=str(security.conversation_id),
    )
    assert registrations[0]["workflow_id"] == str(workflow_id)
    assert first["__interrupt__"][0].value["approval_id"] == "approval-persisted"
    assert task.calls == []

    engine.resume(
        {"approved": True, "approval_id": "approval-persisted"},
        context=context,
        thread_id=str(security.conversation_id),
    )
    assert len(registrations) == 2
    assert registrations[0]["interrupt_id"] == registrations[1]["interrupt_id"]
    assert len(task.calls) == 1


def test_separate_actions_in_one_thread_get_distinct_approval_ids() -> None:
    workflow_id = uuid.uuid4()
    security = _security(allowed_tools={"create_task"}, workflow_id=workflow_id)
    task = FakeTool("create_task", "WRITE", {"task_id": "task"})
    registrations: list[dict[str, Any]] = []

    def register(payload: dict[str, Any]) -> dict[str, Any]:
        registrations.append(payload)
        return {"approval_id": f"approval-{len(registrations)}"}

    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(
            GraphDecision(
                tool_name="create_task",
                tool_args={"title": "First"},
                reason="Create the first requested task.",
            ),
            GraphDecision(
                tool_name="create_task",
                tool_args={"title": "Second"},
                reason="Create the second requested task.",
            ),
        ),
        tools={"create_task": task},
        approval_registrar=register,
    )
    engine = LangGraphEngine()
    thread_id = str(security.conversation_id)

    engine.invoke(
        _state(security, "Create first task", requested_agent="HR"),
        context=context,
        thread_id=thread_id,
    )
    first_interrupt_id = registrations[0]["interrupt_id"]
    engine.resume(
        {"approved": True, "approval_id": "approval-1"},
        context=context,
        thread_id=thread_id,
    )

    engine.invoke(
        _state(security, "Create second task", requested_agent="HR"),
        context=context,
        thread_id=thread_id,
    )
    second_interrupt_id = registrations[-1]["interrupt_id"]

    assert first_interrupt_id != second_interrupt_id


def test_action_rejection_does_not_execute_tool() -> None:
    security = _security(allowed_tools={"rag_search", "create_task"})
    rag = FakeTool("rag_search", "READ_ONLY", [])
    task = FakeTool("create_task", "WRITE", {"task_id": "never"})
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(GraphDecision(
            tool_name="create_task", tool_args={"title": "No"}, reason="Request action."
        )),
        tools={"rag_search": rag, "create_task": task},
    )
    engine = LangGraphEngine()
    engine.invoke(_state(security, "Create task", requested_agent="HR"), context=context, thread_id="reject-thread")
    final = engine.resume(False, context=context, thread_id="reject-thread")
    assert task.calls == []
    assert final["tool_calls"][0]["status"] == "REJECTED"
    assert final["final_answer"] == notices.ACTION_REJECTED


def test_graph_rejects_state_identity_not_bound_to_runtime() -> None:
    security = _security(allowed_tools=set())
    state = _state(security, "hello")
    state["tenant_id"] = str(uuid.uuid4())
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=DeterministicDecisionProvider(),
        tools={},
    )
    try:
        LangGraphEngine().invoke(state, context=context, thread_id="bad-identity")
    except PermissionError as exc:
        assert "trusted runtime context" in str(exc)
    else:
        raise AssertionError("Cross-tenant graph state was accepted")


def test_resume_rejects_a_different_checkpoint_principal() -> None:
    security = _security(allowed_tools={"rag_search", "create_task"})
    rag = FakeTool("rag_search", "READ_ONLY", [])
    task = FakeTool("create_task", "WRITE", {"task_id": "never"})
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(GraphDecision(
            tool_name="create_task", tool_args={"title": "Protected"}, reason="Request action."
        )),
        tools={"rag_search": rag, "create_task": task},
    )
    engine = LangGraphEngine()
    engine.invoke(_state(security, "Create task", requested_agent="HR"), context=context, thread_id="bound-thread")

    other_security = security.model_copy(update={"user_id": uuid.uuid4()})
    other_context = OrchestrationRuntimeContext(
        security=other_security,
        decision_provider=DeterministicDecisionProvider(),
        tools={"rag_search": rag, "create_task": task},
    )
    with pytest.raises(PermissionError, match="Checkpoint identity"):
        engine.resume(True, context=other_context, thread_id="bound-thread")
    assert task.calls == []


def test_resume_rechecks_current_tool_acl_before_action() -> None:
    security = _security(allowed_tools={"rag_search", "create_task"})
    rag = FakeTool("rag_search", "READ_ONLY", [])
    task = FakeTool("create_task", "WRITE", {"task_id": "never"})
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(GraphDecision(
            tool_name="create_task", tool_args={"title": "Revoked"}, reason="Request action."
        )),
        tools={"rag_search": rag, "create_task": task},
    )
    engine = LangGraphEngine()
    engine.invoke(_state(security, "Create task", requested_agent="HR"), context=context, thread_id="revoked-thread")

    revoked_security = security.model_copy(update={"allowed_tools": frozenset({"rag_search"})})
    revoked_context = OrchestrationRuntimeContext(
        security=revoked_security,
        decision_provider=DeterministicDecisionProvider(),
        tools={"rag_search": rag},
    )
    result = engine.resume(True, context=revoked_context, thread_id="revoked-thread")
    assert task.calls == []
    assert result["tool_calls"][0]["status"] == "DENIED"
    assert result["errors"][-1]["error"] == "TOOL_NOT_ALLOWED"


def test_suspended_thread_cannot_be_overwritten_by_a_new_run() -> None:
    security = _security(allowed_tools={"create_task"})
    task = FakeTool("create_task", "WRITE", {"task_id": "never"})
    context = OrchestrationRuntimeContext(
        security=security,
        decision_provider=SequenceDecisionProvider(GraphDecision(
            tool_name="create_task", tool_args={"title": "Pending"}, reason="Request action."
        )),
        tools={"create_task": task},
    )
    engine = LangGraphEngine()
    state = _state(security, "Create task", requested_agent="HR")
    engine.invoke(state, context=context, thread_id="suspended-thread")

    with pytest.raises(ValueError, match="must be resumed"):
        engine.invoke(state, context=context, thread_id="suspended-thread")
    assert task.calls == []


def test_orchestration_endpoint_requires_tool_jwt_and_returns_state(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.base.runtime.configured_chat_models", lambda **_: [])
    client = TestClient(app)
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "role": "Employee",
        "department": "ALL",
        "conversation_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "message": "What can you answer?",
        "requested_agent": "KNOWLEDGE",
        "allowed_tools": [],
        "denied_tools": [],
    }
    assert client.post("/v1/orchestration/run", json=payload).status_code == 401

    response = client.post(
        "/v1/orchestration/run",
        json=payload,
        headers={"X-Internal-Tool-Authorization": "Bearer internal-tool-jwt"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["thread_id"] == payload["conversation_id"]
    assert body["state"]["selected_agent"] == "KNOWLEDGE"


def _review_context(security, review_result, *decisions):
    review = FakeTool("audit_contract_risk", "READ_ONLY", review_result)
    review.metadata["terminal"] = True
    decider = SequenceDecisionProvider(*decisions)
    return review, decider, OrchestrationRuntimeContext(
        security=security, decision_provider=decider, tools={"audit_contract_risk": review},
    )


def test_a_terminal_tool_answers_the_user_and_ends_the_turn() -> None:
    """The model used to get the review back, re-call the tool and open approvals."""
    security = _security(allowed_tools={"audit_contract_risk"}, department="LEGAL", agent_role="LEGAL")
    review, decider, context = _review_context(
        security,
        {"status": "REVIEWED", "review_id": "r-1", "reply": "Tôi đã rà soát nội dung hợp đồng."},
        GraphDecision(tool_name="audit_contract_risk", tool_args={}, reason="review it"),
        # Never reached: the turn ends on the tool's reply.
        GraphDecision(tool_name="audit_contract_risk", tool_args={}, reason="again"),
    )

    result = LangGraphEngine().invoke(
        _state(security, "HỢP ĐỒNG ... rà soát giúp", requested_agent="LEGAL"),
        context=context,
        thread_id="legal-terminal-thread",
    )

    assert result["final_answer"] == "Tôi đã rà soát nội dung hợp đồng."
    assert len(review.calls) == 1
    assert len(decider.decisions) == 1
    assert result["tool_calls"][-1]["result"]["review_id"] == "r-1"
    assert result["errors"] == []


def test_a_terminal_tool_without_a_reply_hands_back_to_the_model() -> None:
    security = _security(allowed_tools={"audit_contract_risk"}, department="LEGAL", agent_role="LEGAL")
    review, decider, context = _review_context(
        security,
        {"status": "REVIEWED"},
        GraphDecision(tool_name="audit_contract_risk", tool_args={}, reason="review it"),
        GraphDecision(final_answer="Đã rà soát.", reason="summarise"),
    )

    result = LangGraphEngine().invoke(
        _state(security, "rà soát giúp", requested_agent="LEGAL"),
        context=context,
        thread_id="legal-terminal-fallback-thread",
    )

    assert result["final_answer"] == "Đã rà soát."
    assert not decider.decisions
