from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.api.v1.approvals import _can_approve
from app.core.security import create_internal_tool_token
from app.models.models import AIAgent, AgentWorkflow, User, WorkflowApproval
from app.services.agents.langgraph_engine import LangGraphEngine
from app.services.langgraph_approvals import (
    GRAPH_APPROVAL_KIND,
    GRAPH_WORKFLOW_KIND,
    _should_notify_approver,
)


def _create_workflow(db, user: User, agent: AIAgent, conversation_id: uuid.UUID) -> AgentWorkflow:
    workflow = AgentWorkflow(
        tenant_id=user.tenant_id,
        initiator_id=user.id,
        title="LangGraph HITL test",
        status="IN_PROGRESS",
        current_step=0,
        thread_id=str(conversation_id),
        dag_plan={
            "kind": GRAPH_WORKFLOW_KIND,
            "conversation_id": str(conversation_id),
            "agent_role": agent.role_code,
        },
    )
    db.add(workflow)
    db.commit()
    return workflow


def _register(client, db):
    user = db.query(User).filter(User.email == "employee@company.com").one()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id,
        AIAgent.role_code == "HR",
    ).one()
    conversation_id = uuid.uuid4()
    workflow = _create_workflow(db, user, agent, conversation_id)
    interrupt_id = f"{conversation_id}:1:create_task"
    token = create_internal_tool_token(user, agent_role="HR")
    payload = {
        "tenant_id": str(user.tenant_id),
        "workflow_id": str(workflow.id),
        "conversation_id": str(conversation_id),
        "interrupt_id": interrupt_id,
        "agent_role": "HR",
        "tool_name": "create_task",
        "action": "WRITE",
        "reason": "Create a governed task",
        "arguments": {"title": "Approved task"},
    }
    response = client.post(
        "/api/v1/internal/tools/langgraph-approvals",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return user, agent, workflow, payload, response.json()


def test_internal_registration_creates_one_idempotent_approval(client, transactional_db_session) -> None:
    _, _, workflow, payload, first = _register(client, transactional_db_session)
    user = workflow.initiator
    token = create_internal_tool_token(user, agent_role="HR")
    second = client.post(
        "/api/v1/internal/tools/langgraph-approvals",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert second.status_code == 200, second.text
    assert second.json()["approval_id"] == first["approval_id"]
    approval = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.id == uuid.UUID(first["approval_id"])
    ).one()
    assert approval.payload["kind"] == GRAPH_APPROVAL_KIND
    assert approval.workflow_id == workflow.id
    assert approval.status == "WAITING"
    assert transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.langgraph_interrupt_id == payload["interrupt_id"]
    ).count() == 1


def test_backend_run_binds_thread_to_conversation_and_workflow(transactional_db_session) -> None:
    db = transactional_db_session
    user = db.query(User).filter(User.email == "employee@company.com").one()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id,
        AIAgent.role_code == "KNOWLEDGE",
    ).one()
    captured = {}

    class FakeClient:
        def run_orchestration(self, payload, *, internal_tool_jwt):
            captured.update(payload)
            return {
                "thread_id": payload["conversation_id"],
                "status": "COMPLETED",
                "state": {
                    "selected_agent": "KNOWLEDGE",
                    "final_answer": "Done",
                    "citations": [],
                    "tool_calls": [],
                    "errors": [],
                },
                "interrupts": [],
            }

    conversation_id = str(uuid.uuid4())
    response = LangGraphEngine(FakeClient()).execute(
        db=db,
        user=user,
        agent=agent,
        message="Read policy",
        conversation_id=conversation_id,
    )
    workflow = db.query(AgentWorkflow).filter(
        AgentWorkflow.id == uuid.UUID(captured["workflow_id"])
    ).one()
    assert captured["conversation_id"] == conversation_id
    assert workflow.thread_id == conversation_id
    assert workflow.dag_plan["conversation_id"] == conversation_id
    assert response["orchestration"]["workflow_id"] == str(workflow.id)


def test_execution_trace_is_reduced_to_public_audit_phases() -> None:
    result = {
        "status": "COMPLETED",
        "state": {
            "execution_trace": [
                {"node": "input_guard", "status": "COMPLETED", "prompt": "secret"},
                {"node": "model_decision", "status": "COMPLETED", "reason": "private"},
                {"node": "execute_read_tool", "status": "COMPLETED", "args": {"secret": True}},
                {"node": "response", "status": "COMPLETED"},
            ]
        },
    }
    trace = LangGraphEngine.sanitize_execution_trace(result)

    assert trace == [
        {"sequence": 1, "phase": "ANALYZING", "status": "COMPLETED"},
        {"sequence": 2, "phase": "TOOL_CALLING", "status": "COMPLETED"},
        {"sequence": 3, "phase": "COMPLETED", "status": "COMPLETED"},
    ]
    assert all(set(item) == {"sequence", "phase", "status"} for item in trace)


def test_executive_decision_resumes_the_same_graph_thread(
    client,
    ceo_token_headers,
    transactional_db_session,
    monkeypatch,
) -> None:
    _, _, workflow, _, registration = _register(client, transactional_db_session)
    approval_id = registration["approval_id"]
    calls = []

    def fake_resume(self, *, db, approval, approved, reviewer, comments):
        calls.append({
            "thread_id": approval.workflow.thread_id,
            "workflow_id": str(approval.workflow_id),
            "approved": approved,
            "reviewer": reviewer.role,
        })
        approval.resumed_at = datetime.now(timezone.utc)
        approval.resume_error = None
        approval.workflow.status = "COMPLETED"
        db.commit()
        return {"orchestration": {"status": "COMPLETED"}}

    monkeypatch.setattr(LangGraphEngine, "resume_approval", fake_resume)
    response = client.post(
        f"/api/v1/approvals/{approval_id}/action",
        headers=ceo_token_headers,
        json={"action": "APPROVE", "comments": "Approved by policy"},
    )
    assert response.status_code == 200, response.text
    assert calls == [{
        "thread_id": workflow.thread_id,
        "workflow_id": str(workflow.id),
        "approved": True,
        "reviewer": "CEO",
    }]
    assert response.json()["orchestration"]["status"] == "COMPLETED"


def test_manager_policy_requires_reporting_scope(client, transactional_db_session) -> None:
    user, _, _, _, registration = _register(client, transactional_db_session)
    approval = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.id == uuid.UUID(registration["approval_id"])
    ).one()
    manager = transactional_db_session.query(User).filter(User.role == "Manager").first()
    assert manager is not None
    manager.department = user.department
    user.manager_id = manager.id
    transactional_db_session.flush()
    assert _can_approve(transactional_db_session, manager, approval) is True

    user.manager_id = None
    manager.department = "OUT_OF_SCOPE"
    transactional_db_session.flush()
    assert _can_approve(transactional_db_session, manager, approval) is False


def test_manager_notifications_follow_the_same_reporting_scope(
    transactional_db_session,
) -> None:
    db = transactional_db_session
    requester = db.query(User).filter(User.email == "employee@company.com").one()
    manager = db.query(User).filter(User.role == "Manager").first()
    assert manager is not None

    requester.manager_id = manager.id
    manager.department = "OUT_OF_SCOPE"
    db.flush()
    assert _should_notify_approver(manager, requester) is True

    requester.manager_id = None
    manager.department = requester.department
    db.flush()
    assert _should_notify_approver(manager, requester) is True

    manager.department = "OUT_OF_SCOPE"
    db.flush()
    assert _should_notify_approver(manager, requester) is False

    executive = db.query(User).filter(User.role == "CEO").first()
    assert executive is not None
    assert _should_notify_approver(executive, requester) is True


class _NeverCalled:
    def run_orchestration(self, *_args, **_kwargs):
        raise AssertionError("a suspended conversation must not start a new graph run")

    def stream_orchestration(self, *_args, **_kwargs):
        raise AssertionError("a suspended conversation must not start a new graph run")


def _suspended_conversation(db):
    user = db.query(User).filter(User.email == "employee@company.com").one()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()
    conversation_id = uuid.uuid4()
    workflow = _create_workflow(db, user, agent, conversation_id)
    workflow.status = "AWAITING_APPROVAL"
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type="LANGGRAPH_SUBMIT_APPROVAL_REQUEST",
        risk_level="MEDIUM",
        payload={"kind": GRAPH_APPROVAL_KIND, "tool_name": "submit_approval_request"},
        status="WAITING",
    )
    db.add(approval)
    db.flush()
    return user, agent, str(conversation_id), workflow, approval


def test_a_message_to_a_suspended_conversation_is_answered_not_a_500(
    transactional_db_session,
) -> None:
    """The graph refuses a new run until resumed; that used to reach the user as a 500."""
    db = transactional_db_session
    user, agent, conversation_id, workflow, approval = _suspended_conversation(db)

    response = LangGraphEngine(_NeverCalled()).execute(  # type: ignore[arg-type]
        db=db, user=user, agent=agent, message="còn việc khác", conversation_id=conversation_id
    )

    assert "đang chờ phê duyệt" in response["reply"]
    assert response["approval_card"]["id"] == str(approval.id)
    # The pending approval's workflow is left exactly as its resume expects it.
    assert workflow.status == "AWAITING_APPROVAL"


def test_a_streamed_message_to_a_suspended_conversation_completes_cleanly(
    transactional_db_session,
) -> None:
    db = transactional_db_session
    user, agent, conversation_id, workflow, approval = _suspended_conversation(db)

    events = list(LangGraphEngine(_NeverCalled()).execute_stream(  # type: ignore[arg-type]
        db=db, user=user, agent=agent, message="còn việc khác", conversation_id=conversation_id
    ))

    assert [event["event"] for event in events] == ["complete"]
    assert events[0]["response"]["approval_card"]["id"] == str(approval.id)
    assert workflow.status == "AWAITING_APPROVAL"
