"""Structured, tenant-scoped enterprise audit events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import AuditLog, User

SENSITIVE_MARKERS = {
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
}


def redact_sensitive(value: Any) -> Any:
    """Recursively redact credential-like values before persistence."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(marker in key.lower() for marker in SENSITIVE_MARKERS)
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value


def request_metadata(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",", 1)[0].strip()
        if forwarded
        else request.client.host if request.client else None
    )
    user_agent = request.headers.get("user-agent")
    return ip_address, user_agent[:1000] if user_agent else None


def add_audit_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    actor_user: Optional[User] = None,
    actor_type: str = "USER",
    agent_role: str = "SYSTEM",
    tool_name: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    workflow_id: Optional[uuid.UUID] = None,
    before_data: Optional[dict[str, Any]] = None,
    after_data: Optional[dict[str, Any]] = None,
    input_parameters: Optional[dict[str, Any]] = None,
    output_result: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
    status: str = "SUCCESS",
    error_message: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
) -> AuditLog:
    ip_address, user_agent = request_metadata(request)
    event = AuditLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_user_id=actor_user.id if actor_user else None,
        actor_type=actor_type,
        workflow_id=workflow_id,
        agent_role=agent_role,
        tool_name=tool_name or action,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        input_parameters=redact_sensitive(input_parameters),
        output_result=redact_sensitive(output_result),
        before_data=redact_sensitive(before_data),
        after_data=redact_sensitive(after_data),
        ip_address=ip_address,
        user_agent=user_agent,
        status=status,
        error_message=error_message,
        execution_time_ms=execution_time_ms,
    )
    db.add(event)
    return event


def serialize_audit_event(event: AuditLog) -> dict[str, Any]:
    actor = event.actor_user
    return {
        "id": str(event.id),
        "actor": (
            {
                "id": str(actor.id),
                "name": actor.full_name,
                "email": actor.email,
                "department": actor.department,
            }
            if actor
            else None
        ),
        "actor_type": event.actor_type or ("AGENT" if event.agent_role else "SYSTEM"),
        "agent_role": event.agent_role,
        "action": event.action or event.tool_name,
        "tool_name": event.tool_name,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "workflow_id": str(event.workflow_id) if event.workflow_id else None,
        "before_data": event.before_data,
        "after_data": event.after_data,
        "input_parameters": event.input_parameters,
        "output_result": event.output_result,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "status": event.status or "SUCCESS",
        "error_message": event.error_message,
        "execution_time_ms": event.execution_time_ms,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def query_audit_events(
    db: Session,
    current_user: User,
    *,
    actor_type: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    query = (
        db.query(AuditLog)
        .outerjoin(User, AuditLog.actor_user_id == User.id)
        .filter(AuditLog.tenant_id == current_user.tenant_id)
    )
    if current_user.role == "Manager":
        query = query.filter(
            or_(
                User.department == current_user.department,
                AuditLog.agent_role == current_user.department,
            )
        )
    if actor_type:
        query = query.filter(AuditLog.actor_type == actor_type)
    if status:
        query = query.filter(AuditLog.status == status)
    if action:
        query = query.filter(
            or_(AuditLog.action.ilike(f"%{action}%"), AuditLog.tool_name.ilike(f"%{action}%"))
        )
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if date_from:
        query = query.filter(AuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(AuditLog.created_at <= date_to)
    total = query.count()
    events = (
        query.order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )
    return [serialize_audit_event(event) for event in events], total
