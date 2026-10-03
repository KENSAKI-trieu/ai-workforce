"""Month-by-month and expense reports, and the charts drawn from the Finance reports."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.domains.finance.reports import account_trend, expense_breakdown, periods_between
from app.domains.finance.settings import get_settings, seed_chart
from app.domains.finance.visuals import (
    aging_chart,
    balance_chart,
    budget_chart,
    charts_from_tool_calls,
    expense_chart,
    trend_chart,
)
from app.models.models import FinLedgerLine, User
from app.tools.registry import tool_registry
from tests.finance_helpers import login, person

# Each test uses its own far-off periods, so rows other tests leave in the module never mix in.


@pytest.fixture()
def tenant_id(transactional_db_session):
    db = transactional_db_session
    tenant = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    get_settings(db, tenant)
    seed_chart(db, tenant, "TT200")
    db.commit()
    return tenant


def _entry(db, tenant_id, day, debit_account, credit_account, amount, *, voucher=None, department=None):
    for code, dr, cr in ((debit_account, amount, "0"), (credit_account, "0", amount)):
        db.add(FinLedgerLine(
            tenant_id=tenant_id, period=f"{day.year:04d}-{day.month:02d}", entry_date=day,
            account_code=code, debit=Decimal(dr), credit=Decimal(cr), source="IMPORT",
            voucher_no=voucher, department=department if code.startswith(("6", "8")) else None,
            description="thử",
        ))


def test_periods_run_across_a_year_end():
    assert periods_between("2041-11", "2042-02") == ["2041-11", "2041-12", "2042-01", "2042-02"]
    assert periods_between("02/2042", "2041-12") == ["2041-12", "2042-01", "2042-02"]


def test_a_trend_is_each_month_balance_one_after_another(tenant_id, transactional_db_session):
    db = transactional_db_session
    _entry(db, tenant_id, date(2041, 1, 5), "1121", "411", "500000000")
    _entry(db, tenant_id, date(2041, 2, 9), "6428", "1121", "120000000")
    _entry(db, tenant_id, date(2041, 3, 2), "1121", "5111", "80000000")
    db.commit()
    trend = account_trend(db, tenant_id, "112", "2041-01", "2041-03")
    closings = [row["closing"]["debit"] for row in trend["periods"]]
    opening_jan = Decimal(trend["periods"][0]["opening"]["debit"])
    assert [Decimal(value) - opening_jan for value in closings] == [
        Decimal("500000000"), Decimal("380000000"), Decimal("460000000"),
    ]
    balance_line, movements = trend_chart(trend)
    assert balance_line["chart"] == "line" and balance_line["categories"] == ["01/2041", "02/2041", "03/2041"]
    assert balance_line["series"][0]["values"] == closings
    # Balances and movements never share one axis.
    assert [series["key"] for series in movements["series"]] == ["period_debit", "period_credit"]


def test_expenses_leave_out_the_close_to_911(tenant_id, transactional_db_session):
    db = transactional_db_session
    marketing, sales = f"MK{uuid.uuid4().hex[:4]}", f"SL{uuid.uuid4().hex[:4]}"
    _entry(db, tenant_id, date(2043, 6, 3), "6428", "1121", "30000000", voucher="PC1", department=marketing)
    _entry(db, tenant_id, date(2043, 6, 8), "6418", "1121", "10000000", voucher="PC2", department=sales)
    _entry(db, tenant_id, date(2043, 6, 30), "911", "6428", "30000000", voucher="KC06")
    _entry(db, tenant_id, date(2043, 6, 30), "911", "6418", "10000000", voucher="KC06")
    db.commit()
    by_account = expense_breakdown(db, tenant_id, "2043-06", "2043-06")
    assert [(row["key"], row["amount"], row["share_percent"]) for row in by_account["rows"]] == [
        ("642", "30000000.00", "75.00"), ("641", "10000000.00", "25.00"),
    ]
    assert by_account["total"] == "40000000.00"
    by_department = expense_breakdown(db, tenant_id, "2043-06", "2043-06", group_by="department")
    assert {row["name"] for row in by_department["rows"]} == {marketing.upper(), sales.upper()}
    chart = expense_chart(by_account)[0]
    assert chart["chart"] == "pie"
    assert [part["value"] for part in chart["parts"]] == ["30000000.00", "10000000.00"]


def test_charts_carry_only_the_report_figures():
    budget = {
        "period": "2026-09", "source": "s",
        "rows": [
            {"department": "MARKETING", "account": "6428", "budget": "50000000.00", "actual": "62000000.00", "over_budget": True},
            {"department": "SALES", "account": "6418", "budget": "80000000.00", "actual": "45000000.00", "over_budget": False},
        ],
    }
    spec = budget_chart(budget)[0]
    assert spec["series"][1]["values"] == ["62000000.00", "45000000.00"]
    assert spec["subtitle"] == "Vượt ngân sách: MARKETING"

    aging = {
        "kind": "RECEIVABLE", "as_of": "2026-10-03", "source": "s",
        "buckets": {"NOT_DUE": "11000000.00", "1_30": "58000000.00", "31_60": "0.00", "61_90": "99000000.00", "OVER_90": "0.00"},
        "parties": [
            {"party": "Phương Nam", "NOT_DUE": "11000000.00", "1_30": "0.00", "31_60": "0.00", "61_90": "99000000.00", "OVER_90": "0.00"},
            {"party": "Bắc Hà", "NOT_DUE": "0.00", "1_30": "58000000.00", "31_60": "0.00", "61_90": "0.00", "OVER_90": "0.00"},
        ],
    }
    spec = aging_chart(aging)[0]
    # Empty buckets draw no series; the order of the rest follows the buckets.
    assert [series["key"] for series in spec["series"]] == ["NOT_DUE", "1_30", "61_90"]
    assert spec["ordinal"] and {part["value"] for part in spec["parts"]} == {"11000000.00", "58000000.00", "99000000.00"}

    balance = {
        "account": "642", "account_name": "Chi phí quản lý", "period": "2026-09", "two_sided": False,
        "ledger_rows": 3, "source": "s",
        "opening": {"debit": "10000000.00", "credit": "0.00"}, "period_debit": "62000000.00",
        "period_credit": "2000000.00", "closing": {"debit": "70000000.00", "credit": "0.00"},
    }
    steps = balance_chart(balance)[0]["series"][0]["values"]
    assert steps == ["10000000.00", "62000000.00", "-2000000.00", "70000000.00"]
    # A two-sided account has no single running figure to step through.
    assert balance_chart({**balance, "two_sided": True}) == []


def test_a_chat_turn_gets_charts_only_from_results_it_read():
    budget = {"period": "2026-09", "source": "s", "rows": [
        {"department": "A", "account": "642", "budget": "1.00", "actual": "2.00", "over_budget": True},
    ]}
    calls = [
        {"name": "budget_vs_actual", "status": "SUCCESS", "result": budget},
        {"name": "budget_vs_actual", "status": "FAILED", "result": budget},
        {"name": "lookup_invoices", "status": "SUCCESS", "result": {"invoices": []}},
        {"name": "ar_ap_aging", "status": "SUCCESS", "result": {"found": False, "message": "x"}},
        {"name": "get_account_balance", "status": "SUCCESS", "result": {"broken": True}},
    ]
    cards = charts_from_tool_calls(calls)
    assert len(cards) == 1 and cards[0]["title"] == "Ngân sách và thực chi 09/2026"


def test_the_new_tools_read_the_ledger_and_the_pages_get_their_charts(client, tenant_id, transactional_db_session):
    for name in ("get_account_trend", "get_expense_breakdown"):
        definition = tool_registry.get(name)
        assert definition.acl.permission == "finance.ledger.view" and not definition.terminal
    db = transactional_db_session
    _entry(db, tenant_id, date(2044, 2, 4), "6421", "1111", "7000000", voucher="PC9")
    db.commit()
    reader = person(db, "finance.ledger.view")
    headers = login(client, reader)
    expenses = client.get(
        "/api/v1/finance/reports/expenses", params={"from_period": "2044-02", "to_period": "2044-02"}, headers=headers,
    )
    assert expenses.status_code == 200, expenses.text
    assert expenses.json()["charts"][0]["parts"] == [{"name": "642 Chi phí quản lý doanh nghiệp", "value": "7000000.00"}]
    trend = client.get(
        "/api/v1/finance/reports/account-trend",
        params={"account": "642", "from_period": "2044-01", "to_period": "2044-02"}, headers=headers,
    )
    assert trend.status_code == 200, trend.text
    assert len(trend.json()["charts"]) == 2
    outsider = person(db, "finance.ar_ap.view")
    refused = client.get(
        "/api/v1/finance/reports/expenses", params={"from_period": "2044-02", "to_period": "2044-02"},
        headers=login(client, outsider),
    )
    assert refused.status_code == 403
