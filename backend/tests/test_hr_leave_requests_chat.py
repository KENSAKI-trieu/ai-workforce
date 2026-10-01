"""Leave requests through the HR chat: who is off, whose requests, withdrawing one.

The router and the lookup arguments come from a scripted provider, so these pin what the
branches do with a label -- above all that another person's leave is shown only within the
asker's reach and without its reason, and that withdrawing touches only the asker's own
request while it still awaits approval.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.hr import llm_flow as hr_llm_flow
from app.models.models import LeaveBalance, LeaveRequest, User, WorkflowApproval
from tests.test_llm_routing_integration import ScriptedAIClient


@pytest.fixture
def scripted(monkeypatch):
    def install(**labels):
        client = ScriptedAIClient(**labels)
        monkeypatch.setattr(hr_llm_flow, "get_ai_service_client", lambda: client)
        return client

    return install


@pytest.fixture
def it_lead_headers(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "it.lead@company.com", "password": "Password123!"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _chat(client, headers, message: str) -> dict:
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": message},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tools(data: dict) -> list[str]:
    return [item["tool_name"] for item in data["tools_executed"]]


def _submit_leave(client, headers, scripted, start: str, end: str) -> None:
    scripted(
        hr_intent={"kind": "ACTION", "intent": "ACTION_LEAVE_REQUEST"},
        leave_slot={"start_date": start, "end_date": end, "reason": "việc gia đình riêng"},
    )
    data = _chat(client, headers, f"Tôi xin nghỉ từ {start} đến {end} vì việc gia đình riêng")
    assert "request_leave" in _tools(data), data["reply"]


def _reserved_days(db, email: str, year: int) -> Decimal:
    employee = db.query(User).filter(User.email == email).one()
    balance = db.query(LeaveBalance).filter(
        LeaveBalance.user_id == employee.id, LeaveBalance.year == year
    ).one()
    db.refresh(balance)
    return Decimal(balance.reserved_days)


def test_a_manager_sees_who_is_off_without_the_reason(
    client, employee_token_headers, it_lead_headers, scripted
):
    _submit_leave(client, employee_token_headers, scripted, "2032-02-02", "2032-02-03")
    scripted(
        hr_intent={"kind": "QUESTION", "intent": "EMPLOYEE_LEAVE_STATUS_COUNT"},
        lookup={"start_date": "2032-02-02", "end_date": "2032-02-02"},
    )

    data = _chat(client, it_lead_headers, "Ngày 2/2/2032 team mình ai nghỉ?")

    card = data["hr_card"]
    assert card["type"] == "LEAVE_CALENDAR"
    assert card["scope"] == "REPORTING_TREE"
    mine = [item for item in card["items"] if item["employee"]["name"] == "Lê Văn Nhẫn"]
    assert [item["status"] for item in mine] == ["WAITING"]
    # The approver has the reason on the approval card; a "who is off" list does not.
    assert "reason" not in mine[0]
    assert "đang chờ duyệt" in data["reply"]


def test_without_the_leave_grant_the_calendar_is_the_asker_alone(
    client, employee_token_headers, scripted
):
    scripted(
        hr_intent={"kind": "QUESTION", "intent": "EMPLOYEE_LEAVE_STATUS_COUNT"},
        lookup={"start_date": "2032-02-02", "end_date": "2032-02-02"},
    )

    data = _chat(client, employee_token_headers, "Ngày 2/2/2032 ai nghỉ?")

    card = data["hr_card"]
    assert card["scope"] == "SELF"
    assert {item["employee"]["name"] for item in card["items"]} <= {"Lê Văn Nhẫn"}
    assert "chưa được cấp quyền" in data["reply"]


def test_own_requests_and_the_team_view_follow_the_leave_grant(
    client, employee_token_headers, it_lead_headers, scripted
):
    _submit_leave(client, employee_token_headers, scripted, "2032-03-01", "2032-03-01")

    scripted(
        hr_intent={"kind": "QUESTION", "intent": "LEAVE_REQUEST_STATUS"},
        lookup={"whose": "SELF", "start_date": "2032-03-01", "end_date": "2032-03-01"},
    )
    own = _chat(client, employee_token_headers, "Đơn nghỉ 1/3/2032 của tôi duyệt chưa?")
    assert own["hr_card"]["type"] == "LEAVE_REQUESTS"
    assert [item["status"] for item in own["hr_card"]["items"]] == ["WAITING"]
    assert own["hr_card"]["items"][0]["reason"] == "việc gia đình riêng"
    assert "Chờ duyệt" in own["reply"]

    scripted(
        hr_intent={"kind": "QUESTION", "intent": "LEAVE_REQUEST_STATUS"},
        lookup={"whose": "TEAM"},
    )
    refused = _chat(client, employee_token_headers, "Đơn nghỉ của cả phòng thế nào?")
    assert refused["hr_card"] is None
    assert "chưa được cấp quyền" in refused["reply"]

    team = _chat(client, it_lead_headers, "Đơn nghỉ của nhân viên tôi thế nào?")
    items = team["hr_card"]["items"]
    assert any(item["start_date"] == "2032-03-01" for item in items)
    assert all("reason" not in item for item in items)


def test_withdrawing_a_waiting_request_releases_its_days_and_closes_the_card(
    client, employee_token_headers, it_lead_headers, scripted, transactional_db_session
):
    db = transactional_db_session
    before = _reserved_days_or_zero(db, "employee@company.com", 2032)
    _submit_leave(client, employee_token_headers, scripted, "2032-04-05", "2032-04-06")
    assert _reserved_days(db, "employee@company.com", 2032) == before + 2

    scripted(
        hr_intent={"kind": "ACTION", "intent": "ACTION_LEAVE_CANCEL"},
        lookup={"start_date": "2032-04-05", "end_date": "2032-04-05"},
    )
    data = _chat(client, employee_token_headers, "Rút giúp tôi đơn nghỉ ngày 5/4/2032")

    assert _tools(data) == ["cancel_leave_request"]
    assert data["hr_card"]["items"][0]["status"] == "CANCELLED"
    record = db.query(LeaveRequest).filter(
        LeaveRequest.start_date == "2032-04-05"
    ).one()
    db.refresh(record)
    assert record.status == "CANCELLED"
    approval = db.get(WorkflowApproval, record.approval_id)
    db.refresh(approval)
    assert approval.status == "CANCELLED"
    assert _reserved_days(db, "employee@company.com", 2032) == before

    # The approver's card can no longer decide a request that is gone.
    decided = client.post(
        f"/api/v1/approvals/{approval.id}/action",
        json={"action": "APPROVE"},
        headers=it_lead_headers,
    )
    assert decided.status_code == 409


def test_an_ambiguous_withdrawal_asks_which_request(
    client, employee_token_headers, scripted, transactional_db_session
):
    _submit_leave(client, employee_token_headers, scripted, "2032-05-03", "2032-05-03")
    _submit_leave(client, employee_token_headers, scripted, "2032-05-10", "2032-05-10")
    scripted(
        hr_intent={"kind": "ACTION", "intent": "ACTION_LEAVE_CANCEL"},
        lookup={"start_date": "2032-05-01", "end_date": "2032-05-31"},
    )

    data = _chat(client, employee_token_headers, "Rút đơn nghỉ tháng 5/2032 của tôi")

    assert "cancel_leave_request" not in _tools(data)
    assert "đơn nào" in data["reply"]
    statuses = {
        row.status
        for row in transactional_db_session.query(LeaveRequest).filter(
            LeaveRequest.start_date.in_(["2032-05-03", "2032-05-10"])
        )
    }
    assert statuses == {"WAITING"}


def test_asking_how_to_withdraw_withdraws_nothing(
    client, employee_token_headers, scripted
):
    scripted(hr_intent={"kind": "QUESTION", "intent": "ACTION_LEAVE_CANCEL"})

    data = _chat(client, employee_token_headers, "Muốn rút đơn nghỉ thì làm thế nào?")

    assert "cancel_leave_request" not in _tools(data)


def _reserved_days_or_zero(db, email: str, year: int) -> Decimal:
    employee = db.query(User).filter(User.email == email).one()
    balance = db.query(LeaveBalance).filter(
        LeaveBalance.user_id == employee.id, LeaveBalance.year == year
    ).first()
    if balance is None:
        return Decimal("0")
    db.refresh(balance)
    return Decimal(balance.reserved_days)
