"""Characterization of who may read which HR data section.

Written against the role x department matrix that existed before positions carried
permissions, and kept unchanged across that refactor. Every expectation here is a
statement about behaviour users depend on, not about how the rule is implemented, so it
must stay green while the implementation moves from role strings to permission codes.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.models import User
from app.services.hr_access_policy import HR_DATA_SECTIONS, _role_sections

ALL_SECTIONS = frozenset(HR_DATA_SECTIONS)
SELF_SECTIONS = frozenset({"BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"})
HR_MANAGER_SECTIONS = frozenset(
    {"BASIC", "PRIVATE", "CONTRACT", "LEAVE", "PERFORMANCE", "DOCUMENTS"}
)
FINANCE_SECTIONS = frozenset({"BASIC", "COMPENSATION"})
LINE_MANAGER_SECTIONS = frozenset({"BASIC", "CONTRACT", "LEAVE", "PERFORMANCE"})


def _actor(role: str, department: str) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@x.com",
        full_name="Actor",
        password_hash="x",
        role=role,
        department=department,
    )


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
    granted = _role_sections(_actor(role, department), _target())

    assert set(granted) == set(expected), (
        f"{role} trong phòng {department} nhận {sorted(granted)}, "
        f"đáng lẽ {sorted(expected)}"
    )


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

    granted = _role_sections(actor, actor)

    assert SELF_SECTIONS <= set(granted)


def test_self_access_never_exposes_hr_only_sections_to_an_ordinary_employee():
    actor = _actor("Employee", "IT")

    granted = set(_role_sections(actor, actor))

    assert "DISCIPLINE" not in granted
    assert "HR_NOTES" not in granted
