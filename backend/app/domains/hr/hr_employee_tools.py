"""Narrow AI HR employee tools that only return policy-filtered data."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import aliased

from app.models.models import Department, EmploymentContract, User, UserProfile
from app.domains.platform.audit_events import add_audit_event
from app.domains.hr.hr_access_policy import authorize_employee_access, normalize_sections
from app.domains.hr.hr_service import (
    authorized_employee_ids,
    hr_scope_label,
    query_leave_balance,
    scoped_employee_query,
)


def list_tenant_departments(db: Session, *, actor: User) -> tuple[tuple[str, str], ...]:
    """Every department code this tenant uses, paired with its display name.

    Two sources, because neither is complete on its own. `departments` holds the names a
    company chose ("Phòng tuyển dụng" for the code HR), and it is the only way to
    recognise a department the user names in words. But a tenant seeded without those
    rows still has department codes written on the user records, and the directory must
    keep working there, so the distinct `User.department` values are folded in as codes
    that stand for themselves.
    """
    catalogue: dict[str, str] = {}
    for code, name in db.execute(
        select(Department.code, Department.name).where(
            Department.tenant_id == actor.tenant_id,
            Department.is_active.is_(True),
        )
    ).all():
        cleaned = str(code or "").strip()
        if cleaned:
            catalogue[cleaned] = str(name or "").strip() or cleaned
    for (code,) in db.execute(
        select(User.department).where(
            User.tenant_id == actor.tenant_id,
            User.department.is_not(None),
        ).distinct()
    ).all():
        cleaned = str(code or "").strip()
        if cleaned and cleaned not in catalogue:
            catalogue[cleaned] = cleaned
    return tuple(sorted(catalogue.items()))


def query_company_users_sql(
    db: Session,
    *,
    actor: User,
    search: str | None = None,
    departments: Iterable[str] | None = None,
    roles: Iterable[str] | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    """Execute a fixed, parameterized BASIC user-directory query.

    This deliberately does not accept raw SQL, table names, selected columns or
    arbitrary predicates. Tenant and reporting scope come from the authenticated
    actor and cannot be overridden by model input.
    """
    request_id = f"REQ-{uuid.uuid4()}"
    normalized_search = str(search or "").strip()[:200]
    normalized_departments = tuple(dict.fromkeys(
        str(value).strip()[:100] for value in (departments or []) if str(value).strip()
    ))
    normalized_roles = tuple(dict.fromkeys(
        str(value).strip()[:50] for value in (roles or []) if str(value).strip()
    ))
    safe_limit = max(1, min(int(limit), 100))
    scoped_ids = authorized_employee_ids(db, actor)
    manager = aliased(User)
    statement = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.role,
            User.department,
            User.is_active,
            UserProfile.job_title,
            UserProfile.employee_code,
            UserProfile.employment_status,
            manager.full_name.label("manager_name"),
        )
        .outerjoin(
            UserProfile,
            (UserProfile.user_id == User.id) & (UserProfile.tenant_id == actor.tenant_id),
        )
        .outerjoin(
            manager,
            (manager.id == User.manager_id) & (manager.tenant_id == actor.tenant_id),
        )
        .where(
            User.tenant_id == actor.tenant_id,
            User.id.in_(scoped_ids),
        )
    )
    if active_only:
        statement = statement.where(User.is_active.is_(True))
    if normalized_search:
        pattern = f"%{normalized_search}%"
        statement = statement.where(or_(
            User.full_name.ilike(pattern),
            User.email.ilike(pattern),
            UserProfile.employee_code.ilike(pattern),
        ))
    if normalized_departments:
        statement = statement.where(User.department.in_(normalized_departments))
    if normalized_roles:
        statement = statement.where(User.role.in_(normalized_roles))
    total_count = int(db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one())
    rows = db.execute(statement.order_by(User.full_name).limit(safe_limit)).all()
    items = [
        {
            "id": str(row.id),
            "name": row.full_name,
            "email": row.email,
            "role": row.role,
            "department": row.department,
            "job_title": row.job_title,
            "employee_code": row.employee_code,
            "employment_status": (
                row.employment_status or ("ACTIVE" if row.is_active else "INACTIVE")
            ),
            "manager_name": row.manager_name,
        }
        for row in rows
    ]
    scope = hr_scope_label(actor)
    add_audit_event(
        db,
        tenant_id=actor.tenant_id,
        actor_user=actor,
        actor_type="USER",
        agent_role="HR",
        action="employee.directory.sql.read",
        tool_name="query_company_users_sql",
        resource_type="EMPLOYEE_DIRECTORY",
        input_parameters={
            "request_id": request_id,
            "search": normalized_search or None,
            "departments": list(normalized_departments),
            "roles": list(normalized_roles),
            "active_only": active_only,
            "limit": safe_limit,
            "purpose": "DIRECTORY_LOOKUP",
        },
        output_result={
            "request_id": request_id,
            "result": "ALLOWED",
            "scope": scope,
            "result_count": len(items),
            "total_count": total_count,
            "result_ids": [item["id"] for item in items],
            "allowed_sections": ["BASIC"],
            "query_type": "FIXED_PARAMETERIZED_SQL",
            "source": "AI_HR",
        },
        status="SUCCESS",
    )
    db.commit()
    return {
        "request_id": request_id,
        "purpose": "DIRECTORY_LOOKUP",
        "scope": scope,
        "total_count": total_count,
        "items": items,
    }


def export_company_users_dataset(
    db: Session,
    *,
    actor: User,
    roles: Iterable[str] | None = None,
    active_only: bool = True,
    max_rows: int = 10_000,
) -> dict[str, Any]:
    """Return an auditable BASIC-only HR directory dataset for file export.

    The caller cannot supply tenant IDs, employee IDs, selected columns, or raw
    SQL. Reporting scope is always resolved from the authenticated actor.
    """
    request_id = f"REQ-{uuid.uuid4()}"
    normalized_roles = tuple(dict.fromkeys(
        str(value).strip()[:50] for value in (roles or []) if str(value).strip()
    ))
    safe_max_rows = max(1, min(int(max_rows), 10_000))
    scoped_ids = authorized_employee_ids(db, actor)
    manager = aliased(User)
    statement = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.role,
            User.department,
            User.is_active,
            UserProfile.job_title,
            UserProfile.employee_code,
            UserProfile.employment_status,
            manager.full_name.label("manager_name"),
        )
        .outerjoin(
            UserProfile,
            (UserProfile.user_id == User.id) & (UserProfile.tenant_id == actor.tenant_id),
        )
        .outerjoin(
            manager,
            (manager.id == User.manager_id) & (manager.tenant_id == actor.tenant_id),
        )
        .where(
            User.tenant_id == actor.tenant_id,
            User.id.in_(scoped_ids),
        )
    )
    if active_only:
        statement = statement.where(User.is_active.is_(True))
    if normalized_roles:
        statement = statement.where(User.role.in_(normalized_roles))

    total_count = int(db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one())
    if total_count > safe_max_rows:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Export contains {total_count} records, exceeding the safe limit "
                f"of {safe_max_rows}. Narrow the export scope and try again."
            ),
        )

    rows = db.execute(statement.order_by(User.full_name)).all()
    items = [
        {
            "id": str(row.id),
            "name": row.full_name,
            "email": row.email,
            "role": row.role,
            "department": row.department,
            "job_title": row.job_title,
            "employee_code": row.employee_code,
            "employment_status": (
                row.employment_status or ("ACTIVE" if row.is_active else "INACTIVE")
            ),
            "manager_name": row.manager_name,
        }
        for row in rows
    ]
    scope = hr_scope_label(actor)
    add_audit_event(
        db,
        tenant_id=actor.tenant_id,
        actor_user=actor,
        actor_type="USER",
        agent_role="HR",
        action="employee.directory.file.export",
        tool_name="export_hr_directory",
        resource_type="EMPLOYEE_DIRECTORY",
        input_parameters={
            "request_id": request_id,
            "roles": list(normalized_roles),
            "active_only": active_only,
            "max_rows": safe_max_rows,
            "purpose": "DIRECTORY_EXPORT",
        },
        output_result={
            "request_id": request_id,
            "result": "ALLOWED",
            "scope": scope,
            "result_count": len(items),
            "allowed_sections": ["BASIC"],
            "source": "AI_HR",
        },
        status="SUCCESS",
    )
    db.commit()
    return {
        "request_id": request_id,
        "purpose": "DIRECTORY_EXPORT",
        "scope": scope,
        "total_count": total_count,
        "items": items,
    }


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def _basic_section(db: Session, employee: User) -> dict[str, Any]:
    profile = db.query(
        UserProfile.job_title,
        UserProfile.employee_code,
        UserProfile.hire_date,
        UserProfile.employment_type,
        UserProfile.employment_status,
        UserProfile.skills,
        UserProfile.certifications,
    ).filter(
        UserProfile.tenant_id == employee.tenant_id,
        UserProfile.user_id == employee.id,
    ).first()
    return {
        "id": str(employee.id),
        "name": employee.full_name,
        "email": employee.email,
        "role": employee.role,
        "department": employee.department,
        "job_title": profile.job_title if profile else None,
        "employee_code": profile.employee_code if profile else None,
        "hire_date": profile.hire_date.isoformat() if profile and profile.hire_date else None,
        "employment_type": profile.employment_type if profile else None,
        "employment_status": profile.employment_status if profile else "OFFICIAL",
        "manager_name": employee.manager.full_name if employee.manager else None,
        "skills": profile.skills if profile else [],
        "certifications": profile.certifications if profile else [],
    }


def _private_section(db: Session, employee: User, *, mask: bool) -> tuple[dict[str, Any], list[str]]:
    profile = db.query(
        UserProfile.phone,
        UserProfile.address,
        UserProfile.city,
        UserProfile.country,
        UserProfile.date_of_birth,
        UserProfile.gender,
        UserProfile.emergency_contact_name,
        UserProfile.emergency_contact_phone,
    ).filter(
        UserProfile.tenant_id == employee.tenant_id,
        UserProfile.user_id == employee.id,
    ).first()
    if not profile:
        return {}, []
    if not mask:
        return {
            "phone": profile.phone,
            "address": profile.address,
            "city": profile.city,
            "country": profile.country,
            "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
            "gender": profile.gender,
            "emergency_contact_name": profile.emergency_contact_name,
            "emergency_contact_phone": profile.emergency_contact_phone,
        }, []
    return {
        "phone": _mask_phone(profile.phone),
        "address": None,
        "city": profile.city,
        "country": profile.country,
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
        "gender": profile.gender,
        "emergency_contact_name": profile.emergency_contact_name,
        "emergency_contact_phone": _mask_phone(profile.emergency_contact_phone),
    }, ["private.address", "private.phone", "private.emergency_contact_phone"]


def _contract_section(db: Session, employee: User) -> list[dict[str, Any]]:
    contracts = db.query(EmploymentContract).filter(
        EmploymentContract.tenant_id == employee.tenant_id,
        EmploymentContract.employee_id == employee.id,
    ).order_by(EmploymentContract.start_date.desc()).limit(20).all()
    return [
        {
            "id": str(contract.id),
            "contract_type": contract.contract_type,
            "status": contract.status,
            "start_date": contract.start_date.isoformat(),
            "end_date": contract.end_date.isoformat() if contract.end_date else None,
            "probation_end_date": (
                contract.probation_end_date.isoformat() if contract.probation_end_date else None
            ),
            "signed_by_employee": contract.signed_by_employee,
            "signed_by_company": contract.signed_by_company,
            "days_until_expiry": (
                (contract.end_date - date.today()).days if contract.end_date else None
            ),
        }
        for contract in contracts
    ]


def list_contract_status_summaries(
    db: Session,
    *,
    actor: User,
    purpose: str = "CONTRACT_STATUS_MONITORING",
    limit: int = 10,
    tool_name: str = "get_contract_expiry",
) -> dict[str, Any]:
    """Return tenant/scoped contract metadata without loading document secrets.

    Policy is evaluated for candidate employees before the contract projection is
    queried. Contract numbers, document names and document URLs never enter the
    tool result or the LLM context.
    """
    request_id = f"REQ-{uuid.uuid4()}"
    candidates = scoped_employee_query(db, actor).all()
    allowed_targets = [
        employee
        for employee in candidates
        if "CONTRACT" in authorize_employee_access(
            db,
            actor=actor,
            target=employee,
            requested_sections=["CONTRACT"],
            purpose=purpose,
        ).allowed_sections
    ]
    allowed_ids = [employee.id for employee in allowed_targets]
    rows = []
    if allowed_ids:
        rows = db.query(
            EmploymentContract.id,
            EmploymentContract.employee_id,
            EmploymentContract.contract_type,
            EmploymentContract.status,
            EmploymentContract.start_date,
            EmploymentContract.end_date,
            EmploymentContract.probation_end_date,
            EmploymentContract.signed_by_employee,
            EmploymentContract.signed_by_company,
            User.full_name.label("employee_name"),
        ).join(
            User,
            User.id == EmploymentContract.employee_id,
        ).filter(
            EmploymentContract.tenant_id == actor.tenant_id,
            EmploymentContract.status == "ACTIVE",
            EmploymentContract.employee_id.in_(allowed_ids),
        ).order_by(
            EmploymentContract.end_date.asc().nullslast(),
        ).limit(max(1, min(limit, 50))).all()

    items = [
        {
            "id": str(row.id),
            "employee_id": str(row.employee_id),
            "employee_name": row.employee_name,
            "contract_type": row.contract_type,
            "status": row.status,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "probation_end_date": (
                row.probation_end_date.isoformat() if row.probation_end_date else None
            ),
            "signed_by_employee": row.signed_by_employee,
            "signed_by_company": row.signed_by_company,
            "days_until_expiry": (
                (row.end_date - date.today()).days if row.end_date else None
            ),
        }
        for row in rows
    ]
    add_audit_event(
        db,
        tenant_id=actor.tenant_id,
        actor_user=actor,
        actor_type="USER",
        agent_role="HR",
        action="employee.contract.status.read",
        tool_name=tool_name,
        resource_type="EMPLOYMENT_CONTRACT",
        input_parameters={
            "request_id": request_id,
            "requested_sections": ["CONTRACT"],
            "purpose": purpose,
            "limit": limit,
        },
        output_result={
            "request_id": request_id,
            "result": "ALLOWED",
            "scope": "COMPANY" if actor.role in {"Owner", "CEO"} else "SCOPED",
            "result_ids": [item["id"] for item in items],
            "target_employee_ids": list(dict.fromkeys(item["employee_id"] for item in items)),
            "allowed_sections": ["CONTRACT"],
            "excluded_fields": ["contract_number", "document_name", "document_url"],
            "source": "AI_HR",
        },
        status="SUCCESS",
    )
    db.commit()
    return {
        "request_id": request_id,
        "purpose": purpose,
        "scope": "COMPANY" if actor.role in {"Owner", "CEO"} else "SCOPED",
        "items": items,
    }


def _compensation_section(db: Session, employee: User) -> dict[str, Any]:
    profile = db.query(
        UserProfile.monthly_salary,
        UserProfile.salary_currency,
    ).filter(
        UserProfile.tenant_id == employee.tenant_id,
        UserProfile.user_id == employee.id,
    ).first()
    return {
        "monthly_salary": float(profile.monthly_salary) if profile and profile.monthly_salary is not None else None,
        "salary_currency": profile.salary_currency if profile else "VND",
    }


def _audit_access(
    db: Session,
    *,
    actor: User,
    target_id: str,
    tool_name: str,
    purpose: str,
    requested_sections: Iterable[str],
    allowed_sections: Iterable[str],
    denied_sections: Iterable[str],
    masked_fields: Iterable[str],
    scope: str,
    result: str,
    reason: Any = None,
    request_id: str,
) -> None:
    add_audit_event(
        db,
        tenant_id=actor.tenant_id,
        actor_user=actor,
        actor_type="USER",
        agent_role="HR",
        action="employee.profile.read",
        tool_name=tool_name,
        resource_type="EMPLOYEE",
        resource_id=target_id,
        input_parameters={
            "request_id": request_id,
            "requested_sections": list(requested_sections),
            "purpose": purpose,
        },
        output_result={
            "request_id": request_id,
            "result": result,
            "scope": scope,
            "allowed_sections": list(allowed_sections),
            "denied_sections": list(denied_sections),
            "masked_fields": list(masked_fields),
            "reason": reason,
            "source": "AI_HR",
        },
        status="SUCCESS" if result == "ALLOWED" else "DENIED",
        error_message=None if result == "ALLOWED" else str(reason),
    )
    db.commit()


def get_employee_sections(
    db: Session,
    *,
    actor: User,
    employee_id: uuid.UUID | str,
    requested_sections: Iterable[str],
    purpose: str,
    tool_name: str = "get_employee_full_profile",
) -> dict[str, Any]:
    """Policy enforcement point used by every narrow employee-data tool."""
    request_id = f"REQ-{uuid.uuid4()}"
    requested = normalize_sections(requested_sections)
    try:
        target_uuid = uuid.UUID(str(employee_id))
    except (TypeError, ValueError):
        _audit_access(
            db,
            actor=actor,
            target_id=str(employee_id),
            tool_name=tool_name,
            purpose=purpose,
            requested_sections=requested,
            allowed_sections=[],
            denied_sections=requested,
            masked_fields=[],
            scope="NONE",
            result="DENIED",
            reason="INVALID_EMPLOYEE_ID",
            request_id=request_id,
        )
        raise HTTPException(status_code=404, detail="Employee not found")

    employee = db.query(User).filter(
        User.id == target_uuid,
        User.tenant_id == actor.tenant_id,
    ).first()
    if not employee:
        _audit_access(
            db,
            actor=actor,
            target_id=str(target_uuid),
            tool_name=tool_name,
            purpose=purpose,
            requested_sections=requested,
            allowed_sections=[],
            denied_sections=requested,
            masked_fields=[],
            scope="NONE",
            result="DENIED",
            reason="NOT_FOUND_OR_CROSS_TENANT",
            request_id=request_id,
        )
        raise HTTPException(status_code=404, detail="Employee not found")

    decision = authorize_employee_access(
        db,
        actor=actor,
        target=employee,
        requested_sections=requested,
        purpose=purpose,
    )
    if not decision.allowed:
        _audit_access(
            db,
            actor=actor,
            target_id=str(employee.id),
            tool_name=tool_name,
            purpose=decision.purpose,
            requested_sections=requested,
            allowed_sections=[],
            denied_sections=decision.denied_sections,
            masked_fields=[],
            scope=decision.scope,
            result="DENIED",
            reason=decision.denial_reasons,
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail="Employee data access denied by HR policy")

    data: dict[str, Any] = {}
    masked_fields: list[str] = []
    allowed = set(decision.allowed_sections)
    if "BASIC" in allowed:
        data["basic"] = _basic_section(db, employee)
    if "PRIVATE" in allowed:
        data["private"], private_masked = _private_section(
            db,
            employee,
            mask=actor.id != employee.id,
        )
        masked_fields.extend(private_masked)
    if "CONTRACT" in allowed:
        data["contract"] = _contract_section(db, employee)
    if "COMPENSATION" in allowed:
        data["compensation"] = _compensation_section(db, employee)
    if "LEAVE" in allowed:
        data["leave"] = query_leave_balance(db, employee)
    for unavailable in allowed & {"PERFORMANCE", "DISCIPLINE", "HR_NOTES", "DOCUMENTS"}:
        data[unavailable.lower()] = {"status": "NOT_AVAILABLE_IN_MVP"}

    _audit_access(
        db,
        actor=actor,
        target_id=str(employee.id),
        tool_name=tool_name,
        purpose=decision.purpose,
        requested_sections=requested,
        allowed_sections=decision.allowed_sections,
        denied_sections=decision.denied_sections,
        masked_fields=masked_fields,
        scope=decision.scope,
        result="ALLOWED",
        reason=decision.denial_reasons or None,
        request_id=request_id,
    )
    return {
        "employee_id": str(employee.id),
        "request_id": request_id,
        "purpose": decision.purpose,
        "scope": decision.scope,
        "allowed_sections": list(decision.allowed_sections),
        "denied_sections": list(decision.denied_sections),
        "masked_fields": masked_fields,
        "data": data,
    }


# Five single-section wrappers around get_employee_sections used to live here, one per
# capability name. Nothing imported them: the executor calls get_employee_sections
# directly with the section list and the tool_name it wants recorded in the audit trail,
# and the policy tests do the same. They were removed rather than left as a second way to
# spell the same call, which would let the two drift.
