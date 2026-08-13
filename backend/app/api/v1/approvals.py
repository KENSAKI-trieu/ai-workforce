"""Human-in-the-loop approval gates with tenant and approver enforcement."""

import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    AgentWorkflow,
    AuditLog,
    CustomerSupportCase,
    Task,
    User,
    UserMemory,
    WorkflowApproval,
)
from app.services.notification_service import create_notification
from app.services.hr_service import can_approve_hr_request, finalize_leave_approval
from app.services.work_queue import enqueue_job
from app.services.agents.langgraph_engine import LangGraphEngine
from app.services.ai_service_client import AIServiceError
from app.services.langgraph_approvals import GRAPH_APPROVAL_KIND

router = APIRouter(prefix="/approvals", tags=["Workflow Approvals"])
APPROVER_ROLES = {"Owner", "Admin", "CEO", "Manager"}
EXECUTIVE_APPROVER_ROLES = {"Owner", "Admin", "CEO"}


class ApprovalActionRequest(BaseModel):
    action: Literal["APPROVE", "REJECT", "EDIT_AND_APPROVE"]
    comments: Optional[str] = None
    edited_payload: Optional[dict[str, Any]] = None


def _can_approve(db: Session, current_user: User, approval: WorkflowApproval) -> bool:
    payload = approval.payload or {}
    if payload.get("kind") == GRAPH_APPROVAL_KIND:
        if current_user.role in EXECUTIVE_APPROVER_ROLES:
            return True
        if current_user.role != "Manager" or approval.risk_level == "CRITICAL":
            return False
        initiator = approval.workflow.initiator
        if initiator.id == current_user.id:
            return False
        return (
            initiator.manager_id == current_user.id
            or initiator.department == current_user.department
        )
    if approval.action_type == "LEAVE_REQUEST":
        return can_approve_hr_request(db, current_user, approval)
    if approval.action_type == "LEGAL_DOCUMENT_APPROVAL":
        return current_user.role in EXECUTIVE_APPROVER_ROLES
    if current_user.role not in APPROVER_ROLES:
        return approval.approver_id == current_user.id
    if approval.approver_id and approval.approver_id != current_user.id:
        return current_user.role in {"Owner", "Admin", "CEO"}
    return True


def _resume_langgraph_approval(
    db: Session,
    approval: WorkflowApproval,
    current_user: User,
) -> dict[str, Any]:
    try:
        graph_response = LangGraphEngine().resume_approval(
            db=db,
            approval=approval,
            approved=approval.status == "APPROVED",
            reviewer=current_user,
            comments=approval.comments,
        )
    except (AIServiceError, ValueError, PermissionError) as error:
        approval.resume_error = f"{type(error).__name__}: {str(error)[:1000]}"
        approval.workflow.status = "RESUME_FAILED"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Approval was saved but LangGraph resume failed",
                "approval_id": str(approval.id),
                "retryable": True,
            },
        ) from error
    return {
        "id": str(approval.id),
        "status": approval.status,
        "action_taken": "APPROVE" if approval.status == "APPROVED" else "REJECT",
        "payload": approval.payload,
        "message": f"Approval moved to {approval.status} and LangGraph resumed.",
        "orchestration": graph_response.get("orchestration"),
        "graph_response": graph_response,
    }


@router.get("/pending", summary="List pending approvals visible to the current approver")
def get_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[dict[str, Any]]:
    approvals = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == current_user.tenant_id,
        WorkflowApproval.status == "WAITING",
    ).order_by(WorkflowApproval.updated_at.desc()).all()
    def public_payload(approval: WorkflowApproval) -> dict[str, Any]:
        payload = dict(approval.payload or {})
        if approval.action_type == "LEGAL_DOCUMENT_APPROVAL":
            payload.pop("draft_storage_key", None)
            payload.pop("approved_storage_key", None)
        return payload

    return [
        {
            "id": str(approval.id),
            "workflow_id": str(approval.workflow_id),
            "workflow_title": approval.workflow.title,
            "action_type": approval.action_type,
            "risk_level": approval.risk_level,
            "payload": public_payload(approval),
            "reason": (approval.payload or {}).get("reason"),
            "requester": (approval.payload or {}).get("requester_name"),
            "data_sources": (approval.payload or {}).get("data_sources", []),
            "status": approval.status,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "comments": approval.comments,
        }
        for approval in approvals
        if _can_approve(db, current_user, approval)
    ]


@router.post("/{approval_id}/action", summary="Approve, reject or edit-and-approve")
def process_approval_action(
    approval_id: UUID,
    req: ApprovalActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    approval = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        WorkflowApproval.id == approval_id,
        AgentWorkflow.tenant_id == current_user.tenant_id,
    ).with_for_update().first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if not _can_approve(db, current_user, approval):
        raise HTTPException(status_code=403, detail="You are not an eligible approver")
    if approval.status != "WAITING":
        expected_status = (
            "APPROVED" if req.action in {"APPROVE", "EDIT_AND_APPROVE"} else "REJECTED"
        )
        if (
            (approval.payload or {}).get("kind") == GRAPH_APPROVAL_KIND
            and approval.status == expected_status
            and approval.resume_error
            and approval.resumed_at is None
        ):
            return _resume_langgraph_approval(db, approval, current_user)
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}")
    if approval.expires_at and approval.expires_at < datetime.now(timezone.utc):
        approval.status = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=410, detail="Approval request has expired")
    if req.action == "EDIT_AND_APPROVE" and req.edited_payload is None:
        raise HTTPException(status_code=422, detail="edited_payload is required")
    if approval.action_type == "LEAVE_REQUEST" and req.action == "EDIT_AND_APPROVE":
        raise HTTPException(
            status_code=422,
            detail="Structured leave requests must be approved or rejected without editing",
        )
    if approval.action_type == "LEGAL_DOCUMENT_APPROVAL" and req.action == "EDIT_AND_APPROVE":
        raise HTTPException(
            status_code=422,
            detail="Generated legal artifacts must be approved or rejected without editing the payload",
        )
    is_langgraph = (approval.payload or {}).get("kind") == GRAPH_APPROVAL_KIND
    if is_langgraph and req.action == "EDIT_AND_APPROVE":
        raise HTTPException(
            status_code=422,
            detail="LangGraph tool arguments cannot be edited during approval",
        )

    original_payload = approval.payload
    if req.action == "EDIT_AND_APPROVE":
        approval.payload = req.edited_payload
    approved = req.action in {"APPROVE", "EDIT_AND_APPROVE"}
    approval.status = "APPROVED" if approved else "REJECTED"
    approval.comments = req.comments
    approval.approver_id = current_user.id
    is_support_email = approval.action_type == "SUPPORT_EMAIL_SEND"
    approval.workflow.status = (
        "IN_PROGRESS" if is_langgraph or (approved and is_support_email)
        else "COMPLETED" if approved
        else "FAILED"
    )
    approval.workflow.completed_at = (
        None if approved and is_support_email else datetime.now(timezone.utc)
    )

    support_case = None
    if is_support_email:
        support_case = db.query(CustomerSupportCase).filter(
            CustomerSupportCase.workflow_id == approval.workflow_id
        ).first()
        if support_case:
            if req.action == "EDIT_AND_APPROVE" and req.edited_payload:
                support_case.draft_reply = req.edited_payload.get(
                    "draft_reply", support_case.draft_reply
                )
            support_case.status = "QUEUED" if approved else "REJECTED"
            support_case.last_error = None if approved else (
                req.comments or "Manager rejected the reply"
            )
            support_task = db.query(Task).filter(Task.id == support_case.task_id).first()
            if support_task and not approved:
                support_task.status = "CANCELLED"

    if approval.action_type == "LEAVE_REQUEST":
        finalize_leave_approval(
            db,
            approval,
            current_user,
            approved=approved,
            comment=req.comments,
        )

    if approved and approval.action_type in {"XIN_NGHI_PHEP", "XIN NGHỈ PHÉP"}:
        request_payload = approval.payload or {}
        requester_id = request_payload.get("requester_id")
        days_requested = request_payload.get("days_requested", 1)
        if requester_id:
            memory = db.query(UserMemory).filter(
                UserMemory.tenant_id == current_user.tenant_id,
                UserMemory.user_id == requester_id,
                UserMemory.memory_key == "leave_balance",
            ).first()
            if memory and memory.memory_value:
                try:
                    data = json.loads(memory.memory_value)
                    data["used_days"] = data.get("used_days", 0) + days_requested
                    data["remaining_days"] = max(
                        0, data.get("total_days", 12) - data["used_days"]
                    )
                    memory.memory_value = json.dumps(data)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass

    db.add(AuditLog(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.id,
        actor_type="USER",
        workflow_id=approval.workflow_id,
        agent_role="APPROVAL",
        tool_name=req.action.lower(),
        action=f"approval.{req.action.lower()}",
        resource_type="APPROVAL",
        resource_id=str(approval.id),
        input_parameters={
            "approval_id": str(approval.id),
            "approver_id": str(current_user.id),
            "original_payload": original_payload,
        },
        output_result={"status": approval.status, "payload": approval.payload},
        before_data={"status": "WAITING", "payload": original_payload},
        after_data={"status": approval.status, "payload": approval.payload},
        status="SUCCESS",
        execution_time_ms=0,
    ))
    create_notification(
        db,
        user=approval.workflow.initiator,
        event_type="APPROVAL_DECIDED",
        title="Yêu cầu đã được phê duyệt" if approved else "Yêu cầu bị từ chối",
        message=approval.workflow.title,
        severity="SUCCESS" if approved else "ERROR",
        entity_type="WORKFLOW",
        entity_id=str(approval.workflow_id),
        dedup_key=f"approval-result:{approval.id}:{approval.status}",
    )
    db.commit()
    if is_langgraph:
        return _resume_langgraph_approval(db, approval, current_user)
    if approved and is_support_email and support_case:
        try:
            enqueue_job(
                "support.execute",
                {"support_case_id": str(support_case.id)},
                f"support:{support_case.id}:approval:{approval.id}",
            )
        except Exception as error:
            support_case.status = "QUEUE_FAILED"
            support_case.last_error = f"Queue unavailable: {type(error).__name__}"
            approval.workflow.status = "FAILED"
            support_task = db.query(Task).filter(Task.id == support_case.task_id).first()
            if support_task:
                support_task.status = "FAILED"
            db.commit()
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Approval saved but resume queue is unavailable",
                    "support_case_id": str(support_case.id),
                },
            ) from error
    return {
        "id": str(approval.id),
        "status": approval.status,
        "action_taken": req.action,
        "payload": approval.payload,
        "message": f"Approval moved to {approval.status}.",
    }
