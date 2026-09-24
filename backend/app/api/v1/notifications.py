"""In-app notifications, unread state, preferences and operational scans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    AgentWorkflow,
    IntegrationConnection,
    Notification,
    Task,
    User,
    WorkflowApproval,
)
from app.domains.platform.audit_service import get_budget_limits_and_alerts
from app.domains.platform.notification_service import (
    CHANNELS,
    EVENT_TYPES,
    create_notification,
    get_or_create_preferences,
    mark_read,
    serialize_notification,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class QuietHours(BaseModel):
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "Asia/Ho_Chi_Minh"

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Time must use HH:MM format") from exc
        return value


class PreferenceUpdateRequest(BaseModel):
    enabled_event_types: list[str] = Field(default_factory=list)
    enabled_channels: list[str] = Field(default_factory=lambda: ["IN_APP"])
    quiet_hours: QuietHours = Field(default_factory=QuietHours)

    @field_validator("enabled_event_types")
    @classmethod
    def validate_event_types(cls, values: list[str]) -> list[str]:
        invalid = set(values) - set(EVENT_TYPES)
        if invalid:
            raise ValueError(f"Unsupported event types: {', '.join(sorted(invalid))}")
        return list(dict.fromkeys(values))

    @field_validator("enabled_channels")
    @classmethod
    def validate_channels(cls, values: list[str]) -> list[str]:
        invalid = set(values) - set(CHANNELS)
        if invalid:
            raise ValueError(f"Unsupported channels: {', '.join(sorted(invalid))}")
        return list(dict.fromkeys(values))


@router.get("", summary="List current user's notifications")
def list_notifications(
    unread_only: bool = False,
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = db.query(Notification).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.user_id == current_user.id,
    )
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    if event_type:
        query = query.filter(Notification.event_type == event_type)
    notifications = query.order_by(Notification.created_at.desc()).limit(limit).all()
    unread_count = db.query(Notification).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.user_id == current_user.id,
        Notification.is_read.is_(False),
    ).count()
    return {
        "items": [serialize_notification(item) for item in notifications],
        "unread_count": unread_count,
    }


@router.post("/{notification_id}/read", summary="Mark one notification as read")
def read_notification(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.tenant_id == current_user.tenant_id,
        Notification.user_id == current_user.id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    mark_read(notification)
    db.commit()
    return serialize_notification(notification)


@router.post("/read-all", summary="Mark all current user's notifications as read")
def read_all_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    notifications = db.query(Notification).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.user_id == current_user.id,
        Notification.is_read.is_(False),
    ).all()
    for notification in notifications:
        mark_read(notification)
    db.commit()
    return {"updated": len(notifications)}


@router.delete("/{notification_id}", summary="Delete one current user's notification")
def delete_notification(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.tenant_id == current_user.tenant_id,
        Notification.user_id == current_user.id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(notification)
    db.commit()
    return {"message": "Notification deleted"}


@router.get("/preferences", summary="Get notification preferences")
def get_notification_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    preference = get_or_create_preferences(db, current_user)
    db.commit()
    return {
        "event_catalog": EVENT_TYPES,
        "channel_catalog": CHANNELS,
        "enabled_event_types": preference.enabled_event_types or [],
        "enabled_channels": preference.enabled_channels or [],
        "quiet_hours": preference.quiet_hours or {},
    }


@router.put("/preferences", summary="Update notification preferences")
def update_notification_preferences(
    req: PreferenceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    preference = get_or_create_preferences(db, current_user)
    preference.enabled_event_types = req.enabled_event_types
    preference.enabled_channels = req.enabled_channels
    preference.quiet_hours = req.quiet_hours.model_dump()
    db.commit()
    return {"message": "Notification preferences updated"}


@router.post("/scan", summary="Generate deduplicated operational notifications")
def scan_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    created_before = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).count()

    tasks = db.query(Task).filter(
        Task.tenant_id == current_user.tenant_id,
        or_(Task.creator_id == current_user.id, Task.assignee_id == current_user.id),
    ).all()
    for task in tasks:
        if (
            task.due_date
            and now <= task.due_date <= now + timedelta(hours=24)
            and task.status not in {"COMPLETED", "FAILED", "CANCELLED"}
        ):
            create_notification(
                db,
                user=current_user,
                event_type="TASK_DUE_SOON",
                title="Task sắp hết hạn",
                message=f"{task.title} sẽ đến hạn trong vòng 24 giờ.",
                severity="WARNING",
                entity_type="TASK",
                entity_id=str(task.id),
                dedup_key=f"task-due:{task.id}:{task.due_date.date()}",
            )
        if task.status in {"COMPLETED", "FAILED"}:
            create_notification(
                db,
                user=current_user,
                event_type=f"TASK_{task.status}",
                title="Task đã hoàn thành" if task.status == "COMPLETED" else "Task thất bại",
                message=task.title,
                severity="SUCCESS" if task.status == "COMPLETED" else "ERROR",
                entity_type="TASK",
                entity_id=str(task.id),
                dedup_key=f"task-status:{task.id}:{task.status}",
            )

    pending_query = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == current_user.tenant_id,
        WorkflowApproval.status == "WAITING",
    )
    if current_user.role not in {"Owner", "Admin", "CEO", "Manager"}:
        pending_query = pending_query.filter(
            WorkflowApproval.approver_id == current_user.id
        )
    for approval in pending_query.all():
        create_notification(
            db,
            user=current_user,
            event_type="APPROVAL_REQUIRED",
            title="Cần phê duyệt",
            message=approval.workflow.title,
            severity="WARNING",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"approval:{approval.id}",
        )

    if current_user.role in {"Owner", "Admin", "CEO"}:
        for alert in get_budget_limits_and_alerts(
            db, current_user.tenant_id
        ).get("alerts", []):
            create_notification(
                db,
                user=current_user,
                event_type="AGENT_COST_LIMIT",
                title=alert["title"],
                message=alert["message"],
                severity=alert["severity"],
                entity_type="BUDGET",
                entity_id=alert["id"],
                dedup_key=f"budget:{alert['id']}:{now.strftime('%Y-%m')}",
            )
        disconnected = db.query(IntegrationConnection).filter(
            IntegrationConnection.tenant_id == current_user.tenant_id,
            IntegrationConnection.status.in_(("DISCONNECTED", "ERROR")),
        ).all()
        for connection in disconnected:
            create_notification(
                db,
                user=current_user,
                event_type="INTEGRATION_DISCONNECTED",
                title="Integration mất kết nối",
                message=f"{connection.display_name} cần được kiểm tra.",
                severity="ERROR",
                entity_type="INTEGRATION",
                entity_id=str(connection.id),
                dedup_key=f"integration-disconnected:{connection.id}:{connection.updated_at.date()}",
            )

    db.commit()
    created_after = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).count()
    return {"created": created_after - created_before}
