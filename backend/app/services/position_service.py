"""Seed, evaluate and safely mutate a tenant's position tree.

Every invariant that keeps a company from locking itself out lives here rather than in the
routers, so there is one place to audit: cycle prevention, the last-administrator guard,
and the rule that nobody may grant a permission they do not themselves hold.
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session, object_session

from app.core.permissions import (
    DEFAULT_POSITIONS,
    LEGACY_ROLE_TO_SLUG,
    PERMISSION_CODES,
    ROOT_POSITION_SLUG,
    SELF_LOCKOUT_GUARD_PERMISSIONS,
    normalize_permissions,
)
from app.models.models import Position, User

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_VIETNAMESE_FOLD = str.maketrans({
    "đ": "d", "Đ": "d",
})


def _slugify(name: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFD", str(name).lower().translate(_VIETNAMESE_FOLD))
    ascii_only = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug[:70] or "chuc-vu"


def unique_slug(db: Session, tenant_id: uuid.UUID, name: str) -> str:
    """A slug that is free within this tenant. Generated once; never regenerated."""
    base = _slugify(name)
    taken = {
        row[0]
        for row in db.query(Position.slug).filter(Position.tenant_id == tenant_id).all()
    }
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def position_permissions(position: Position | None) -> frozenset[str]:
    """What a position grants. The root grants everything this build defines."""
    if position is None or not position.is_active:
        return frozenset()
    if position.grants_all:
        return PERMISSION_CODES
    return frozenset(normalize_permissions(position.permissions))


def user_permissions(db: Session | None, user: User) -> frozenset[str]:
    """Permissions the user holds through their assigned position.

    `db` may be omitted when the caller has no session at hand; the user's own session is
    recovered instead. A user with no position at all holds nothing, but a user whose
    position cannot be loaded raises rather than returning an empty set: silently
    answering "no permissions" would read as a denial everywhere and be very hard to
    trace back to a detached object.
    """
    if user.position_id is None:
        return frozenset()
    position = user.position
    if position is None:
        session = db or object_session(user)
        if session is None:
            raise RuntimeError(
                "Cannot resolve permissions: the user is detached from its session and "
                "no session was supplied."
            )
        position = session.query(Position).filter(
            Position.id == user.position_id,
            Position.tenant_id == user.tenant_id,
        ).first()
    return position_permissions(position)


def has_permission(db: Session | None, user: User, code: str) -> bool:
    return code in user_permissions(db, user)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def ensure_tenant_positions(db: Session, tenant_id: uuid.UUID) -> dict[str, Position]:
    """Create the default tree for a tenant if it is missing. Idempotent.

    Returns every position of the tenant keyed by slug, so callers can assign users
    without a second query.
    """
    existing = {
        position.slug: position
        for position in db.query(Position).filter(Position.tenant_id == tenant_id).all()
    }
    created = False
    for index, default in enumerate(DEFAULT_POSITIONS):
        if default.slug in existing:
            continue
        parent = existing.get(default.parent_slug) if default.parent_slug else None
        if default.parent_slug and parent is None:
            # Parents are declared before children in DEFAULT_POSITIONS, so this only
            # happens if that ordering is broken.
            raise RuntimeError(
                f"Default position '{default.slug}' declared before its parent"
            )
        position = Position(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            parent_id=parent.id if parent else None,
            name=default.name,
            slug=default.slug,
            permissions=normalize_permissions(default.permissions),
            grants_all=default.grants_all,
            sort_order=index,
        )
        db.add(position)
        db.flush()
        existing[default.slug] = position
        created = True
    if created:
        db.flush()
    return existing


def backfill_tenant_user_positions(db: Session, tenant_id: uuid.UUID) -> int:
    """Give every position-less user of a tenant the position matching their legacy role.

    Idempotent: users that already have a position are left alone, so running this twice
    produces the same result as running it once.
    """
    positions = ensure_tenant_positions(db, tenant_id)
    fallback = positions.get(LEGACY_ROLE_TO_SLUG.get("Employee", "employee"))
    users = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.position_id.is_(None),
    ).all()
    assigned = 0
    for user in users:
        slug = LEGACY_ROLE_TO_SLUG.get(user.role)
        position = positions.get(slug) if slug else None
        if position is None:
            position = fallback
        if position is None:
            continue
        user.position_id = position.id
        assigned += 1
    if assigned:
        db.flush()
    return assigned


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------
def descendant_ids(db: Session, position: Position) -> set[uuid.UUID]:
    """Every position under this one, itself included."""
    rows = db.query(Position.id, Position.parent_id).filter(
        Position.tenant_id == position.tenant_id
    ).all()
    children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for row_id, parent_id in rows:
        children.setdefault(parent_id, []).append(row_id)
    found = {position.id}
    frontier = [position.id]
    while frontier:
        current = frontier.pop()
        for child in children.get(current, ()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


def assert_no_cycle(db: Session, position: Position, new_parent: Position | None) -> None:
    """A position may not be re-parented under itself or any of its own descendants."""
    if new_parent is None:
        return
    if new_parent.tenant_id != position.tenant_id:
        raise HTTPException(status_code=404, detail="Position not found")
    if new_parent.id == position.id:
        raise HTTPException(
            status_code=422, detail="Một chức vụ không thể là cấp trên của chính nó"
        )
    if new_parent.id in descendant_ids(db, position):
        raise HTTPException(
            status_code=422,
            detail="Không thể đặt cấp trên là một chức vụ nằm dưới chức vụ này",
        )


def assert_no_privilege_escalation(
    db: Session, actor: User, requested: Iterable[str]
) -> list[str]:
    """Nobody may grant a permission they do not hold themselves."""
    codes = normalize_permissions(requested)
    held = user_permissions(db, actor)
    excess = sorted(set(codes) - held)
    if excess:
        raise HTTPException(
            status_code=403,
            detail=(
                "Bạn không thể cấp quyền mà chính bạn không có: " + ", ".join(excess)
            ),
        )
    return codes


def _administrator_count(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    ignore_user_id: uuid.UUID | None = None,
    ignore_position_id: uuid.UUID | None = None,
    replacement_permissions: frozenset[str] | None = None,
) -> int:
    """Active users still holding every self-administration permission.

    `ignore_position_id` with `replacement_permissions` models "what if this position's
    permissions became that", so a change can be checked before it is written.
    """
    positions = {
        position.id: position
        for position in db.query(Position).filter(Position.tenant_id == tenant_id).all()
    }
    users = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_active.is_(True),
        User.position_id.isnot(None),
    ).all()
    count = 0
    for user in users:
        if ignore_user_id is not None and user.id == ignore_user_id:
            continue
        position = positions.get(user.position_id)
        if position is None:
            continue
        if ignore_position_id is not None and position.id == ignore_position_id:
            granted = replacement_permissions
            if granted is None:
                continue
        else:
            granted = position_permissions(position)
        if SELF_LOCKOUT_GUARD_PERMISSIONS <= granted:
            count += 1
    return count


def assert_not_last_administrator(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    ignore_user_id: uuid.UUID | None = None,
    ignore_position_id: uuid.UUID | None = None,
    replacement_permissions: frozenset[str] | None = None,
) -> None:
    """Refuse a change that would leave nobody able to administer the company.

    Without this a tenant can remove its own last administrator and has no path back:
    there is no super-user outside the tenant to restore it.
    """
    remaining = _administrator_count(
        db,
        tenant_id,
        ignore_user_id=ignore_user_id,
        ignore_position_id=ignore_position_id,
        replacement_permissions=replacement_permissions,
    )
    if remaining < 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Thao tác này sẽ khiến công ty không còn ai quản trị được cơ cấu tổ chức. "
                "Hãy cấp quyền cho một người khác trước."
            ),
        )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def derive_manager_id(
    db: Session, user: User, position: Position
) -> uuid.UUID | None:
    """The nearest holder of an ancestor position, or None at the top of the tree.

    Walks upward rather than taking only the direct parent, so a vacant intermediate
    position does not orphan everybody below it.
    """
    positions = {
        row.id: row
        for row in db.query(Position).filter(Position.tenant_id == user.tenant_id).all()
    }
    current = positions.get(position.parent_id) if position.parent_id else None
    seen: set[uuid.UUID] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        holder = db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.position_id == current.id,
            User.is_active.is_(True),
            User.id != user.id,
        ).order_by(User.created_at).first()
        if holder is not None:
            return holder.id
        current = positions.get(current.parent_id) if current.parent_id else None
    return None


def assign_position(
    db: Session,
    user: User,
    position: Position,
    *,
    update_manager: bool = True,
) -> uuid.UUID | None:
    """Put a user in a position, deriving their manager unless it was set by hand.

    Returns the manager id in effect afterwards. Does not commit.
    """
    if position.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Position not found")
    user.position_id = position.id
    if update_manager and not user.manager_is_manual:
        derived = derive_manager_id(db, user, position)
        if derived != user.id:
            user.manager_id = derived
    return user.manager_id
