"""Idempotent persistence for approvals created by LangGraph interrupts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import AIAgent, AgentWorkflow, User, WorkflowApproval
from app.services.notification_service import create_notification


GRAPH_WORKFLOW_KIND = "LANGGRAPH_CONVERSATION"
GRAPH_APPROVAL_KIND = "LANGGRAPH_INTERRUPT"
GRAPH_APPROVER_ROLES = ("Owner", "Admin", "CEO", "Manager")
GRAPH_EXECUTIVE_ROLES = ("Owner", "Admin", "CEO")


def _risk(value: dict[str, Any]) -> str:
    if value.get("action") == "EXTERNAL_ACTION":
        return "HIGH"
    if value.get("tool_name") == "generate_legal_document":
        return "HIGH"
    return "MEDIUM"


def _should_notify_approver(approver: User, requester: User) -> bool:
    if approver.role in GRAPH_EXECUTIVE_ROLES:
        return True
    return (
        approver.role == "Manager"
        and approver.id != requester.id
        and (
            requester.manager_id == approver.id
            or approver.department == requester.department
        )
    )


def ensure_graph_approval(
    db: Session,
    *,
    workflow: AgentWorkflow,
    user: User,
    agent: AIAgent,
    interrupt_id: str,
    value: dict[str, Any],
) -> WorkflowApproval:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"langgraph-approval:{interrupt_id}"},
    )
    approval = db.query(WorkflowApproval).filter(
        WorkflowApproval.langgraph_interrupt_id == interrupt_id,
    ).first()
    if approval is not None:
        if approval.workflow_id != workflow.id:
            raise PermissionError("LangGraph interrupt is already bound to another workflow")
        return approval

    tool_name = str(value.get("tool_name") or "ACTION")
    payload = {
        "kind": GRAPH_APPROVAL_KIND,
        "interrupt_id": interrupt_id,
        "thread_id": workflow.thread_id,
        "workflow_id": str(workflow.id),
        "requester_id": str(user.id),
        "requester_name": user.full_name,
        "requester_department": user.department,
        "agent_role": agent.role_code,
        "tool_name": tool_name,
        "action_class": value.get("action"),
        "arguments": value.get("arguments") or {},
        "reason": value.get("reason") or "AI action requires human approval.",
    }
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type=f"LANGGRAPH_{tool_name.upper()}"[:100],
        risk_level=_risk(value),
        payload=payload,
        status="WAITING",
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        langgraph_interrupt_id=interrupt_id,
    )
    db.add(approval)
    db.flush()
    approvers = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.role.in_(GRAPH_APPROVER_ROLES),
        User.is_active.is_(True),
    ).all()
    for approver in approvers:
        if not _should_notify_approver(approver, user):
            continue
        create_notification(
            db,
            user=approver,
            event_type="APPROVAL_REQUIRED",
            title="Hành động AI cần phê duyệt",
            message=f"{agent.name}: {tool_name}",
            severity="WARNING",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"langgraph-approval:{approval.id}:{approver.id}",
        )
    return approval
