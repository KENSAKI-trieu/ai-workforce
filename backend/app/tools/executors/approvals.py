"""Approval tools."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.models import AgentWorkflow, User, WorkflowApproval
from app.agents.langgraph.approvals import GRAPH_APPROVER_ROLES
from app.tools.registry import ToolContext
from app.tools.schemas import SubmitApprovalInput

# Keys in a caller-supplied approval payload that the approval routes read as control data.
# `kind` selects which branch of `_can_approve` applies and whether the approve route tries
# to resume a LangGraph thread, so accepting it from a tool argument would let a requester
# choose the rule that governs their own request.
RESERVED_APPROVAL_PAYLOAD_KEYS = frozenset({"kind", "requester_id", "requester_name"})


def _validate_approver(context: ToolContext, request: SubmitApprovalInput) -> User | None:
    """Reject approvers who would turn the gate into a formality."""
    if not request.approver_id:
        return None
    actor = context.actor
    if request.approver_id == actor.id:
        # `_can_approve` lets the named approver act on their own request, so a requester
        # naming themselves approves their own external action.
        raise HTTPException(
            status_code=422,
            detail="The requester cannot be the approver of their own request",
        )
    approver = context.db.query(User).filter(
        User.id == request.approver_id,
        User.tenant_id == actor.tenant_id,
        User.is_active.is_(True),
    ).first()
    if not approver:
        raise HTTPException(status_code=422, detail="Approver is unavailable")
    if approver.role not in GRAPH_APPROVER_ROLES:
        raise HTTPException(
            status_code=422,
            detail="Approver must hold an approver role (Owner, Admin, CEO or Manager)",
        )
    return approver


def submit_approval_request(
    context: ToolContext,
    request: SubmitApprovalInput,
) -> dict[str, Any]:
    db = context.db
    actor = context.actor
    reserved = RESERVED_APPROVAL_PAYLOAD_KEYS & set(request.payload)
    if reserved:
        raise HTTPException(
            status_code=422,
            detail=f"Approval payload cannot set reserved keys: {', '.join(sorted(reserved))}",
        )
    _validate_approver(context, request)
    workflow = AgentWorkflow(
        tenant_id=actor.tenant_id,
        initiator_id=actor.id,
        title=request.title.strip(),
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={"agent_role": "SYSTEM", "steps": ["REQUESTED", "HUMAN_APPROVAL"]},
    )
    db.add(workflow)
    db.flush()
    payload = dict(request.payload)
    payload.update({"requester_id": str(actor.id), "requester_name": actor.full_name})
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        approver_id=request.approver_id,
        action_type=request.action_type,
        risk_level=request.risk_level,
        payload=payload,
        status="WAITING",
        expires_at=request.expires_at,
    )
    db.add(approval)
    db.flush()
    return {
        "workflow_id": str(workflow.id),
        "approval_id": str(approval.id),
        "status": approval.status,
    }
