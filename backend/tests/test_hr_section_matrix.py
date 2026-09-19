"""Characterization of who may read which HR data section.

The expectations are the ones the role x department matrix produced before positions
carried permissions. They are behaviour users depend on, so they must survive the move to
position permissions -- but the *question* has to be asked in the new vocabulary.

The old version of this file built a bare `User` carrying only `role` and `department`
and asked `_role_sections` about it. Under the current model such a user holds no
position, and therefore genuinely holds no permissions, so every expectation that wanted
sections failed and the three that wanted nothing passed for the wrong reason. Each actor
here holds a real `Position` instead: the one the seeding vocabulary says reproduces that
legacy job.

No database. `user_permissions` reads `user.position` directly when it is already loaded,
so an in-memory Position exercises the real policy function end to end.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.permissions import (
    DEFAULT_POSITIONS,
    HR_SECTION_PERMISSIONS,
    LEGACY_ROLE_TO_SLUG,
    LEGACY_SPECIALISATIONS,
    PERMISSION_CODES,
    SPECIALISED_POSITIONS,
)
from app.models.models import Position, User
from app.services.hr_access_policy import HR_DATA_SECTIONS, _role_sections

ALL_SECTIONS = frozenset(HR_DATA_SECTIONS)
SELF_SECTIONS = frozenset({"BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"})
HR_MANAGER_SECTIONS = frozenset(
    {"BASIC", "PRIVATE", "CONTRACT", "LEAVE", "PERFORMANCE", "DOCUMENTS"}
)
FINANCE_SECTIONS = frozenset({"BASIC", "COMPENSATION"})
LINE_MANAGER_SECTIONS = frozenset({"BASIC", "CONTRACT", "LEAVE", "PERFORMANCE"})

# slug -> (permissions, grants_all), mirroring what ensure_tenant_positions writes.
_POSITION_DEFINITIONS = {
    item.slug: (tuple(item.permissions), item.grants_all) for item in DEFAULT_POSITIONS
} | {item.slug: (tuple(item.permissions), False) for item in SPECIALISED_POSITIONS}


def _position_for(role: str, department: str) -> Position:
    """The position that reproduces this legacy role x department job.

    A department-specialised job (an Admin in HR, a Manager in FINANCE) has its own
    position; everything else falls back to the position for the role alone. This is the
    same lookup the seeding and backfill code performs.
    """
    slug = LEGACY_SPECIALISATIONS.get((role, department)) or LEGACY_ROLE_TO_SLUG[role]
    permissions, grants_all = _POSITION_DEFINITIONS[slug]
    return Position(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=slug,
        slug=slug,
        permissions=list(permissions),
        grants_all=grants_all,
        # Spelled out because a column `default=` is applied by the INSERT, not by the
        # constructor: on an object that is never flushed, is_active would be None, and
        # `position_permissions` reads that as a deactivated job and grants nothing.
        is_active=True,
    )


def _actor(role: str, department: str) -> User:
    position = _position_for(role, department)
    user = User(
        id=uuid.uuid4(),
        tenant_id=position.tenant_id,
        email=f"{uuid.uuid4().hex[:8]}@x.com",
        full_name="Actor",
        password_hash="x",
        role=role,
        department=department,
        position_id=position.id,
    )
    # Set the relationship as well as the id: user_permissions uses the loaded object
    # when there is one, which keeps this test free of a database.
    user.position = position
    return user


def _target() -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@x.com",
        full_name="Target",
        password_hash="x",
        role="Employee",
        department="IT",
    )


@pytest.mark.parametrize(
    ("role", "department", "expected"),
    [
        # Executives see everything, in any department.
        ("Owner", "BOARD", ALL_SECTIONS),
        ("Owner", "IT", ALL_SECTIONS),
        ("CEO", "BOARD", ALL_SECTIONS),
        ("CEO", "SALES", ALL_SECTIONS),
        # An Admin sitting in HR is the full HR back office.
        ("Admin", "HR", ALL_SECTIONS),
        # An HR Manager sees the people sections but never salary.
        ("Manager", "HR", HR_MANAGER_SECTIONS),
        # Finance sees salary and nothing else personal.
        ("Admin", "FINANCE", FINANCE_SECTIONS),
        ("Manager", "FINANCE", FINANCE_SECTIONS),
        # Every other Admin or Manager is a line manager.
        ("Admin", "IT", LINE_MANAGER_SECTIONS),
        ("Manager", "IT", LINE_MANAGER_SECTIONS),
        ("Manager", "SALES", LINE_MANAGER_SECTIONS),
        ("Admin", "LEGAL", LINE_MANAGER_SECTIONS),
        # Everyone else sees nothing about other people.
        ("Employee", "IT", frozenset()),
        ("Employee", "HR", frozenset()),
        ("Guest", "ALL", frozenset()),
    ],
)
def test_section_matrix_for_another_employee(role, department, expected):
    granted = _role_sections(_actor(role, department), _target(), None)

    assert set(granted) == set(expected), (
        f"{role} trong phòng {department} nhận {sorted(granted)}, "
        f"đáng lẽ {sorted(expected)}"
    )


def test_an_actor_with_no_position_is_granted_nothing():
    """The empty answer must come from holding no position, not from a missing argument."""
    actor = _actor("Admin", "HR")
    actor.position = None
    actor.position_id = None

    assert _role_sections(actor, _target(), None) == set()


@pytest.mark.parametrize(
    ("role", "department"),
    [
        ("Employee", "IT"),
        ("Guest", "ALL"),
        ("Manager", "SALES"),
        ("Admin", "FINANCE"),
        ("CEO", "BOARD"),
    ],
)
def test_reading_your_own_record_never_depends_on_your_job(role, department):
    """Self-service is a floor: it must not be reachable only through a job title."""
    actor = _actor(role, department)

    granted = _role_sections(actor, actor, None)

    assert SELF_SECTIONS <= set(granted)


def test_self_access_never_exposes_hr_only_sections_to_an_ordinary_employee():
    actor = _actor("Employee", "IT")

    granted = set(_role_sections(actor, actor, None))

    assert "DISCIPLINE" not in granted
    assert "HR_NOTES" not in granted


def test_every_section_maps_to_a_permission_that_actually_exists():
    """A section gated on a code no position can hold would be unreachable forever."""
    unknown = {
        section: code
        for section, code in HR_SECTION_PERMISSIONS.items()
        if code not in PERMISSION_CODES
    }
    assert unknown == {}
    assert set(HR_SECTION_PERMISSIONS) == set(HR_DATA_SECTIONS)
