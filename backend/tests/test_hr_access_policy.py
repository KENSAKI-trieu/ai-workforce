"""Security regression coverage for the centralized AI HR policy engine."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.models import AuditLog, User, UserProfile
from app.domains.hr.hr_employee_tools import get_employee_sections, query_company_users_sql
from app.domains.hr.hr_service import authorized_employee_ids


def _user(db, email: str) -> User:
    return db.query(User).filter(User.email == email).one()


def test_employee_can_read_own_compensation_but_manager_cannot(
    transactional_db_session,
):
    db = transactional_db_session
    employee = _user(db, "employee@company.com")
    manager = _user(db, "it.lead@company.com")
    profile = db.query(UserProfile).filter(UserProfile.user_id == employee.id).first()
    if not profile:
        profile = UserProfile(
            tenant_id=employee.tenant_id,
            user_id=employee.id,
            preferences={},
            skills=[],
            certifications=[],
            employment_history=[],
            salary_currency="VND",
        )
        db.add(profile)
    profile.monthly_salary = 42_000_000
    db.commit()

    own = get_employee_sections(
        db,
        actor=employee,
        employee_id=employee.id,
        requested_sections=["COMPENSATION"],
        purpose="SELF_SERVICE",
        tool_name="get_employee_compensation_summary",
    )
    assert own["data"]["compensation"]["monthly_salary"] == 42_000_000

    with pytest.raises(HTTPException) as denied:
        get_employee_sections(
            db,
            actor=manager,
            employee_id=employee.id,
            requested_sections=["COMPENSATION"],
            purpose="PAYROLL_PROCESSING",
            tool_name="get_employee_compensation_summary",
        )
    assert denied.value.status_code == 403
    audit = db.query(AuditLog).filter(
        AuditLog.actor_user_id == manager.id,
        AuditLog.tool_name == "get_employee_compensation_summary",
    ).order_by(AuditLog.created_at.desc()).first()
    assert audit.status == "DENIED"
    assert audit.output_result["reason"]["COMPENSATION"] == "MISSING_PERMISSION"


def test_purpose_limitation_filters_sections_before_data_fetch(
    transactional_db_session,
):
    db = transactional_db_session
    ceo = _user(db, "admin@company.com")
    employee = _user(db, "employee@company.com")
    result = get_employee_sections(
        db,
        actor=ceo,
        employee_id=employee.id,
        requested_sections=["BASIC", "CONTRACT", "COMPENSATION"],
        purpose="CONTRACT_RENEWAL",
        tool_name="get_employee_full_profile",
    )
    assert result["allowed_sections"] == ["BASIC", "CONTRACT"]
    assert result["denied_sections"] == ["COMPENSATION"]
    assert "compensation" not in result["data"]
    assert all(
        "document_url" not in contract and "contract_number" not in contract
        for contract in result["data"]["contract"]
    )


def test_technical_admin_does_not_gain_private_hr_data(
    transactional_db_session,
):
    db = transactional_db_session
    technical_admin = _user(db, "legal.counsel@company.com")
    employee = _user(db, "employee@company.com")
    technical_admin.role = "Admin"
    technical_admin.department = "IT"
    employee.manager_id = technical_admin.id
    db.commit()

    with pytest.raises(HTTPException) as denied:
        get_employee_sections(
            db,
            actor=technical_admin,
            employee_id=employee.id,
            requested_sections=["PRIVATE"],
            purpose="HR_OPERATIONS",
            tool_name="get_employee_private_profile",
        )
    assert denied.value.status_code == 403


def test_unknown_or_cross_tenant_employee_id_is_hidden_and_audited(
    transactional_db_session,
):
    db = transactional_db_session
    employee = _user(db, "employee@company.com")
    unknown_id = uuid.uuid4()
    with pytest.raises(HTTPException) as denied:
        get_employee_sections(
            db,
            actor=employee,
            employee_id=unknown_id,
            requested_sections=["BASIC"],
            purpose="SELF_SERVICE",
            tool_name="get_employee_basic_profile",
        )
    assert denied.value.status_code == 404
    audit = db.query(AuditLog).filter(
        AuditLog.actor_user_id == employee.id,
        AuditLog.resource_id == str(unknown_id),
    ).order_by(AuditLog.created_at.desc()).first()
    assert audit.status == "DENIED"
    assert audit.output_result["reason"] == "NOT_FOUND_OR_CROSS_TENANT"


def test_company_user_sql_tool_is_parameterized_and_scope_limited(
    transactional_db_session,
):
    db = transactional_db_session
    ceo = _user(db, "admin@company.com")
    employee = _user(db, "employee@company.com")

    company_result = query_company_users_sql(db, actor=ceo, limit=100)
    assert company_result["scope"] == "COMPANY"
    assert any(item["email"] == employee.email for item in company_result["items"])
    assert all("monthly_salary" not in item for item in company_result["items"])
    assert all("password_hash" not in item for item in company_result["items"])

    self_result = query_company_users_sql(db, actor=employee, limit=100)
    assert self_result["scope"] == "SELF"
    assert [item["id"] for item in self_result["items"]] == [str(employee.id)]

    injection_result = query_company_users_sql(
        db,
        actor=ceo,
        search="' OR 1=1 --",
        limit=100,
    )
    assert injection_result["items"] == []
    audit = db.query(AuditLog).filter(
        AuditLog.actor_user_id == ceo.id,
        AuditLog.tool_name == "query_company_users_sql",
    ).order_by(AuditLog.created_at.desc()).first()
    assert audit.output_result["query_type"] == "FIXED_PARAMETERIZED_SQL"


def test_manager_can_read_self_and_recursive_reports_but_not_other_branches(
    transactional_db_session,
):
    db = transactional_db_session
    manager = _user(db, "it.lead@company.com")
    peer_manager = _user(db, "legal.counsel@company.com")
    nested_manager = User(
        tenant_id=manager.tenant_id,
        email=f"nested.manager.{uuid.uuid4()}@company.com",
        full_name="Nested Manager",
        password_hash="test-only-hash",
        role="Manager",
        department="IT",
        manager_id=manager.id,
        is_active=True,
    )
    nested_employee = User(
        tenant_id=manager.tenant_id,
        email=f"nested.employee.{uuid.uuid4()}@company.com",
        full_name="Nested Employee",
        password_hash="test-only-hash",
        role="Employee",
        department="IT",
        manager=nested_manager,
        is_active=True,
    )
    db.add_all([nested_manager, nested_employee])
    db.commit()

    visible_ids = authorized_employee_ids(db, manager)
    assert manager.id in visible_ids
    assert nested_manager.id in visible_ids
    assert nested_employee.id in visible_ids
    assert peer_manager.id not in visible_ids

    directory = query_company_users_sql(db, actor=manager, active_only=False, limit=100)
    directory_ids = {item["id"] for item in directory["items"]}
    assert str(nested_manager.id) in directory_ids
    assert str(nested_employee.id) in directory_ids
    assert str(peer_manager.id) not in directory_ids

    own_profile = get_employee_sections(
        db,
        actor=manager,
        employee_id=manager.id,
        requested_sections=["BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"],
        purpose="SELF_SERVICE",
        tool_name="get_employee_full_profile",
    )
    assert own_profile["scope"] == "SELF"
    assert own_profile["denied_sections"] == []

    subordinate = get_employee_sections(
        db,
        actor=manager,
        employee_id=nested_employee.id,
        requested_sections=["BASIC", "CONTRACT", "LEAVE"],
        purpose="HR_OPERATIONS",
        tool_name="get_employee_full_profile",
    )
    assert subordinate["scope"] == "REPORTING_TREE"
    assert subordinate["denied_sections"] == []

    with pytest.raises(HTTPException) as denied:
        get_employee_sections(
            db,
            actor=manager,
            employee_id=peer_manager.id,
            requested_sections=["BASIC"],
            purpose="DIRECTORY_LOOKUP",
            tool_name="get_employee_basic_profile",
        )
    assert denied.value.status_code == 403
