"""Task management with lifecycle validation, tenant isolation and role scopes."""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    AIAgent,
    AgentWorkflow,
    AuditLog,
    OnboardingCase,
    OnboardingStep,
    Task,
    TaskComment,
    User,
)
from app.domains.platform.notification_service import notify_users

router = APIRouter(prefix="/tasks", tags=["Task Management"])

TaskStatus = Literal[
    "DRAFT", "PENDING", "RUNNING", "WAITING_APPROVAL",
    "COMPLETED", "FAILED", "CANCELLED", "OVERDUE",
]
TaskPriority = Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
PRIVILEGED_ROLES = {"Owner", "Admin", "CEO"}
TRANSITIONS = {
    "DRAFT": {"PENDING", "CANCELLED"},
    "PENDING": {"RUNNING", "CANCELLED", "OVERDUE"},
    "RUNNING": {"WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED", "OVERDUE"},
    "WAITING_APPROVAL": {"RUNNING", "COMPLETED", "FAILED", "CANCELLED", "OVERDUE"},
    "FAILED": {"PENDING", "CANCELLED"},
    "OVERDUE": {"RUNNING", "COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
TASK_STATUS_ORDER = [
    "DRAFT", "PENDING", "RUNNING", "WAITING_APPROVAL",
    "COMPLETED", "FAILED", "CANCELLED", "OVERDUE",
]


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    assignee_id: Optional[uuid.UUID] = None
    ai_agent_id: Optional[uuid.UUID] = None
    priority: TaskPriority = "MEDIUM"
    due_date: Optional[datetime] = None
    status: Literal["DRAFT", "PENDING"] = "PENDING"
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    assignee_id: Optional[uuid.UUID] = None
    ai_agent_id: Optional[uuid.UUID] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None
    status: Optional[TaskStatus] = None
    attachments: Optional[list[dict[str, Any]]] = None
    output_result: Optional[dict[str, Any]] = None


class CommentCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


def _visible_task_query(db: Session, current_user: User):
    query = db.query(Task).filter(Task.tenant_id == current_user.tenant_id)
    if current_user.role in PRIVILEGED_ROLES:
        return query
    if current_user.role == "Manager":
        department_user_ids = db.query(User.id).filter(
            User.tenant_id == current_user.tenant_id,
            User.department == current_user.department,
        )
        return query.filter(or_(
            Task.creator_id.in_(department_user_ids),
            Task.assignee_id.in_(department_user_ids),
        ))
    return query.filter(or_(
        Task.creator_id == current_user.id,
        Task.assignee_id == current_user.id,
    ))


def _get_visible_task(db: Session, current_user: User, task_id: uuid.UUID) -> Task:
    task = _visible_task_query(db, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _validate_relations(
    db: Session,
    current_user: User,
    assignee_id: Optional[uuid.UUID],
    ai_agent_id: Optional[uuid.UUID],
) -> None:
    if assignee_id:
        assignee = db.query(User).filter(
            User.id == assignee_id,
            User.tenant_id == current_user.tenant_id,
            User.is_active.is_(True),
        ).first()
        if not assignee:
            raise HTTPException(status_code=422, detail="Assignee does not belong to this workspace")
        if current_user.role == "Manager" and assignee.department != current_user.department:
            raise HTTPException(status_code=403, detail="Manager can only assign within their department")
        if current_user.role in {"Employee", "Guest"} and assignee.id != current_user.id:
            raise HTTPException(status_code=403, detail="Employee can only assign a task to themselves")
    if ai_agent_id:
        agent = db.query(AIAgent).filter(
            AIAgent.id == ai_agent_id,
            AIAgent.tenant_id == current_user.tenant_id,
            AIAgent.is_active.is_(True),
        ).first()
        if not agent:
            raise HTTPException(status_code=422, detail="AI Employee is unavailable in this workspace")


def _effective_status(task: Task) -> str:
    if (
        task.due_date
        and task.due_date < datetime.now(timezone.utc)
        and task.status not in {"COMPLETED", "CANCELLED", "FAILED"}
    ):
        return "OVERDUE"
    return task.status


def _add_history(
    db: Session,
    task: Task,
    current_user: User,
    action: str,
    changes: dict[str, Any],
) -> None:
    db.add(AuditLog(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.id,
        actor_type="USER",
        agent_role="TASK",
        tool_name=action,
        action=action,
        resource_type="TASK",
        resource_id=str(task.id),
        input_parameters={
            "task_id": str(task.id),
            "actor_id": str(current_user.id),
            "actor_name": current_user.full_name,
        },
        output_result=changes,
        after_data=changes,
        status="SUCCESS",
        execution_time_ms=0,
    ))


def _serialize_task(task: Task) -> dict[str, Any]:
    effective_status = _effective_status(task)
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "creator": (
            {"id": str(task.creator.id), "name": task.creator.full_name, "email": task.creator.email}
            if task.creator else None
        ),
        "assignee": (
            {"id": str(task.assignee.id), "name": task.assignee.full_name}
            if task.assignee else None
        ),
        "ai_agent": (
            {"id": str(task.ai_agent.id), "name": task.ai_agent.name, "emoji": task.ai_agent.avatar_emoji}
            if task.ai_agent else None
        ),
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "status": effective_status,
        "allowed_transitions": [
            status
            for status in TASK_STATUS_ORDER
            if status in TRANSITIONS[effective_status]
        ],
        "comments_count": len(task.comments),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.get("", summary="List tasks visible to the current role")
def get_tasks(
    status_filter: Optional[TaskStatus] = Query(None, alias="status"),
    priority_filter: Optional[TaskPriority] = Query(None, alias="priority"),
    assignee_filter: Optional[uuid.UUID] = Query(None, alias="assignee_id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = _visible_task_query(db, current_user)
    if status_filter and status_filter != "OVERDUE":
        query = query.filter(Task.status == status_filter)
    if priority_filter:
        query = query.filter(Task.priority == priority_filter)
    if assignee_filter:
        query = query.filter(Task.assignee_id == assignee_filter)
    tasks = query.order_by(Task.created_at.desc()).all()
    if status_filter == "OVERDUE":
        tasks = [task for task in tasks if _effective_status(task) == "OVERDUE"]
    return [_serialize_task(task) for task in tasks]


@router.post("", status_code=201, summary="Create a task")
def create_task(
    req: TaskCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _validate_relations(db, current_user, req.assignee_id, req.ai_agent_id)
    task = Task(
        tenant_id=current_user.tenant_id,
        title=req.title.strip(),
        description=req.description,
        creator_id=current_user.id,
        assignee_id=req.assignee_id,
        ai_agent_id=req.ai_agent_id,
        priority=req.priority,
        due_date=req.due_date,
        status=req.status,
        attachments=req.attachments,
    )
    db.add(task)
    db.flush()
    _add_history(db, task, current_user, "task_created", {"status": task.status})
    db.commit()
    return {"message": "Task created successfully", "task_id": str(task.id)}


@router.get("/{task_id}", summary="Get task detail, comments and change history")
def get_task_detail(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    task = _get_visible_task(db, current_user, task_id)
    comments = db.query(TaskComment).filter(
        TaskComment.task_id == task.id
    ).order_by(TaskComment.created_at.asc()).all()
    history = [
        item for item in db.query(AuditLog).filter(
        AuditLog.tenant_id == current_user.tenant_id,
        AuditLog.agent_role == "TASK",
        ).order_by(AuditLog.created_at.asc()).all()
        if (item.input_parameters or {}).get("task_id") == str(task.id)
    ]
    result = _serialize_task(task)
    result.update({
        "attachments": task.attachments or [],
        "output_result": task.output_result,
        "comments": [
            {
                "id": str(comment.id),
                "user_name": comment.user.full_name if comment.user else "User",
                "content": comment.content,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
            }
            for comment in comments
        ],
        "history": [
            {
                "id": str(item.id),
                "action": item.tool_name,
                "actor": (item.input_parameters or {}).get("actor_name"),
                "changes": item.output_result or {},
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in history
        ],
    })
    return result


@router.patch("/{task_id}", summary="Update a task")
def update_task(
    task_id: uuid.UUID,
    req: TaskUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    task = _get_visible_task(db, current_user, task_id)
    data = req.model_dump(exclude_unset=True)
    if any(field in data for field in {"assignee_id", "ai_agent_id"}):
        if current_user.role in {"Employee", "Guest"}:
            raise HTTPException(status_code=403, detail="Employee cannot reassign a task")
        _validate_relations(
            db,
            current_user,
            data.get("assignee_id", task.assignee_id),
            data.get("ai_agent_id", task.ai_agent_id),
        )
    if "status" in data and data["status"] != task.status:
        current_status = _effective_status(task)
        if data["status"] not in TRANSITIONS[current_status]:
            raise HTTPException(
                status_code=409,
                detail=f"Invalid task transition: {current_status} -> {data['status']}",
            )

    changes: dict[str, Any] = {}
    for field_name, value in data.items():
        old_value = getattr(task, field_name)
        if old_value != value:
            changes[field_name] = {
                "from": old_value.isoformat() if isinstance(old_value, datetime) else old_value,
                "to": value.isoformat() if isinstance(value, datetime) else value,
            }
            setattr(task, field_name, value)
    if changes:
        _add_history(db, task, current_user, "task_updated", changes)
        if "status" in changes:
            onboarding_step = db.query(OnboardingStep).filter(
                OnboardingStep.task_id == task.id
            ).first()
            if onboarding_step:
                onboarding_step.status = task.status
                onboarding_step.completed_at = (
                    datetime.now(timezone.utc) if task.status == "COMPLETED" else None
                )
                db.flush()
                onboarding_case = db.query(OnboardingCase).filter(
                    OnboardingCase.id == onboarding_step.onboarding_id
                ).first()
                if onboarding_case:
                    unfinished = db.query(OnboardingStep).filter(
                        OnboardingStep.onboarding_id == onboarding_case.id,
                        OnboardingStep.status != "COMPLETED",
                    ).count()
                    if unfinished == 0:
                        onboarding_case.status = "COMPLETED"
                        onboarding_case.completed_at = datetime.now(timezone.utc)
                        workflow = db.query(AgentWorkflow).filter(
                            AgentWorkflow.id == onboarding_case.workflow_id
                        ).first()
                        if workflow:
                            workflow.status = "COMPLETED"
                            workflow.completed_at = datetime.now(timezone.utc)
        if "status" in changes and task.status in {"COMPLETED", "FAILED"}:
            recipients = [
                user
                for user in (task.creator, task.assignee)
                if user is not None
            ]
            notify_users(
                db,
                recipients,
                event_type=f"TASK_{task.status}",
                title=(
                    "Task đã hoàn thành"
                    if task.status == "COMPLETED"
                    else "Task thất bại"
                ),
                message=task.title,
                severity="SUCCESS" if task.status == "COMPLETED" else "ERROR",
                entity_type="TASK",
                entity_id=str(task.id),
                dedup_key=f"task-status:{task.id}:{task.status}",
            )
        db.commit()
    return {"message": "Task updated successfully"}


@router.post("/{task_id}/comments", status_code=201, summary="Add a task comment")
def add_task_comment(
    task_id: uuid.UUID,
    req: CommentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    task = _get_visible_task(db, current_user, task_id)
    comment = TaskComment(task_id=task.id, user_id=current_user.id, content=req.content.strip())
    db.add(comment)
    db.flush()
    _add_history(db, task, current_user, "task_commented", {"comment_id": str(comment.id)})
    db.commit()
    return {"message": "Comment added successfully"}


@router.delete("/{task_id}", summary="Delete a draft or completed task")
def delete_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    task = _get_visible_task(db, current_user, task_id)
    if current_user.role not in PRIVILEGED_ROLES and task.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator or Admin can delete this task")
    if task.status not in {"DRAFT", "COMPLETED"}:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only DRAFT or COMPLETED tasks can be deleted; "
                "cancel active tasks instead"
            ),
        )
    _add_history(
        db,
        task,
        current_user,
        "task_deleted",
        {"status": task.status, "title": task.title},
    )
    db.delete(task)
    db.commit()
    return {"message": "Task deleted successfully"}
