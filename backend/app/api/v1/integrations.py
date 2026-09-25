"""Tenant-scoped enterprise integration registry and access controls."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    IntegrationConnection,
    IntegrationUsageLog,
    User,
)
from app.domains.platform.audit_events import add_audit_event
from app.domains.platform.notification_service import create_notification

router = APIRouter(prefix="/integrations", tags=["Enterprise Integrations"])
ADMIN_ROLES = {"Owner", "Admin", "CEO"}

CATALOG: dict[str, dict[str, Any]] = {
    "GMAIL": {"name": "Gmail", "category": "Email", "auth_types": ["OAUTH2"]},
    "OUTLOOK": {"name": "Outlook", "category": "Email", "auth_types": ["OAUTH2"]},
    "GOOGLE_CALENDAR": {"name": "Google Calendar", "category": "Calendar", "auth_types": ["OAUTH2"]},
    "SLACK": {"name": "Slack", "category": "Communication", "auth_types": ["OAUTH2", "TOKEN"]},
    "TEAMS": {"name": "Microsoft Teams", "category": "Communication", "auth_types": ["OAUTH2"]},
    "GOOGLE_DRIVE": {"name": "Google Drive", "category": "Storage", "auth_types": ["OAUTH2"]},
    "SHAREPOINT": {"name": "SharePoint", "category": "Storage", "auth_types": ["OAUTH2"]},
    "CRM": {"name": "CRM", "category": "Business", "auth_types": ["OAUTH2", "TOKEN"]},
    "TRELLO": {"name": "Trello", "category": "Project", "auth_types": ["OAUTH2", "TOKEN"]},
    "JIRA": {"name": "Jira", "category": "Project", "auth_types": ["OAUTH2", "TOKEN"]},
    "NOTION": {"name": "Notion", "category": "Knowledge", "auth_types": ["OAUTH2", "TOKEN"]},
    "POSTGRESQL": {"name": "PostgreSQL", "category": "Database", "auth_types": ["CONNECTION_REF"]},
    "WEBHOOK": {"name": "Webhook", "category": "Developer", "auth_types": ["SECRET_REF"]},
    "REST_API": {"name": "REST API", "category": "Developer", "auth_types": ["OAUTH2", "TOKEN", "SECRET_REF"]},
}


def _require_admin(user: User) -> None:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Integration management requires Owner/Admin")


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(marker in key.lower() for marker in ("secret", "password", "token", "api_key", "authorization")):
                return True
            if _contains_sensitive_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class IntegrationCreateRequest(BaseModel):
    provider: str
    display_name: str = Field(min_length=2, max_length=150)
    auth_type: str
    credential_reference: str = Field(
        pattern=r"^(env:[A-Z][A-Z0-9_]*|vault://[A-Za-z0-9_./-]+)$",
        max_length=255,
    )
    permissions: list[str] = Field(min_length=1, max_length=30)
    allowed_resources: list[str] = Field(min_length=1, max_length=100)
    allowed_agent_roles: list[str] = Field(default_factory=list, max_length=30)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "auth_type")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("permissions", "allowed_resources", "allowed_agent_roles")
    @classmethod
    def forbid_wildcards(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(value in {"*", "ALL"} for value in cleaned):
            raise ValueError("Wildcard access is not allowed")
        return list(dict.fromkeys(cleaned))

    @field_validator("configuration")
    @classmethod
    def forbid_plaintext_credentials(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_sensitive_key(value):
            raise ValueError("Store credentials in env/vault and submit only a reference")
        return value


class IntegrationUpdateRequest(BaseModel):
    permissions: Optional[list[str]] = None
    allowed_resources: Optional[list[str]] = None
    allowed_agent_roles: Optional[list[str]] = None
    configuration: Optional[dict[str, Any]] = None

    @field_validator("permissions", "allowed_resources", "allowed_agent_roles")
    @classmethod
    def forbid_wildcards(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return values
        cleaned = [value.strip() for value in values if value.strip()]
        if any(value in {"*", "ALL"} for value in cleaned):
            raise ValueError("Wildcard access is not allowed")
        return list(dict.fromkeys(cleaned))

    @field_validator("configuration")
    @classmethod
    def forbid_plaintext_credentials(
        cls, value: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        if value is not None and _contains_sensitive_key(value):
            raise ValueError("Plaintext credentials are not accepted")
        return value


def _serialize(connection: IntegrationConnection) -> dict[str, Any]:
    return {
        "id": str(connection.id),
        "provider": connection.provider,
        "provider_name": CATALOG.get(connection.provider, {}).get("name", connection.provider),
        "display_name": connection.display_name,
        "auth_type": connection.auth_type,
        "credential_configured": bool(connection.credential_reference),
        "permissions": connection.permissions or [],
        "allowed_resources": connection.allowed_resources or [],
        "allowed_agent_roles": connection.allowed_agent_roles or [],
        "configuration": connection.configuration or {},
        "status": connection.status,
        "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
        "last_checked_at": connection.last_checked_at.isoformat() if connection.last_checked_at else None,
        "last_error": connection.last_error,
        "created_by": connection.created_by.full_name if connection.created_by else None,
        "created_at": connection.created_at.isoformat() if connection.created_at else None,
    }


def _get_connection(db: Session, user: User, connection_id: UUID) -> IntegrationConnection:
    connection = db.query(IntegrationConnection).filter(
        IntegrationConnection.id == connection_id,
        IntegrationConnection.tenant_id == user.tenant_id,
    ).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Integration not found")
    return connection


@router.get("/catalog", summary="List supported enterprise integrations")
def get_integration_catalog(
    current_user: User = Depends(get_current_active_user),
):
    del current_user
    return [
        {"provider": provider, **details}
        for provider, details in CATALOG.items()
    ]


@router.get("", summary="List workspace integration connections")
def list_integrations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    connections = db.query(IntegrationConnection).filter(
        IntegrationConnection.tenant_id == current_user.tenant_id
    ).order_by(IntegrationConnection.provider, IntegrationConnection.display_name).all()
    return [_serialize(connection) for connection in connections]


@router.post("", status_code=201, summary="Register a least-privilege connection")
def create_integration(
    req: IntegrationCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    catalog_entry = CATALOG.get(req.provider)
    if not catalog_entry:
        raise HTTPException(status_code=422, detail="Unsupported integration provider")
    if req.auth_type not in catalog_entry["auth_types"]:
        raise HTTPException(status_code=422, detail="Unsupported auth type for provider")
    duplicate = db.query(IntegrationConnection).filter(
        IntegrationConnection.tenant_id == current_user.tenant_id,
        IntegrationConnection.provider == req.provider,
        IntegrationConnection.display_name == req.display_name.strip(),
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="Integration name already exists")
    connection = IntegrationConnection(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        provider=req.provider,
        display_name=req.display_name.strip(),
        auth_type=req.auth_type,
        credential_reference=req.credential_reference,
        permissions=req.permissions,
        allowed_resources=req.allowed_resources,
        allowed_agent_roles=req.allowed_agent_roles,
        configuration=req.configuration,
        status="CONFIGURED",
        created_by_id=current_user.id,
    )
    db.add(connection)
    db.flush()
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="integration.created",
        resource_type="INTEGRATION",
        resource_id=str(connection.id),
        after_data=_serialize(connection),
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return _serialize(connection)


@router.patch("/{connection_id}", summary="Update integration access controls")
def update_integration(
    connection_id: UUID,
    req: IntegrationUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    connection = _get_connection(db, current_user, connection_id)
    before = _serialize(connection)
    data = req.model_dump(exclude_unset=True)
    if "permissions" in data and not data["permissions"]:
        raise HTTPException(status_code=422, detail="At least one permission is required")
    if "allowed_resources" in data and not data["allowed_resources"]:
        raise HTTPException(status_code=422, detail="At least one resource is required")
    for key, value in data.items():
        setattr(connection, key, value)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="integration.access_updated",
        resource_type="INTEGRATION",
        resource_id=str(connection.id),
        before_data=before,
        after_data=_serialize(connection),
        request=request,
    )
    db.commit()
    return _serialize(connection)


@router.post("/{connection_id}/test", summary="Validate connection configuration")
def test_integration(
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    connection = _get_connection(db, current_user, connection_id)
    now = datetime.now(timezone.utc)
    valid = bool(
        connection.credential_reference
        and connection.permissions
        and connection.allowed_resources
    )
    connection.last_checked_at = now
    connection.status = "CONNECTED" if valid else "ERROR"
    connection.connected_at = connection.connected_at or (now if valid else None)
    connection.last_error = None if valid else "Missing credential reference or access scope"
    usage = IntegrationUsageLog(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        connection_id=connection.id,
        actor_user_id=current_user.id,
        operation="CONFIGURATION_TEST",
        status="SUCCESS" if valid else "FAILED",
        execution_time_ms=0,
        metadata_={"mode": "CONFIGURATION_VALIDATION"},
        error_message=connection.last_error,
    )
    db.add(usage)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="integration.tested",
        resource_type="INTEGRATION",
        resource_id=str(connection.id),
        after_data={"status": connection.status, "mode": "CONFIGURATION_VALIDATION"},
        status="SUCCESS" if valid else "FAILED",
        request=request,
    )
    db.commit()
    return {
        "success": valid,
        "status": connection.status,
        "mode": "CONFIGURATION_VALIDATION",
        "message": (
            "Configuration and least-privilege scopes are valid. Provider OAuth/API "
            "handshake runs only when a connector worker is configured."
            if valid
            else connection.last_error
        ),
    }


@router.post("/{connection_id}/disconnect", summary="Disconnect an integration")
def disconnect_integration(
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    connection = _get_connection(db, current_user, connection_id)
    before_status = connection.status
    connection.status = "DISCONNECTED"
    connection.last_error = "Disconnected by administrator"
    create_notification(
        db,
        user=current_user,
        event_type="INTEGRATION_DISCONNECTED",
        title="Integration đã ngắt kết nối",
        message=connection.display_name,
        severity="WARNING",
        entity_type="INTEGRATION",
        entity_id=str(connection.id),
        dedup_key=f"integration-disconnected:{connection.id}:{datetime.now(timezone.utc).date()}",
    )
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="integration.disconnected",
        resource_type="INTEGRATION",
        resource_id=str(connection.id),
        before_data={"status": before_status},
        after_data={"status": connection.status},
        request=request,
    )
    db.commit()
    return _serialize(connection)


@router.get("/{connection_id}/activity", summary="View integration usage history")
def integration_activity(
    connection_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    connection = _get_connection(db, current_user, connection_id)
    logs = db.query(IntegrationUsageLog).filter(
        IntegrationUsageLog.tenant_id == current_user.tenant_id,
        IntegrationUsageLog.connection_id == connection.id,
    ).order_by(IntegrationUsageLog.created_at.desc()).limit(100).all()
    return [
        {
            "id": str(item.id),
            "operation": item.operation,
            "agent_role": item.agent_role,
            "resource": item.resource,
            "status": item.status,
            "execution_time_ms": item.execution_time_ms,
            "metadata": item.metadata_ or {},
            "error_message": item.error_message,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in logs
    ]
