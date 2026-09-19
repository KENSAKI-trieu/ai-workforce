"""Workspace employee management with tenant-safe RBAC."""

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.permissions import LEGACY_ROLE_TO_SLUG
from app.core.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    validate_password,
)
from app.core.security import get_current_active_user, get_password_hash
from app.models.models import Department, Position, User
from app.services.position_service import (
    assert_no_privilege_escalation,
    assert_not_last_administrator,
    assign_position,
    ensure_tenant_positions,
    position_permissions,
)

router = APIRouter(prefix="/users-mgmt", tags=["User & Department Management"])

# What a caller may write. "Owner" is gone: the founder's role string is "CEO" now, and it
# is derived from the position rather than typed in.
UserRole = Literal["CEO", "Admin", "Manager", "Employee"]
# What a caller may filter by. Keeps "Owner" so a workspace with rows written before the
# rename stays searchable.
UserRoleFilter = Literal["CEO", "Owner", "Admin", "Manager", "Employee", "Guest"]
MANAGEMENT_ROLES = {"CEO", "Owner", "Admin"}


class CreateEmployeeRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    # Same floor as public registration. These two forms used to declare their own
    # limits and drifted apart; an account made here is no less of a way in.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    _check_password = field_validator("password")(validate_password)
    # The position is what actually grants access. `role` stays as a fallback for callers
    # that predate the org tree; the stored role string is derived from the position.
    position_id: Optional[UUID] = None
    role: UserRole = "Employee"
    department: str = Field(default="ALL", min_length=2, max_length=50, pattern="^[A-Z0-9_-]+$")


class UpdateUserStatusRequest(BaseModel):
    is_active: Optional[bool] = None
    position_id: Optional[UUID] = None
    role: Optional[UserRole] = None
    department: Optional[str] = Field(None, min_length=2, max_length=50, pattern="^[A-Z0-9_-]+$")


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "position_id": str(user.position_id) if user.position_id else None,
        "position_name": user.position.name if user.position else None,
        "department": user.department,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _resolve_position(
    db: Session,
    tenant_id,
    position_id: Optional[UUID],
    fallback_role: Optional[str],
) -> Optional[Position]:
    """The position a write should put the user in, by id or by legacy role name."""
    if position_id is not None:
        position = db.query(Position).filter(
            Position.id == position_id,
            Position.tenant_id == tenant_id,
        ).first()
        if position is None:
            raise HTTPException(status_code=404, detail="Position not found")
        if not position.is_active:
            raise HTTPException(
                status_code=422, detail="Không thể gán vào một chức vụ đã vô hiệu hoá"
            )
        return position
    if fallback_role is None:
        return None
    positions = ensure_tenant_positions(db, tenant_id)
    return positions.get(LEGACY_ROLE_TO_SLUG.get(fallback_role, "employee"))


def _department_exists(db: Session, tenant_id, code: str) -> bool:
    return code == "ALL" or db.query(Department).filter(
        Department.tenant_id == tenant_id,
        Department.code == code,
        Department.is_active.is_(True),
    ).first() is not None


@router.get("", summary="List employees visible to the current management role")
def get_organization_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: Optional[str] = Query(None, max_length=100),
    department: Optional[str] = Query(
        None, min_length=2, max_length=50, pattern="^[A-Z0-9_-]+$"
    ),
    role: Optional[UserRoleFilter] = Query(None),
    position_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.role not in MANAGEMENT_ROLES | {"Manager"}:
        raise HTTPException(status_code=403, detail="Insufficient permission to list employees")

    # The position is read for every row, so load it with the page rather than per user.
    query = db.query(User).options(joinedload(User.position)).filter(
        User.tenant_id == current_user.tenant_id
    )
    if current_user.role == "Manager":
        query = query.filter(User.department == current_user.department)
    if department:
        query = query.filter(User.department == department)
    if position_id:
        query = query.filter(User.position_id == position_id)
    if role:
        query = query.filter(User.role == role)
    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.filter(or_(
            User.full_name.ilike(search),
            User.email.ilike(search),
        ))

    total = query.count()
    users = query.order_by(User.created_at.desc(), User.id.asc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": [_serialize_user(user) for user in users],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


@router.post("", status_code=201, summary="Add an employee to the workspace")
def add_employee(
    req: CreateEmployeeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.role not in MANAGEMENT_ROLES:
        raise HTTPException(status_code=403, detail="Only CEO/Admin can add employees")
    if not _department_exists(db, current_user.tenant_id, req.department):
        raise HTTPException(status_code=422, detail="Department does not exist or is inactive")

    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=409, detail="Email is already in use")

    position = _resolve_position(db, current_user.tenant_id, req.position_id, req.role)
    if position is not None:
        # Handing someone a position hands them its permissions, so it is a grant like
        # any other and goes through the same escalation guard the org chart uses.
        assert_no_privilege_escalation(
            db, current_user, position_permissions(position)
        )

    new_user = User(
        tenant_id=current_user.tenant_id,
        email=req.email,
        full_name=req.full_name,
        password_hash=get_password_hash(req.password),
        role=req.role,
        department=req.department,
        is_active=True,
    )
    db.add(new_user)
    if position is not None:
        # Without this the account has no position at all, which reads as "no permissions"
        # everywhere the new guards look.
        db.flush()
        assign_position(db, new_user, position)
    db.commit()
    db.refresh(new_user)
    return {
        "message": "Employee created successfully",
        "user_id": str(new_user.id),
        "user": _serialize_user(new_user),
    }


@router.patch("/{user_id}/status", summary="Update an employee account")
def update_employee_status(
    user_id: UUID,
    req: UpdateUserStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.role not in MANAGEMENT_ROLES:
        raise HTTPException(status_code=403, detail="Only CEO/Admin can update employees")

    target_user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Employee not found")
    if current_user.role == "Admin" and target_user.role in {"CEO", "Owner"}:
        raise HTTPException(status_code=403, detail="Admin cannot modify a CEO account")
    if req.department and not _department_exists(
        db, current_user.tenant_id, req.department
    ):
        raise HTTPException(status_code=422, detail="Department does not exist or is inactive")
    if target_user.id == current_user.id and req.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot lock your own account")

    position = _resolve_position(db, current_user.tenant_id, req.position_id, req.role)
    if position is not None and position.id != target_user.position_id:
        assert_no_privilege_escalation(
            db, current_user, position_permissions(position)
        )

    demotes_ceo = target_user.role in {"CEO", "Owner"} and (
        req.is_active is False
        or (position is not None and not position.grants_all)
    )
    if demotes_ceo:
        active_ceo_count = db.query(User).filter(
            User.tenant_id == current_user.tenant_id,
            User.role.in_(("CEO", "Owner")),
            User.is_active.is_(True),
        ).count()
        if active_ceo_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="The workspace must keep at least one active CEO",
            )

    previous_position_id = target_user.position_id
    if req.is_active is not None:
        target_user.is_active = req.is_active
    if position is not None:
        # Goes through assign_position so the legacy role string and the derived manager
        # stay consistent with the position -- never set `target_user.role` by hand here.
        assign_position(db, target_user, position)
    if req.department is not None:
        target_user.department = req.department

    db.flush()
    if req.is_active is False or (
        position is not None and position.id != previous_position_id
    ):
        assert_not_last_administrator(db, current_user.tenant_id)

    db.commit()
    return {
        "message": "Employee updated successfully",
        "user": _serialize_user(target_user),
    }
