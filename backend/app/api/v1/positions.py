"""Company-defined position tree: the org chart a tenant builds for itself.

Every mutation here goes through app.services.position_service, which owns the four
invariants that keep a company from locking itself out. Routers must not re-implement
them.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import permission_catalog
from app.core.security import PermissionRequired, get_current_active_user
from app.models.models import Position, User
from app.services.audit_events import add_audit_event
from app.services.position_service import (
    assert_no_cycle,
    assert_no_privilege_escalation,
    assert_not_last_administrator,
    assign_position,
    descendant_ids,
    ensure_tenant_positions,
    position_permissions,
    unique_slug,
    user_permissions,
)

router = APIRouter(prefix="/positions", tags=["Org Structure"])


class PositionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_id: Optional[uuid.UUID] = None
    permissions: list[str] = Field(default_factory=list)
    default_department: Optional[str] = Field(None, max_length=50)


class PositionUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    parent_id: Optional[uuid.UUID] = None
    permissions: Optional[list[str]] = None
    default_department: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None


class AssignPositionRequest(BaseModel):
    position_id: uuid.UUID
    manager_id: Optional[uuid.UUID] = None
    manager_is_manual: bool = False


def _get_position(db: Session, tenant_id: uuid.UUID, position_id: uuid.UUID) -> Position:
    position = db.query(Position).filter(
        Position.id == position_id,
        Position.tenant_id == tenant_id,
    ).first()
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


def _serialize(position: Position, holder_count: int) -> dict:
    return {
        "id": str(position.id),
        "parent_id": str(position.parent_id) if position.parent_id else None,
        "name": position.name,
        "slug": position.slug,
        "permissions": sorted(position_permissions(position)),
        "grants_all": position.grants_all,
        "default_department": position.default_department,
        "is_active": position.is_active,
        "sort_order": position.sort_order,
        "holder_count": holder_count,
    }


@router.get("", summary="Cây chức vụ của công ty")
def list_positions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Readable by anyone signed in: an org chart is not privileged information."""
    positions = ensure_tenant_positions(db, current_user.tenant_id)
    db.commit()
    rows = sorted(positions.values(), key=lambda item: (item.sort_order, item.name))
    counts: dict[uuid.UUID, int] = {}
    for position_id, in db.query(User.position_id).filter(
        User.tenant_id == current_user.tenant_id,
        User.position_id.isnot(None),
    ).all():
        counts[position_id] = counts.get(position_id, 0) + 1
    return {
        "positions": [_serialize(item, counts.get(item.id, 0)) for item in rows],
        "my_permissions": sorted(user_permissions(db, current_user)),
    }


@router.get("/permissions", summary="Danh mục quyền có thể cấp")
def list_permission_catalog(
    current_user: User = Depends(get_current_active_user),
) -> dict:
    return {"permissions": permission_catalog()}


@router.post(
    "",
    status_code=201,
    summary="Tạo chức vụ mới",
    dependencies=[Depends(PermissionRequired("org.structure.manage"))],
)
def create_position(
    payload: PositionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    ensure_tenant_positions(db, current_user.tenant_id)
    parent = (
        _get_position(db, current_user.tenant_id, payload.parent_id)
        if payload.parent_id
        else None
    )
    granted = assert_no_privilege_escalation(db, current_user, payload.permissions)

    position = Position(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        parent_id=parent.id if parent else None,
        name=payload.name.strip(),
        # Generated once from the name it was created with, then never touched again, so
        # a later rename cannot invalidate anything that stored this slug.
        slug=unique_slug(db, current_user.tenant_id, payload.name),
        permissions=granted,
        grants_all=False,
        default_department=payload.default_department,
        sort_order=1000,
    )
    db.add(position)
    db.flush()
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        actor_type="USER",
        agent_role="SYSTEM",
        action="org.position.create",
        tool_name="positions",
        resource_type="POSITION",
        resource_id=str(position.id),
        input_parameters={"name": position.name, "permissions": granted},
        output_result={"slug": position.slug},
        status="SUCCESS",
    )
    db.commit()
    return _serialize(position, 0)


@router.patch(
    "/{position_id}",
    summary="Đổi tên, đổi quyền hoặc đổi cấp trên của chức vụ",
    dependencies=[Depends(PermissionRequired("org.structure.manage"))],
)
def update_position(
    position_id: uuid.UUID,
    payload: PositionUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    position = _get_position(db, current_user.tenant_id, position_id)
    changes: dict = {}

    if payload.name is not None:
        # Only the display name moves. The slug stays put, which is what keeps document
        # ACLs and anything else holding the old string working after a rename.
        position.name = payload.name.strip()
        changes["name"] = position.name

    if payload.parent_id is not None:
        parent = _get_position(db, current_user.tenant_id, payload.parent_id)
        assert_no_cycle(db, position, parent)
        position.parent_id = parent.id
        changes["parent_id"] = str(parent.id)

    if payload.permissions is not None:
        if position.grants_all:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Chức vụ gốc luôn có toàn bộ quyền và không thể chỉnh sửa danh sách "
                    "quyền. Hãy tạo một chức vụ khác nếu cần giới hạn."
                ),
            )
        granted = assert_no_privilege_escalation(db, current_user, payload.permissions)
        assert_not_last_administrator(
            db,
            current_user.tenant_id,
            ignore_position_id=position.id,
            replacement_permissions=frozenset(granted),
        )
        position.permissions = granted
        changes["permissions"] = granted

    if payload.default_department is not None:
        position.default_department = payload.default_department
        changes["default_department"] = payload.default_department

    if payload.is_active is not None:
        if not payload.is_active:
            if position.grants_all:
                raise HTTPException(
                    status_code=422, detail="Không thể vô hiệu hoá chức vụ gốc"
                )
            assert_not_last_administrator(
                db,
                current_user.tenant_id,
                ignore_position_id=position.id,
                replacement_permissions=frozenset(),
            )
        position.is_active = payload.is_active
        changes["is_active"] = payload.is_active

    db.flush()
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        actor_type="USER",
        agent_role="SYSTEM",
        action="org.position.update",
        tool_name="positions",
        resource_type="POSITION",
        resource_id=str(position.id),
        input_parameters=changes,
        output_result={"slug": position.slug},
        status="SUCCESS",
    )
    db.commit()
    holders = db.query(User).filter(User.position_id == position.id).count()
    return _serialize(position, holders)


@router.delete(
    "/{position_id}",
    summary="Xoá chức vụ",
    dependencies=[Depends(PermissionRequired("org.structure.manage"))],
)
def delete_position(
    position_id: uuid.UUID,
    reassign_to: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    position = _get_position(db, current_user.tenant_id, position_id)
    if position.grants_all:
        raise HTTPException(status_code=422, detail="Không thể xoá chức vụ gốc")

    children = db.query(Position).filter(Position.parent_id == position.id).count()
    if children:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Chức vụ này còn {children} chức vụ cấp dưới. "
                "Hãy chuyển hoặc xoá chúng trước."
            ),
        )

    holders = db.query(User).filter(User.position_id == position.id).all()
    if holders:
        if reassign_to is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Chức vụ này còn {len(holders)} người đang giữ. "
                    "Hãy chỉ định chức vụ thay thế qua tham số reassign_to."
                ),
            )
        replacement = _get_position(db, current_user.tenant_id, reassign_to)
        if replacement.id == position.id:
            raise HTTPException(
                status_code=422, detail="Chức vụ thay thế phải khác chức vụ bị xoá"
            )
        for holder in holders:
            assign_position(db, holder, replacement)
        db.flush()

    assert_not_last_administrator(db, current_user.tenant_id)
    db.delete(position)
    db.flush()
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        actor_type="USER",
        agent_role="SYSTEM",
        action="org.position.delete",
        tool_name="positions",
        resource_type="POSITION",
        resource_id=str(position_id),
        input_parameters={
            "slug": position.slug,
            "reassigned": len(holders),
            "reassign_to": str(reassign_to) if reassign_to else None,
        },
        output_result={"deleted": True},
        status="SUCCESS",
    )
    db.commit()
    return {"deleted": True, "reassigned": len(holders)}


@router.put(
    "/assign/{user_id}",
    summary="Gán nhân sự vào một chức vụ",
    dependencies=[Depends(PermissionRequired("users.position.assign"))],
)
def assign_user_position(
    user_id: uuid.UUID,
    payload: AssignPositionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    target = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    position = _get_position(db, current_user.tenant_id, payload.position_id)
    if not position.is_active:
        raise HTTPException(
            status_code=422, detail="Không thể gán vào một chức vụ đã vô hiệu hoá"
        )
    # Assigning a position hands over its permissions, so it is a grant like any other.
    assert_no_privilege_escalation(db, current_user, position_permissions(position))

    previous_position_id = target.position_id

    if payload.manager_id is not None:
        if payload.manager_id == target.id:
            raise HTTPException(
                status_code=422, detail="Một người không thể là cấp trên của chính mình"
            )
        manager = db.query(User).filter(
            User.id == payload.manager_id,
            User.tenant_id == current_user.tenant_id,
        ).first()
        if manager is None:
            raise HTTPException(status_code=404, detail="Manager not found")
        target.manager_id = manager.id
        target.manager_is_manual = True
        assign_position(db, target, position, update_manager=False)
    else:
        target.manager_is_manual = payload.manager_is_manual
        assign_position(db, target, position)

    db.flush()
    if previous_position_id is not None and previous_position_id != position.id:
        assert_not_last_administrator(db, current_user.tenant_id)

    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        actor_type="USER",
        agent_role="SYSTEM",
        action="org.position.assign",
        tool_name="positions",
        resource_type="USER",
        resource_id=str(target.id),
        input_parameters={
            "position_id": str(position.id),
            "position_slug": position.slug,
        },
        output_result={"manager_id": str(target.manager_id) if target.manager_id else None},
        status="SUCCESS",
    )
    db.commit()
    return {
        "user_id": str(target.id),
        "position_id": str(position.id),
        "position_name": position.name,
        "manager_id": str(target.manager_id) if target.manager_id else None,
        "manager_is_manual": target.manager_is_manual,
    }


@router.get("/{position_id}/holders", summary="Những người đang giữ chức vụ này")
def list_position_holders(
    position_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    position = _get_position(db, current_user.tenant_id, position_id)
    holders = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.position_id == position.id,
    ).order_by(User.full_name).all()
    return {
        "position_id": str(position.id),
        "position_name": position.name,
        "descendant_count": len(descendant_ids(db, position)) - 1,
        "holders": [
            {
                "id": str(holder.id),
                "full_name": holder.full_name,
                "email": holder.email,
                "department": holder.department,
                "is_active": holder.is_active,
            }
            for holder in holders
        ],
    }
