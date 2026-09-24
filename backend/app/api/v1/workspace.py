"""Workspace settings, data governance and dynamic department management."""

import ipaddress
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.security import get_current_active_user
from app.models.models import (
    AIAgent,
    AgentWorkflow,
    Department,
    IntegrationConnection,
    Task,
    Tenant,
    User,
)
from app.domains.platform.audit_events import add_audit_event
from app.domains.platform.cost_calculator import supported_model_names
from app.domains.platform.notification_service import create_notification

router = APIRouter(prefix="/workspace", tags=["Workspace Management"])
ADMIN_ROLES = {"Owner", "Admin", "CEO"}


class NotificationPolicyRequest(BaseModel):
    approval_notifications: bool = True
    task_notifications: bool = True
    cost_notifications: bool = True
    integration_notifications: bool = True


class SecurityPolicyRequest(BaseModel):
    mfa_required: bool = False
    session_timeout_minutes: int = Field(480, ge=15, le=10080)
    allowed_email_domains: list[str] = Field(default_factory=list, max_length=50)
    ip_allowlist: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("allowed_email_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            domain = value.strip().lower()
            if not domain or "." not in domain or "/" in domain or "@" in domain:
                raise ValueError(f"Invalid email domain: {value}")
            cleaned.append(domain)
        return list(dict.fromkeys(cleaned))

    @field_validator("ip_allowlist")
    @classmethod
    def validate_networks(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            try:
                result.append(str(ipaddress.ip_network(value.strip(), strict=False)))
            except ValueError as exc:
                raise ValueError(f"Invalid IP/CIDR: {value}") from exc
        return list(dict.fromkeys(result))


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    logo_url: Optional[HttpUrl] = None
    timezone: Optional[str] = Field(None, max_length=100)
    language: Optional[str] = Field(None, pattern="^(vi|en)$")
    data_retention_days: Optional[int] = Field(None, ge=30, le=3650)
    default_model: Optional[str] = Field(None, max_length=100)
    billing_email: Optional[EmailStr] = None
    notification_settings: Optional[NotificationPolicyRequest] = None
    security_settings: Optional[SecurityPolicyRequest] = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("default_model")
    @classmethod
    def validate_model(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in supported_model_names():
            raise ValueError("Default model has no supported pricing/configuration")
        return value


class DeletionRequest(BaseModel):
    confirmation_domain: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=10, max_length=2000)


class DepartmentCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern="^[A-Z0-9_-]+$")
    name: str = Field(min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class DepartmentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    is_active: Optional[bool] = None


def _require_admin(current_user: User) -> None:
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only Owner/Admin can manage the workspace")


def _serialize_department(department: Department, member_count: int = 0) -> dict:
    return {
        "id": str(department.id),
        "code": department.code,
        "name": department.name,
        "description": department.description,
        "is_active": department.is_active,
        "member_count": member_count,
        "created_at": department.created_at.isoformat() if department.created_at else None,
    }


@router.get("", summary="Get current workspace")
def get_workspace(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "domain": tenant.domain,
        "logo_url": tenant.logo_url,
        "timezone": tenant.timezone,
        "language": tenant.language,
        "data_retention_days": tenant.data_retention_days,
        "default_model": tenant.default_model,
        "billing_email": tenant.billing_email,
        "notification_settings": tenant.notification_settings or {},
        "security_settings": tenant.security_settings or {},
        "supported_models": sorted(supported_model_names()),
        "api_key_status": {
            "OPENAI_API_KEY": bool(settings.OPENAI_API_KEY),
            "ANTHROPIC_API_KEY": bool(settings.ANTHROPIC_API_KEY),
            "GOOGLE_AI_API_KEY": bool(settings.GOOGLE_AI_API_KEY),
        },
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        "current_user_role": current_user.role,
    }


@router.patch("", summary="Update current workspace")
def update_workspace(
    req: WorkspaceUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    before = {
        "name": tenant.name,
        "logo_url": tenant.logo_url,
        "timezone": tenant.timezone,
        "language": tenant.language,
        "data_retention_days": tenant.data_retention_days,
        "default_model": tenant.default_model,
        "billing_email": tenant.billing_email,
        "notification_settings": tenant.notification_settings or {},
        "security_settings": tenant.security_settings or {},
    }
    data = req.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = data["name"].strip()
    if "logo_url" in data and data["logo_url"] is not None:
        data["logo_url"] = str(data["logo_url"])
    if "billing_email" in data and data["billing_email"] is not None:
        data["billing_email"] = str(data["billing_email"])
    for key in ("notification_settings", "security_settings"):
        if key in data and isinstance(data[key], BaseModel):
            data[key] = data[key].model_dump()
    for key, value in data.items():
        setattr(tenant, key, value)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="workspace.settings_updated",
        resource_type="WORKSPACE",
        resource_id=str(tenant.id),
        before_data=before,
        after_data=data,
        request=request,
    )
    db.commit()
    return {"message": "Workspace updated successfully", "updated_fields": sorted(data)}


@router.get("/export", summary="Export a sanitized workspace snapshot")
def export_workspace(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    departments = db.query(Department).filter(
        Department.tenant_id == tenant.id
    ).all()
    users = db.query(User).filter(User.tenant_id == tenant.id).all()
    agents = db.query(AIAgent).filter(AIAgent.tenant_id == tenant.id).all()
    tasks = db.query(Task).filter(Task.tenant_id == tenant.id).all()
    workflows = db.query(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == tenant.id
    ).all()
    integrations = db.query(IntegrationConnection).filter(
        IntegrationConnection.tenant_id == tenant.id
    ).all()
    snapshot = {
        "format": "ai-workforce-workspace-export-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": {
            "id": str(tenant.id),
            "name": tenant.name,
            "domain": tenant.domain,
            "timezone": tenant.timezone,
            "language": tenant.language,
            "data_retention_days": tenant.data_retention_days,
            "default_model": tenant.default_model,
        },
        "departments": [
            {
                "id": str(item.id),
                "code": item.code,
                "name": item.name,
                "description": item.description,
                "is_active": item.is_active,
            }
            for item in departments
        ],
        "members": [
            {
                "id": str(item.id),
                "email": item.email,
                "full_name": item.full_name,
                "role": item.role,
                "department": item.department,
                "is_active": item.is_active,
            }
            for item in users
        ],
        "agents": [
            {
                "id": str(item.id),
                "name": item.name,
                "role_code": item.role_code,
                "model_name": item.model_name,
                "is_active": item.is_active,
                "tools_access": item.tools_access or [],
                "allowed_actions": item.allowed_actions or [],
                "disallowed_actions": item.disallowed_actions or [],
            }
            for item in agents
        ],
        "tasks": [
            {
                "id": str(item.id),
                "title": item.title,
                "status": item.status,
                "priority": item.priority,
                "creator_id": str(item.creator_id),
                "assignee_id": str(item.assignee_id) if item.assignee_id else None,
                "ai_agent_id": str(item.ai_agent_id) if item.ai_agent_id else None,
                "due_date": item.due_date.isoformat() if item.due_date else None,
            }
            for item in tasks
        ],
        "workflows": [
            {
                "id": str(item.id),
                "title": item.title,
                "status": item.status,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in workflows
        ],
        "integrations": [
            {
                "id": str(item.id),
                "provider": item.provider,
                "display_name": item.display_name,
                "status": item.status,
                "permissions": item.permissions or [],
                "allowed_resources": item.allowed_resources or [],
                "allowed_agent_roles": item.allowed_agent_roles or [],
            }
            for item in integrations
        ],
    }
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        action="workspace.data_exported",
        resource_type="WORKSPACE",
        resource_id=str(tenant.id),
        output_result={
            "members": len(users),
            "tasks": len(tasks),
            "workflows": len(workflows),
        },
        request=request,
    )
    db.commit()
    return snapshot


@router.post("/data-deletion-request", status_code=202, summary="Request reviewed workspace deletion")
def request_workspace_deletion(
    req: DeletionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # "Owner" is still accepted so a workspace whose rows predate the rename is not locked
    # out of its own deletion request; new founders are written as "CEO".
    if current_user.role not in {"CEO", "Owner"}:
        raise HTTPException(status_code=403, detail="Only the workspace CEO can request deletion")
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if req.confirmation_domain.strip().lower() != tenant.domain.lower():
        raise HTTPException(status_code=422, detail="Confirmation domain does not match")
    add_audit_event(
        db,
        tenant_id=tenant.id,
        actor_user=current_user,
        action="workspace.deletion_requested",
        resource_type="WORKSPACE",
        resource_id=str(tenant.id),
        input_parameters={"reason": req.reason},
        request=request,
        status="PENDING",
    )
    owners = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role.in_(("CEO", "Owner", "Admin")),
        User.is_active.is_(True),
    ).all()
    for owner in owners:
        create_notification(
            db,
            user=owner,
            event_type="APPROVAL_REQUIRED",
            title="Yêu cầu xóa dữ liệu công ty",
            message=(
                f"{current_user.full_name} đã gửi yêu cầu xóa workspace. "
                "Yêu cầu đang chờ quy trình xác minh và export cuối cùng."
            ),
            severity="ERROR",
            entity_type="WORKSPACE",
            entity_id=str(tenant.id),
            dedup_key=f"workspace-deletion:{tenant.id}",
        )
    db.commit()
    return {
        "status": "PENDING_REVIEW",
        "message": "Deletion request recorded. No data has been deleted.",
    }


@router.get("/departments", summary="List workspace departments")
def list_departments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    departments = db.query(Department).filter(
        Department.tenant_id == current_user.tenant_id
    ).order_by(Department.name).all()
    counts = {
        department.code: db.query(User).filter(
            User.tenant_id == current_user.tenant_id,
            User.department == department.code,
        ).count()
        for department in departments
    }
    return [
        _serialize_department(department, counts[department.code])
        for department in departments
    ]


@router.post("/departments", status_code=201, summary="Create a department")
def create_department(
    req: DepartmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    if db.query(Department).filter(
        Department.tenant_id == current_user.tenant_id,
        Department.code == req.code,
    ).first():
        raise HTTPException(status_code=409, detail="Department code already exists")
    department = Department(
        tenant_id=current_user.tenant_id,
        code=req.code,
        name=req.name.strip(),
        description=req.description,
    )
    db.add(department)
    db.commit()
    db.refresh(department)
    return _serialize_department(department)


@router.patch("/departments/{department_id}", summary="Update a department")
def update_department(
    department_id: UUID,
    req: DepartmentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    department = db.query(Department).filter(
        Department.id == department_id,
        Department.tenant_id == current_user.tenant_id,
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    for field_name, value in req.model_dump(exclude_unset=True).items():
        setattr(department, field_name, value)
    db.commit()
    return _serialize_department(department)


@router.delete("/departments/{department_id}", summary="Delete an unused department")
def delete_department(
    department_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    department = db.query(Department).filter(
        Department.id == department_id,
        Department.tenant_id == current_user.tenant_id,
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    in_use = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.department == department.code,
    ).count()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Department still has {in_use} member(s); reassign them first",
        )
    db.delete(department)
    db.commit()
    return {"message": "Department deleted successfully"}
