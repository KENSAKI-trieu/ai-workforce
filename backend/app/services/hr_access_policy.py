"""Central policy engine for tenant-safe, purpose-limited AI HR access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.permissions import HR_SECTION_PERMISSIONS
from app.models.models import User
from app.services.hr_service import authorized_employee_ids, has_company_hr_scope

HR_DATA_SECTIONS = {
    "BASIC",
    "PRIVATE",
    "CONTRACT",
    "COMPENSATION",
    "LEAVE",
    "PERFORMANCE",
    "DISCIPLINE",
    "HR_NOTES",
    "DOCUMENTS",
}

SECTION_PERMISSIONS = {
    "BASIC": "employee.basic.read",
    "PRIVATE": "employee.private.read",
    "CONTRACT": "employee.contract.read",
    "COMPENSATION": "employee.compensation.read",
    "LEAVE": "employee.leave.read",
    "PERFORMANCE": "employee.performance.read",
    "DISCIPLINE": "employee.discipline.read",
    "HR_NOTES": "employee.hr_notes.read",
    "DOCUMENTS": "employee.documents.read",
}

PURPOSE_SECTIONS = {
    "SELF_SERVICE": {"BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"},
    "DIRECTORY_LOOKUP": {"BASIC"},
    "LEAVE_MANAGEMENT": {"BASIC", "LEAVE"},
    "CONTRACT_RENEWAL": {"BASIC", "CONTRACT"},
    "CONTRACT_STATUS_MONITORING": {"BASIC", "CONTRACT"},
    "ONBOARDING": {"BASIC", "PRIVATE", "CONTRACT", "DOCUMENTS"},
    "PERFORMANCE_REVIEW": {"BASIC", "PERFORMANCE"},
    "PAYROLL_PROCESSING": {"BASIC", "COMPENSATION"},
    "HR_OPERATIONS": {"BASIC", "PRIVATE", "CONTRACT", "LEAVE", "DOCUMENTS"},
    "LEGAL_REVIEW": {"BASIC", "CONTRACT", "DISCIPLINE", "DOCUMENTS"},
    "EMPLOYEE_SUPPORT": {"BASIC", "PRIVATE", "LEAVE"},
    "EXECUTIVE_REVIEW": {
        "BASIC",
        "CONTRACT",
        "COMPENSATION",
        "LEAVE",
        "PERFORMANCE",
    },
}


@dataclass(frozen=True)
class EmployeeAccessDecision:
    allowed: bool
    scope: str
    purpose: str
    allowed_sections: tuple[str, ...] = ()
    denied_sections: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    denial_reasons: dict[str, str] = field(default_factory=dict)


def normalize_sections(sections: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(section).strip().upper() for section in sections if section))


def _scope_for_employee(db: Session, actor: User, target: User) -> tuple[bool, str, str | None]:
    if actor.tenant_id != target.tenant_id:
        return False, "NONE", "CROSS_TENANT"
    if actor.id == target.id:
        return True, "SELF", None
    if has_company_hr_scope(actor):
        return True, "COMPANY", None
    if target.id in authorized_employee_ids(db, actor):
        return True, "REPORTING_TREE", None
    return False, "NONE", "OUTSIDE_SCOPE"


SELF_SERVICE_SECTIONS: frozenset[str] = frozenset(
    {"BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"}
)


def _role_sections(actor: User, target: User, db: Session | None) -> set[str]:
    """Sections the actor may read about the target, from their position's permissions.

    This used to be a role x department ladder with department codes like "FINANCE"
    written into it. Departments are tenant-defined data, so a company that named its
    finance department anything else silently lost salary access. The rule now asks what
    the actor is permitted to do, and each department-specialised job is a real position.

    Reading your own record is a floor that no job title can take away.

    `db` is required rather than defaulted. It used to default to None and answer with an
    empty set whenever it was omitted, which silently denied every section -- the exact
    failure `user_permissions` refuses to produce, and which it raises about instead.
    Passing None is still allowed, because `user_permissions` can recover the session from
    the actor itself; what is no longer allowed is forgetting the argument and getting a
    denial that looks like a policy decision.
    """
    if actor.id == target.id:
        return set(SELF_SERVICE_SECTIONS)

    from app.services.position_service import user_permissions

    granted = user_permissions(db, actor)
    return {
        section
        for section, permission in HR_SECTION_PERMISSIONS.items()
        if permission in granted
    }


def authorize_employee_access(
    db: Session,
    *,
    actor: User,
    target: User,
    requested_sections: Iterable[str],
    purpose: str,
) -> EmployeeAccessDecision:
    """Apply tenant, scope, RBAC, field and purpose checks in one place."""
    requested = normalize_sections(requested_sections)
    normalized_purpose = str(purpose or "").strip().upper()
    if normalized_purpose not in PURPOSE_SECTIONS:
        return EmployeeAccessDecision(
            allowed=False,
            scope="NONE",
            purpose=normalized_purpose or "UNSPECIFIED",
            denied_sections=requested,
            denial_reasons={section: "INVALID_PURPOSE" for section in requested},
        )

    in_scope, scope, scope_denial = _scope_for_employee(db, actor, target)
    if not in_scope:
        return EmployeeAccessDecision(
            allowed=False,
            scope=scope,
            purpose=normalized_purpose,
            denied_sections=requested,
            denial_reasons={section: scope_denial or "OUTSIDE_SCOPE" for section in requested},
        )

    role_sections = _role_sections(actor, target, db)
    purpose_sections = PURPOSE_SECTIONS[normalized_purpose]
    allowed_sections: list[str] = []
    denied_sections: list[str] = []
    denial_reasons: dict[str, str] = {}
    for section in requested:
        if section not in HR_DATA_SECTIONS:
            denied_sections.append(section)
            denial_reasons[section] = "UNSUPPORTED_SECTION"
        elif section not in role_sections:
            denied_sections.append(section)
            denial_reasons[section] = "MISSING_PERMISSION"
        elif section not in purpose_sections:
            denied_sections.append(section)
            denial_reasons[section] = "PURPOSE_LIMITATION"
        else:
            allowed_sections.append(section)
    return EmployeeAccessDecision(
        allowed=bool(allowed_sections),
        scope=scope,
        purpose=normalized_purpose,
        allowed_sections=tuple(allowed_sections),
        denied_sections=tuple(denied_sections),
        permissions=tuple(SECTION_PERMISSIONS[section] for section in allowed_sections),
        denial_reasons=denial_reasons,
    )
