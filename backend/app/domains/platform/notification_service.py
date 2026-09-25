"""Durable in-app notifications with user preferences and deduplication."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.models.models import Notification, NotificationPreference, User

EVENT_TYPES = [
    "TASK_COMPLETED",
    "TASK_FAILED",
    "WORKFLOW_FAILED",
    "APPROVAL_REQUIRED",
    "APPROVAL_DECIDED",
    "AGENT_COST_LIMIT",
    "TASK_DUE_SOON",
    "DOCUMENT_READY",
    "INTEGRATION_DISCONNECTED",
    "LEAVE_APPROVAL_REQUIRED",
    "ONBOARDING_TASK_CREATED",
    "CONTRACT_EXPIRING",
    "PROBATION_ENDING",
]
CHANNELS = ["IN_APP", "EMAIL", "SLACK", "TEAMS", "MOBILE_PUSH"]
DEFAULT_CHANNELS = ["IN_APP"]


def get_or_create_preferences(
    db: Session, user: User, *, flush: bool = True
) -> NotificationPreference:
    preference = db.query(NotificationPreference).filter(
        NotificationPreference.user_id == user.id,
        NotificationPreference.tenant_id == user.tenant_id,
    ).first()
    if preference:
        return preference
    preference = NotificationPreference(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        enabled_event_types=list(EVENT_TYPES),
        enabled_channels=list(DEFAULT_CHANNELS),
        quiet_hours={},
    )
    db.add(preference)
    if flush:
        db.flush()
    return preference


def create_notification(
    db: Session,
    *,
    user: User,
    event_type: str,
    title: str,
    message: str,
    severity: str = "INFO",
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    dedup_key: Optional[str] = None,
) -> Optional[Notification]:
    preference = get_or_create_preferences(db, user)
    enabled_types = preference.enabled_event_types or EVENT_TYPES
    enabled_channels = preference.enabled_channels or DEFAULT_CHANNELS
    if event_type not in enabled_types or "IN_APP" not in enabled_channels:
        return None
    if dedup_key:
        existing = db.query(Notification).filter(
            Notification.tenant_id == user.tenant_id,
            Notification.user_id == user.id,
            Notification.dedup_key == dedup_key,
        ).first()
        if existing:
            return existing
    notification = Notification(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        event_type=event_type,
        title=title,
        message=message,
        severity=severity,
        entity_type=entity_type,
        entity_id=entity_id,
        channel="IN_APP",
        delivery_status="DELIVERED",
        payload=payload or {},
        dedup_key=dedup_key,
    )
    db.add(notification)
    return notification


def notify_users(
    db: Session,
    users: Iterable[User],
    **kwargs: Any,
) -> list[Notification]:
    created: list[Notification] = []
    seen: set[uuid.UUID] = set()
    for user in users:
        if user.id in seen or not user.is_active:
            continue
        seen.add(user.id)
        notification = create_notification(db, user=user, **kwargs)
        if notification:
            created.append(notification)
    return created


def serialize_notification(notification: Notification) -> dict[str, Any]:
    return {
        "id": str(notification.id),
        "event_type": notification.event_type,
        "title": notification.title,
        "message": notification.message,
        "severity": notification.severity,
        "entity_type": notification.entity_type,
        "entity_id": notification.entity_id,
        "channel": notification.channel,
        "delivery_status": notification.delivery_status,
        "payload": notification.payload or {},
        "is_read": notification.is_read,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": (
            notification.created_at.isoformat() if notification.created_at else None
        ),
    }


def mark_read(notification: Notification) -> None:
    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
