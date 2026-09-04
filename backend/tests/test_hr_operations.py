"""End-to-end coverage for the structured AI HR V1 vertical slice."""

import uuid
from datetime import date, timedelta

from app.models.models import (
    AuditLog,
    HRCalendarEvent,
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    OnboardingCase,
    Task,
    User,
)


def _login(client, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _next_weekday(offset: int = 7) -> date:
    value = date.today() + timedelta(days=offset)
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def test_employee_and_manager_hr_scopes(
    client,
    employee_token_headers,
    ceo_token_headers,
    transactional_db_session,
):
    employee_scope = client.get("/api/v1/hr/employees", headers=employee_token_headers)
    assert employee_scope.status_code == 200, employee_scope.text
    assert len(employee_scope.json()) == 1
    assert employee_scope.json()[0]["email"] == "employee@company.com"

    manager_headers = _login(client, "it.lead@company.com")
    team_scope = client.get("/api/v1/hr/employees", headers=manager_headers)
    assert team_scope.status_code == 200, team_scope.text
    emails = {item["email"] for item in team_scope.json()}
    assert "employee@company.com" in emails
    assert "finance.lead@company.com" not in emails

    ceo_scope = client.get("/api/v1/hr/employees", headers=ceo_token_headers)
    assert ceo_scope.status_code == 200
    assert len(ceo_scope.json()) >= 7

    admin = transactional_db_session.query(User).filter(
        User.email == "legal.counsel@company.com"
    ).one()
    finance = transactional_db_session.query(User).filter(
        User.email == "finance.lead@company.com"
    ).one()
    admin.role = "Admin"
    finance.manager_id = admin.id
    transactional_db_session.commit()
    admin_headers = _login(client, "legal.counsel@company.com")
    admin_scope = client.get("/api/v1/hr/employees", headers=admin_headers)
    assert {item["email"] for item in admin_scope.json()} == {
        "legal.counsel@company.com",
        "finance.lead@company.com",
    }


def test_hr_chat_employee_search_respects_reporting_tree(
    client,
    employee_token_headers,
    ceo_token_headers,
):
    ceo_search = client.post(
        "/api/v1/agent/chat",
        headers=ceo_token_headers,
        json={"agent_role": "HR", "message": "Tìm nhân viên finance.lead@company.com"},
    )
    assert ceo_search.status_code == 200, ceo_search.text
    assert ceo_search.json()["hr_card"]["employee"]["email"] == "finance.lead@company.com"

    manager_headers = _login(client, "it.lead@company.com")
    manager_search = client.post(
        "/api/v1/agent/chat",
        headers=manager_headers,
        json={"agent_role": "HR", "message": "Tìm nhân viên employee@company.com"},
    )
    assert manager_search.status_code == 200, manager_search.text
    assert manager_search.json()["hr_card"]["employee"]["email"] == "employee@company.com"

    blocked_search = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={"agent_role": "HR", "message": "Tìm nhân viên finance.lead@company.com"},
    )
    assert blocked_search.status_code == 200, blocked_search.text
    assert blocked_search.json()["hr_card"] is None
    assert "phạm vi" in blocked_search.json()["reply"].lower()


def test_hr_chat_manager_directory_uses_fast_scoped_query(
    client,
    employee_token_headers,
    ceo_token_headers,
):
    ceo_response = client.post(
        "/api/v1/agent/chat",
        headers=ceo_token_headers,
        json={"agent_role": "HR", "message": "Xem danh sach quan ly"},
    )
    assert ceo_response.status_code == 200, ceo_response.text
    ceo_data = ceo_response.json()
    assert ceo_data["hr_card"]["directory_type"] == "MANAGERS"
    assert ceo_data["tools_executed"][0]["input"]["directory"] == "managers"
    assert ceo_data["hr_card"]["items"]
    assert {
        item["employee"]["role"] for item in ceo_data["hr_card"]["items"]
    } <= {"Admin", "CEO", "Manager"}
    assert all(
        item["leave_balance"] is None for item in ceo_data["hr_card"]["items"]
    )

    employee_response = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={"agent_role": "HR", "message": "Xem danh sach quan ly"},
    )
    assert employee_response.status_code == 200, employee_response.text
    employee_data = employee_response.json()
    assert employee_data["hr_card"]["scope"] == "SELF"
    assert employee_data["hr_card"]["items"] == []


def test_hr_leave_intent_routes_policy_to_rag_without_creating_request(
    client,
    employee_token_headers,
    transactional_db_session,
):
    before = transactional_db_session.query(LeaveRequest).count()
    response = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={
            "agent_role": "HR",
            "message": "Quy định xin nghỉ phép của công ty là gì?",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["approval_card"] is None
    assert all(
        item["tool_name"] != "request_leave" for item in data["tools_executed"]
    )
    assert any(
        item["tool_name"] == "hybrid_rag_search" for item in data["tools_executed"]
    )
    assert transactional_db_session.query(LeaveRequest).count() == before


def test_hr_leave_request_collects_three_required_slots_before_submission(
    client,
    employee_token_headers,
    transactional_db_session,
):
    start = _next_weekday(160)
    end = start + timedelta(days=2)
    before = transactional_db_session.query(LeaveRequest).count()

    draft_response = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={
            "agent_role": "HR",
            "message": f"Tôi muốn xin nghỉ phép từ {start.isoformat()}",
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft_data = draft_response.json()
    assert draft_data["approval_card"] is None
    assert draft_data["hr_card"]["type"] == "LEAVE_REQUEST_DRAFT"
    assert draft_data["hr_card"]["start_date"] == start.isoformat()
    assert draft_data["hr_card"]["missing_fields"] == ["end_date", "reason"]
    assert all(
        item["tool_name"] != "request_leave"
        for item in draft_data["tools_executed"]
    )
    assert transactional_db_session.query(LeaveRequest).count() == before

    submit_response = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={
            "agent_role": "HR",
            "conversation_id": draft_data["conversation_id"],
            "message": f"Đến ngày {end.isoformat()} vì về quê thăm gia đình",
        },
    )
    assert submit_response.status_code == 200, submit_response.text
    submit_data = submit_response.json()
    assert submit_data["approval_card"]["status"] == "WAITING"
    tool_input = submit_data["tools_executed"][0]["input"]
    assert tool_input == {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "reason": "về quê thăm gia đình",
    }
    assert transactional_db_session.query(LeaveRequest).count() == before + 1


def test_leave_request_approval_updates_ledger_calendar_and_audit(
    client,
    employee_token_headers,
    ceo_token_headers,
    transactional_db_session,
):
    start = _next_weekday()
    response = client.post(
        "/api/v1/hr/leave-requests",
        headers=employee_token_headers,
        json={
            "leave_type": "ANNUAL",
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
            "part_of_day": "AFTERNOON",
            "reason": "Khám sức khỏe định kỳ",
        },
    )
    assert response.status_code == 201, response.text
    request_data = response.json()
    assert request_data["requested_days"] == 0.5
    assert request_data["status"] == "WAITING"

    reserved = client.get("/api/v1/hr/leave-balance", headers=employee_token_headers)
    assert reserved.json()["reserved_days"] == 0.5
    approval_id = request_data["approval_id"]
    approved = client.post(
        f"/api/v1/approvals/{approval_id}/action",
        headers=ceo_token_headers,
        json={"action": "APPROVE", "comments": "Đồng ý"},
    )
    assert approved.status_code == 200, approved.text

    balance = client.get("/api/v1/hr/leave-balance", headers=employee_token_headers).json()
    assert balance["used_days"] == 2.5
    assert balance["reserved_days"] == 0
    assert balance["remaining_days"] == 9.5
    events = client.get("/api/v1/hr/calendar-events", headers=employee_token_headers)
    assert events.status_code == 200
    assert any(item["source_id"] == request_data["id"] for item in events.json())

    duplicate = client.post(
        f"/api/v1/approvals/{approval_id}/action",
        headers=ceo_token_headers,
        json={"action": "APPROVE"},
    )
    assert duplicate.status_code == 409

    leave_request = transactional_db_session.query(LeaveRequest).filter(
        LeaveRequest.id == uuid.UUID(request_data["id"])
    ).one()
    balance_row = transactional_db_session.query(LeaveBalance).filter(
        LeaveBalance.user_id == leave_request.employee_id,
        LeaveBalance.year == start.year,
    ).one()
    assert transactional_db_session.query(LeaveLedger).filter(
        LeaveLedger.balance_id == balance_row.id,
        LeaveLedger.leave_request_id == leave_request.id,
    ).count() == 2
    assert transactional_db_session.query(HRCalendarEvent).filter(
        HRCalendarEvent.source_id == str(leave_request.id)
    ).count() == 1
    assert transactional_db_session.query(AuditLog).filter(
        AuditLog.resource_type == "LEAVE_REQUEST",
        AuditLog.resource_id == str(leave_request.id),
    ).count() >= 1


def test_contract_tracking_and_onboarding_workflow(
    client,
    employee_token_headers,
    ceo_token_headers,
    transactional_db_session,
):
    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    contract_number = f"HD-{uuid.uuid4().hex[:8]}"
    forbidden = client.post(
        "/api/v1/hr/contracts",
        headers=employee_token_headers,
        json={
            "employee_id": str(employee.id),
            "contract_number": contract_number,
            "contract_type": "PROBATION",
            "start_date": date.today().isoformat(),
        },
    )
    assert forbidden.status_code == 403

    contract = client.post(
        "/api/v1/hr/contracts",
        headers=ceo_token_headers,
        json={
            "employee_id": str(employee.id),
            "contract_number": contract_number,
            "contract_type": "PROBATION",
            "start_date": date.today().isoformat(),
            "end_date": (date.today() + timedelta(days=30)).isoformat(),
            "probation_end_date": (date.today() + timedelta(days=7)).isoformat(),
            "document_name": "hop-dong-thu-viec.pdf",
        },
    )
    assert contract.status_code == 201, contract.text

    employee_contracts = client.get("/api/v1/hr/contracts", headers=employee_token_headers)
    assert any(item["contract_number"] == contract_number for item in employee_contracts.json())
    scan = client.post("/api/v1/hr/reminders/scan", headers=ceo_token_headers)
    assert scan.status_code == 200, scan.text
    assert scan.json()["matched_contracts"] >= 1

    hr_headers = _login(client, "hr.manager@company.com")
    onboarding = client.post(
        "/api/v1/hr/onboarding",
        headers=hr_headers,
        json={
            "employee_id": str(employee.id),
            "start_date": _next_weekday(14).isoformat(),
            "probation_end_date": (_next_weekday(14) + timedelta(days=60)).isoformat(),
        },
    )
    assert onboarding.status_code == 201, onboarding.text
    data = onboarding.json()
    assert data["progress"] == {"completed": 0, "total": 8}
    case = transactional_db_session.query(OnboardingCase).filter(
        OnboardingCase.id == uuid.UUID(data["id"])
    ).one()
    assert transactional_db_session.query(Task).filter(
        Task.id.in_([step.task_id for step in case.steps])
    ).count() == 8
