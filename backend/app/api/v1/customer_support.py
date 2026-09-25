"""Asynchronous, auditable Customer Support operations."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    AgentWorkflow,
    CustomerSupportCase,
    OutboundMessage,
    Task,
    User,
    WorkflowStepExecution,
)
from app.domains.support.support_workflow import initialize_steps
from app.domains.platform.work_queue import enqueue_job, queue_stats

router = APIRouter(prefix="/customer-support", tags=["Customer Support Operations"])
ADMIN_ROLES = {"Owner", "Admin", "CEO"}


class SupportCaseCreate(BaseModel):
    customer_email: EmailStr
    customer_name: str | None = Field(None, max_length=255)
    subject: str = Field(min_length=2, max_length=255)
    body: str = Field(min_length=5, max_length=50000)
    priority: str = Field("MEDIUM", pattern="^(LOW|MEDIUM|HIGH|URGENT)$")
    due_in_hours: int = Field(48, ge=1, le=720)


def _query(db: Session, user: User):
    query = db.query(CustomerSupportCase).filter(
        CustomerSupportCase.tenant_id == user.tenant_id
    )
    if user.role in ADMIN_ROLES or user.role == "Manager":
        return query
    return query.filter(CustomerSupportCase.created_by_id == user.id)


def _serialize(db: Session, case: CustomerSupportCase) -> dict[str, Any]:
    steps = db.query(WorkflowStepExecution).filter(
        WorkflowStepExecution.workflow_id == case.workflow_id
    ).order_by(WorkflowStepExecution.position).all()
    message = db.query(OutboundMessage).filter(
        OutboundMessage.support_case_id == case.id
    ).first()
    return {
        "id": str(case.id),
        "task_id": str(case.task_id),
        "workflow_id": str(case.workflow_id),
        "customer_email": case.customer_email,
        "customer_name": case.customer_name,
        "subject": case.subject,
        "classification": case.classification,
        "confidence": case.confidence,
        "draft_reply": case.draft_reply,
        "citations": case.citations,
        "status": case.status,
        "last_error": case.last_error,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        "steps": [
            {
                "key": step.step_key,
                "type": step.step_type,
                "position": step.position,
                "status": step.status,
                "attempt_count": step.attempt_count,
                "max_attempts": step.max_attempts,
                "timeout_seconds": step.timeout_seconds,
                "output": step.output_data,
                "error": step.error_message,
            }
            for step in steps
        ],
        "delivery": (
            {
                "status": message.status,
                "mode": message.delivery_mode,
                "attempt_count": message.attempt_count,
                "provider_message_id": message.provider_message_id,
                "error": message.error_message,
            }
            if message
            else None
        ),
    }


@router.post("/cases", status_code=status.HTTP_202_ACCEPTED)
def create_case(
    req: SupportCaseCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    existing = db.query(CustomerSupportCase).filter(
        CustomerSupportCase.tenant_id == current_user.tenant_id,
        CustomerSupportCase.idempotency_key == idempotency_key
    ).first()
    if existing:
        if existing.created_by_id != current_user.id and (
            current_user.role not in ADMIN_ROLES
            and current_user.role != "Manager"
        ):
            raise HTTPException(status_code=409, detail="Idempotency key is already in use")
        return _serialize(db, existing)
    task = Task(
        tenant_id=current_user.tenant_id,
        title=f"Customer Support: {req.subject}",
        description=req.body,
        creator_id=current_user.id,
        priority=req.priority,
        due_date=datetime.now(timezone.utc) + timedelta(hours=req.due_in_hours),
        status="PENDING",
    )
    workflow = AgentWorkflow(
        tenant_id=current_user.tenant_id,
        initiator_id=current_user.id,
        title=f"Support email — {req.subject}",
        status="PENDING",
        current_step=0,
        thread_id=str(uuid.uuid4()),
        dag_plan={
            "kind": "run",
            "workflow_type": "CUSTOMER_SUPPORT_EMAIL",
            "version": "1.0",
            "steps": [
                "read_email",
                "classify",
                "retrieve_policy",
                "draft_reply",
                "human_approval",
                "send_email",
                "finalize",
            ],
        },
    )
    db.add_all([task, workflow])
    db.flush()
    case = CustomerSupportCase(
        tenant_id=current_user.tenant_id,
        task_id=task.id,
        workflow_id=workflow.id,
        created_by_id=current_user.id,
        idempotency_key=idempotency_key,
        customer_email=str(req.customer_email),
        customer_name=req.customer_name,
        subject=req.subject,
        inbound_body=req.body,
        status="QUEUED",
    )
    db.add(case)
    db.flush()
    initialize_steps(db, case)
    db.commit()
    try:
        enqueue_job(
            "support.execute",
            {"support_case_id": str(case.id)},
            f"support:{case.id}:initial",
        )
    except Exception as error:
        case.status = "QUEUE_FAILED"
        case.last_error = f"Queue unavailable: {type(error).__name__}"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"message": "Case persisted but queue is unavailable", "case_id": str(case.id)},
        ) from error
    db.refresh(case)
    return _serialize(db, case)


@router.get("/cases")
def list_cases(
    case_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = _query(db, current_user)
    if case_status:
        query = query.filter(CustomerSupportCase.status == case_status.upper())
    return [
        _serialize(db, item)
        for item in query.order_by(CustomerSupportCase.created_at.desc()).limit(limit).all()
    ]


@router.get("/operations")
def support_operations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Tenant workflow metrics plus non-sensitive queue/worker health."""
    tenant_cases = db.query(CustomerSupportCase).filter(
        CustomerSupportCase.tenant_id == current_user.tenant_id
    )
    total = tenant_cases.count()
    completed = tenant_cases.filter(CustomerSupportCase.status == "COMPLETED").count()
    failed = tenant_cases.filter(
        CustomerSupportCase.status.in_(("FAILED", "QUEUE_FAILED", "REJECTED"))
    ).count()
    waiting_approval = tenant_cases.filter(
        CustomerSupportCase.status == "WAITING_APPROVAL"
    ).count()
    overdue = tenant_cases.join(Task, Task.id == CustomerSupportCase.task_id).filter(
        Task.due_date < datetime.now(timezone.utc),
        Task.status.notin_(("COMPLETED", "CANCELLED")),
    ).count()
    retried_steps = db.query(WorkflowStepExecution).filter(
        WorkflowStepExecution.tenant_id == current_user.tenant_id,
        WorkflowStepExecution.attempt_count > 1,
    ).count()
    average_seconds = db.query(
        func.avg(
            func.extract(
                "epoch",
                CustomerSupportCase.updated_at - CustomerSupportCase.created_at,
            )
        )
    ).filter(
        CustomerSupportCase.tenant_id == current_user.tenant_id,
        CustomerSupportCase.status == "COMPLETED",
    ).scalar()
    terminal = completed + failed
    try:
        queue = queue_stats()
    except Exception as error:
        queue = {
            "available": False,
            "queued": None,
            "processing": None,
            "dead_letter": None,
            "worker_online": False,
            "worker_last_seen": None,
            "error": type(error).__name__,
        }
    return {
        "cases": {
            "total": total,
            "completed": completed,
            "failed_or_rejected": failed,
            "waiting_approval": waiting_approval,
            "overdue": overdue,
            "success_rate": round(completed / terminal, 4) if terminal else None,
            "average_completion_seconds": (
                round(float(average_seconds), 3) if average_seconds is not None else None
            ),
            "retried_steps": retried_steps,
        },
        "queue": queue,
    }


@router.get("/cases/{case_id}")
def get_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    case = _query(db, current_user).filter(CustomerSupportCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Support case not found")
    return _serialize(db, case)


@router.post("/cases/{case_id}/retry", status_code=202)
def retry_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    case = _query(db, current_user).filter(CustomerSupportCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Support case not found")
    if case.status not in {"FAILED", "QUEUE_FAILED", "RETRY_PENDING"}:
        raise HTTPException(status_code=409, detail=f"Case cannot retry from {case.status}")
    failed_steps = db.query(WorkflowStepExecution).filter(
        WorkflowStepExecution.workflow_id == case.workflow_id,
        WorkflowStepExecution.status.in_(("FAILED", "RETRY_PENDING")),
    ).all()
    for step in failed_steps:
        if step.attempt_count >= step.max_attempts:
            step.attempt_count = 0
        step.status = "PENDING"
        step.error_message = None
    case.status = "QUEUED"
    case.last_error = None
    db.commit()
    try:
        enqueue_job(
            "support.execute",
            {"support_case_id": str(case.id)},
            f"support:{case.id}:retry:{uuid.uuid4()}",
        )
    except Exception as error:
        case.status = "QUEUE_FAILED"
        case.last_error = f"Queue unavailable: {type(error).__name__}"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"message": "Retry persisted but queue is unavailable", "case_id": str(case.id)},
        ) from error
    return _serialize(db, case)
