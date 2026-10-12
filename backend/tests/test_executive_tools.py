"""The CEO assistant: the briefing, the decisions waiting, and the work it hands out.

What is pinned: each section of the briefing keeps its department's permission and says so
when it is withheld; every change and percentage is worked out here, not by the model; the
inbox is the Approvals page's own list, most pressing first; a task goes to exactly one
person or is asked back; and the placeholder agent's grants are swapped for the real ones.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.finance_capabilities import CEO_RETIRED_TOOLS, CEO_TOOL_INTRODUCTIONS, versioned_default_tools
from app.core.security import create_internal_tool_token, get_password_hash
from app.domains.executive.briefing import build_briefing, compare_income_statements, window_for
from app.domains.executive.inbox import approvals_inbox
from app.domains.executive.tasks import assign_task, list_tasks
from app.domains.finance.settings import get_settings, seed_chart
from app.domains.platform.auth_service import upgrade_agent_grants
from app.models.models import (
    AgentWorkflow,
    AIAgent,
    FinLedgerLine,
    Notification,
    Task,
    User,
    WorkflowApproval,
)
from tests.finance_helpers import person


@pytest.fixture()
def admin(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "admin@company.com").one()


@pytest.fixture()
def employee(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "employee@company.com").one()


def _colleague(db, tenant_id, full_name: str, department: str = "SALES") -> User:
    user = User(
        id=uuid.uuid4(), tenant_id=tenant_id, email=f"exec-{uuid.uuid4().hex[:8]}@company.com",
        full_name=full_name, password_hash=get_password_hash("Password123!"),
        role="Employee", department=department, is_active=True,
    )
    db.add(user)
    db.commit()
    return user


# --------------------------------------------------------------------------- briefing


def test_each_period_reads_its_months_and_the_ones_before() -> None:
    monday = date(2026, 10, 12)
    week = window_for("WEEK", monday)
    assert (week.start, week.end) == (date(2026, 10, 12), date(2026, 10, 18))
    assert (week.months, week.previous_months) == (("2026-10", "2026-10"), ("2026-09", "2026-09"))
    quarter = window_for("QUARTER", monday)
    assert (quarter.start, quarter.end) == (date(2026, 10, 1), date(2026, 12, 31))
    assert quarter.months == ("2026-10", "2026-10")
    assert quarter.previous_months == ("2026-07", "2026-09")
    assert quarter.partial is True
    january = window_for("MONTH", date(2027, 1, 5))
    assert january.previous_months == ("2026-12", "2026-12")


def test_a_section_the_position_may_not_see_says_so_and_names_the_box(transactional_db_session) -> None:
    db = transactional_db_session
    director = person(db, "executive.briefing.view")

    briefing = build_briefing(db, director, period="MONTH")

    finance = briefing["sections"]["finance"]
    assert finance["permitted"] is False
    assert "Xem sổ sách kế toán" in finance["needs"]
    assert briefing["sections"]["ai_cost"]["permitted"] is False
    people = briefing["sections"]["people"]
    assert people["headcount"]["permitted"] is False
    assert people["contracts_ending"]["permitted"] is False
    # Their own records are no secret: these read in their own scope.
    assert briefing["sections"]["legal"]["scope"] == "chỉ hợp đồng do bạn rà soát"
    assert briefing["sections"]["marketing"]["scope"] == "chỉ chiến dịch của bạn"
    assert briefing["sections"]["approvals"]["waiting_count"] == 0


def test_the_results_compare_with_the_month_before_worked_out_here(
    transactional_db_session, admin, monkeypatch
) -> None:
    db = transactional_db_session
    get_settings(db, admin.tenant_id)
    seed_chart(db, admin.tenant_id, "TT200")
    for day, revenue, cost in ((date(2032, 2, 10), "100000000", "10000000"), (date(2032, 3, 10), "150000000", "20000000")):
        for account, debit, credit in (("5111", "0", revenue), ("6421", cost, "0")):
            for code, dr, cr in ((account, debit, credit), ("1388", credit, debit)):
                db.add(FinLedgerLine(
                    tenant_id=admin.tenant_id, period=f"{day.year}-{day.month:02d}", entry_date=day,
                    account_code=code, debit=Decimal(dr), credit=Decimal(cr), description="thử bản tin",
                ))
    db.commit()
    monkeypatch.setattr("app.domains.executive.briefing.local_today", lambda db, user: date(2032, 3, 15))

    results = build_briefing(db, admin, period="MONTH", sections=["FINANCE"])["sections"]["finance"]["results"]

    assert (results["revenue"], results["previous_revenue"]) == ("150000000.00", "100000000.00")
    assert results["revenue_vs_previous"] == {"change": "50000000.00", "change_percent": "50.00", "direction": "tăng"}
    assert (results["profit_before_tax"], results["previous_profit_before_tax"]) == ("130000000.00", "90000000.00")
    assert results["profit_vs_previous"]["change"] == "40000000.00"
    assert results["profit_vs_previous"]["change_percent"] == "44.44"
    assert results["so_far"] is True

    # Any two periods side by side, line by line, with the change worked out.
    lines = {
        line["label"]: line
        for line in compare_income_statements(db, admin, ("2032-03", "2032-03"), ("2032-02", "2032-02"))["lines"]
    }
    revenue = lines["Doanh thu thuần"]
    assert (revenue["amount"], revenue["compared_amount"], revenue["change_percent"]) == (
        "150000000.00", "100000000.00", "50.00",
    )
    assert lines["Tổng lợi nhuận kế toán trước thuế"]["change"] == "40000000.00"


# --------------------------------------------------------------------------- approvals


def _approval(db, requester: User, title: str, risk: str) -> WorkflowApproval:
    workflow = AgentWorkflow(
        tenant_id=requester.tenant_id, initiator_id=requester.id, title=title,
        status="AWAITING_APPROVAL", current_step=1, dag_plan={"agent_role": "SYSTEM"},
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        workflow_id=workflow.id, action_type="SUPPORT_EMAIL_SEND", risk_level=risk, status="WAITING",
        payload={"requester_id": str(requester.id), "requester_name": requester.full_name},
    )
    db.add(approval)
    db.commit()
    return approval


def test_the_inbox_is_what_the_user_may_decide_most_pressing_first(
    transactional_db_session, admin, employee
) -> None:
    db = transactional_db_session
    tag = uuid.uuid4().hex[:6]
    for risk in ("LOW", "CRITICAL", "HIGH"):
        _approval(db, employee, f"Hộp duyệt {tag} {risk}", risk)

    inbox = approvals_inbox(db, admin, limit=30)
    ours = [item for item in inbox["items"] if tag in item["title"]]
    assert [item["risk"] for item in ours] == ["rất cao", "cao", "thấp"]
    assert all(item["link"].startswith("/approvals?id=") for item in ours)
    assert inbox["waiting_count"] >= 3
    # Someone who may not sign anything has nothing waiting.
    assert not [item for item in approvals_inbox(db, employee, limit=30)["items"] if tag in item["title"]]


# --------------------------------------------------------------------------- tasks


def test_a_task_goes_to_the_one_person_named_who_is_told(transactional_db_session, admin) -> None:
    db = transactional_db_session
    tag = uuid.uuid4().hex[:4]
    colleague = _colleague(db, admin.tenant_id, f"Lê Thị Giaoviec{tag}")

    result = assign_task(
        db, admin, assignee=f"chị giaoviec{tag}", title="Chuẩn bị báo cáo công nợ",
        due_date=date(2032, 1, 9), priority="HIGH",
    )

    assert result["created"] is True, result["reply"]
    task = db.query(Task).filter(Task.id == uuid.UUID(result["task_id"])).one()
    assert (task.assignee_id, task.creator_id, task.priority) == (colleague.id, admin.id, "HIGH")
    assert task.due_date is not None
    assert "09/01/2032" in result["reply"] and colleague.full_name in result["reply"]
    assert db.query(Notification).filter(
        Notification.user_id == colleague.id, Notification.event_type == "TASK_ASSIGNED",
        Notification.entity_id == str(task.id),
    ).count() == 1


def test_a_name_that_fits_several_people_is_asked_back(transactional_db_session, admin) -> None:
    db = transactional_db_session
    tag = uuid.uuid4().hex[:4]
    _colleague(db, admin.tenant_id, f"Ngô Văn Trung{tag}")
    _colleague(db, admin.tenant_id, f"Phạm Thị Trung{tag}")
    before = db.query(Task).filter(Task.creator_id == admin.id).count()

    result = assign_task(db, admin, assignee=f"Trung{tag}", title="Gọi khách hàng")

    assert result["created"] is False
    assert f"Ngô Văn Trung{tag}" in result["reply"] and f"Phạm Thị Trung{tag}" in result["reply"]
    assert db.query(Task).filter(Task.creator_id == admin.id).count() == before


def test_nobody_found_and_a_past_deadline_create_nothing(transactional_db_session, admin) -> None:
    db = transactional_db_session
    tag = uuid.uuid4().hex[:4]
    _colleague(db, admin.tenant_id, f"Đỗ Minh Hanchot{tag}")

    nobody = assign_task(db, admin, assignee="Người Không Tồn Tại Xyz", title="Việc")
    late = assign_task(db, admin, assignee=f"Hanchot{tag}", title="Việc", due_date=date(2020, 1, 1))

    assert nobody["created"] is False and "không tìm thấy" in nobody["reply"]
    assert late["created"] is False and "đã qua" in late["reply"]


def test_overdue_work_handed_out_is_listed_as_overdue(transactional_db_session, admin) -> None:
    db = transactional_db_session
    colleague = _colleague(db, admin.tenant_id, f"Vũ Quá Hạn {uuid.uuid4().hex[:4]}")
    task = Task(
        tenant_id=admin.tenant_id, title=f"Báo cáo trễ {uuid.uuid4().hex[:4]}", creator_id=admin.id,
        assignee_id=colleague.id, priority="MEDIUM", status="RUNNING",
        due_date=datetime(2020, 1, 1, tzinfo=timezone.utc), attachments=[],
    )
    db.add(task)
    db.commit()

    report = list_tasks(db, admin, scope="ASSIGNED_BY_ME", status="OVERDUE", limit=50)

    item = next(item for item in report["items"] if item["title"] == task.title)
    assert item["status"] == "quá hạn" and item["days_overdue"] > 0
    assert item["assignee"] == colleague.full_name
    assert report["counts"]["overdue"] >= 1


# --------------------------------------------------------------------------- gateway and grants


def test_the_briefing_needs_the_ceo_box_at_the_gateway(client, transactional_db_session, employee) -> None:
    token = create_internal_tool_token(employee)
    response = client.post(
        "/api/v1/internal/tools/executive_briefing/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"input": {
            "tenant_id": str(employee.tenant_id),
            "audit": {"correlation_id": str(uuid.uuid4())},
            "period": "TODAY",
        }},
    )
    assert response.status_code == 403


def test_the_placeholder_ceo_agent_gets_the_real_tools() -> None:
    placeholder = [
        "create_task", "expense_lookup", "generate_and_execute_ceo_dag",
        "generate_legal_document", "rag_search", "submit_approval_request",
    ]
    agent = AIAgent(
        role_code="CEO", tools_access=list(placeholder), allowed_actions=list(placeholder),
        disallowed_actions=[], configuration_version=9,
    )

    assert upgrade_agent_grants(agent) is True

    assert set(agent.tools_access) == set(versioned_default_tools("CEO"))
    assert not set(agent.tools_access) & CEO_RETIRED_TOOLS
    assert set(agent.allowed_actions) == set(agent.tools_access)
    assert agent.configuration_version == max(CEO_TOOL_INTRODUCTIONS)
    assert upgrade_agent_grants(agent) is False
