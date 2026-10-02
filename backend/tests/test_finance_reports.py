"""The figures the Finance agent reads: balances, trial balance, ledger, budget, aging."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.domains.finance.reports import account_balance, aging, ledger_detail, payment_schedule, trial_balance
from app.domains.finance.settings import get_settings, seed_chart
from app.models.models import FinBudget, FinInvoice, FinLedgerLine, FinParty, FinPayment, User
from app.tools.executors.finance import get_budget_vs_actual
from app.tools.registry import ToolContext
from app.tools.schemas import BudgetVsActualInput
from tests.finance_helpers import person, unique_tax_code

# Each test gets its own account range so rows left by other tests in the module never mix in.


@pytest.fixture()
def tenant_id(transactional_db_session):
    db = transactional_db_session
    tenant = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    get_settings(db, tenant)
    seed_chart(db, tenant, "TT200")
    db.commit()
    return tenant


def _line(db, tenant_id, account, day, debit="0", credit="0", *, source="IMPORT", department=None, party_id=None):
    """One side, and its other side on suspense account 1388, so the books stay balanced."""
    for code, dr, cr in ((account, debit, credit), ("1388", credit, debit)):
        db.add(FinLedgerLine(
            tenant_id=tenant_id, period=f"{day.year:04d}-{day.month:02d}", entry_date=day,
            account_code=code, debit=Decimal(dr), credit=Decimal(cr),
            source=source, department=department if code == account else None,
            party_id=party_id if code == account else None, description="thử",
        ))


def test_a_balance_rolls_opening_and_movements_and_covers_sub_accounts(tenant_id, transactional_db_session):
    db = transactional_db_session
    account = "3319" + uuid.uuid4().hex[:4].translate(str.maketrans("abcdef", "123456"))
    _line(db, tenant_id, account, date(2026, 8, 1), credit="100000000", source="OPENING")
    _line(db, tenant_id, account, date(2026, 8, 20), debit="30000000")
    _line(db, tenant_id, account + "1", date(2026, 9, 5), credit="55000000")
    _line(db, tenant_id, account, date(2026, 9, 25), debit="5000000")
    db.commit()
    september = account_balance(db, tenant_id, account, "2026-09")
    assert september["opening"] == {"debit": "0.00", "credit": "70000000.00"}
    assert (september["period_debit"], september["period_credit"]) == ("5000000.00", "55000000.00")
    assert september["closing"] == {"debit": "0.00", "credit": "120000000.00"}
    august = account_balance(db, tenant_id, account, "08/2026")
    # Opening balances loaded for a period are its opening, not its movement.
    assert august["opening"]["credit"] == "100000000.00"
    assert august["period_debit"] == "30000000.00"


def test_the_trial_balance_of_balanced_books_balances(tenant_id, transactional_db_session):
    db = transactional_db_session
    _line(db, tenant_id, "6428", date(2031, 1, 10), debit="2000000")
    db.commit()
    report = trial_balance(db, tenant_id, "2031-01")
    rows = {row["account"]: row for row in report["rows"]}
    assert rows["642"]["period_debit"] == "2000000.00"
    assert rows["138"]["period_credit"] == "2000000.00"
    assert report["balanced"]


def test_the_ledger_detail_runs_its_balance(tenant_id, transactional_db_session):
    db = transactional_db_session
    _line(db, tenant_id, "1121", date(2032, 3, 1), debit="1000000")
    _line(db, tenant_id, "1121", date(2032, 3, 5), credit="400000")
    db.commit()
    detail = ledger_detail(db, tenant_id, "1121", date(2032, 3, 1), date(2032, 3, 31))

    def net(side):
        return Decimal(side["debit"]) - Decimal(side["credit"])

    opening = net(detail["opening"])
    assert [net(line["balance"]) - opening for line in detail["lines"][-2:]] == [Decimal("1000000"), Decimal("600000")]


def test_someone_with_only_their_own_budget_box_sees_only_their_department(tenant_id, transactional_db_session):
    db = transactional_db_session
    period = "2033-04"
    db.add(FinBudget(tenant_id=tenant_id, department="MARKETING", account_code="6428", period=period, amount=Decimal("50000000")))
    db.add(FinBudget(tenant_id=tenant_id, department="SALES", account_code="6418", period=period, amount=Decimal("80000000")))
    _line(db, tenant_id, "6428", date(2033, 4, 10), debit="60000000", department="MARKETING")
    db.commit()
    marketer = person(db, "finance.budget.view_own", department="MARKETING")

    def ask(user, department=None):
        request = BudgetVsActualInput.model_construct(tenant_id=tenant_id, audit=None, period=period, department=department)
        return get_budget_vs_actual(ToolContext(db=db, actor=user), request)

    own = ask(marketer)
    assert [row["department"] for row in own["rows"]] == ["MARKETING"]
    assert own["rows"][0]["over_budget"] and own["rows"][0]["over_percent"] == "20.00"
    refused = ask(marketer, "SALES")
    assert refused["found"] is False
    controller = person(db, "finance.ledger.view")
    assert {row["department"] for row in ask(controller)["rows"]} == {"MARKETING", "SALES"}
    with pytest.raises(Exception):
        ask(person(db))


def test_aging_buckets_what_is_left_after_payments(tenant_id, transactional_db_session):
    db = transactional_db_session
    customer = FinParty(tenant_id=tenant_id, kind="CUSTOMER", tax_code=unique_tax_code(), name="Khách thử tuổi nợ")
    db.add(customer)
    db.flush()

    def sale(number, due, total, paid="0", status="POSTED"):
        invoice = FinInvoice(
            tenant_id=tenant_id, direction="OUT", party_id=customer.id, seller_tax_code="",
            series="AG", number=number, issue_date=due, due_date=due, status=status,
            amount_before_tax=Decimal(total), vat_amount=Decimal("0"), total_amount=Decimal(total),
        )
        db.add(invoice)
        db.flush()
        if paid != "0":
            db.add(FinPayment(tenant_id=tenant_id, invoice_id=invoice.id, party_id=customer.id,
                              direction="RECEIVE", amount=Decimal(paid), status="PAID"))

    sale("1", date(2026, 10, 5), "10000000")                   # due in 5 days: not due
    sale("2", date(2026, 8, 31), "20000000", paid="5000000")   # 30 days over, 15m left
    sale("3", date(2026, 6, 1), "7000000")                     # 121 days over
    sale("4", date(2026, 6, 1), "9000000", paid="9000000")     # fully paid: gone
    sale("5", date(2026, 6, 1), "3000000", status="MATCHED")   # never posted: not a debt yet
    db.commit()
    report = aging(db, tenant_id, "OUT", date(2026, 9, 30), party_id=customer.id)
    assert report["buckets"] == {
        "NOT_DUE": "10000000.00", "1_30": "15000000.00", "31_60": "0.00", "61_90": "0.00", "OVER_90": "7000000.00",
    }
    assert report["total"] == "32000000.00"
    overdue = aging(db, tenant_id, "OUT", date(2026, 9, 30), party_id=customer.id, min_days_overdue=60)
    assert overdue["total"] == "7000000.00"


def test_the_payment_schedule_lists_what_falls_due(tenant_id, transactional_db_session):
    db = transactional_db_session
    vendor = FinParty(tenant_id=tenant_id, kind="VENDOR", tax_code=unique_tax_code(), name="NCC lịch trả")
    db.add(vendor)
    db.flush()
    for number, due in (("1", date(2040, 1, 5)), ("2", date(2040, 1, 30))):
        db.add(FinInvoice(
            tenant_id=tenant_id, direction="IN", party_id=vendor.id, seller_tax_code=vendor.tax_code,
            series="PS", number=number, issue_date=due, due_date=due, status="POSTED",
            amount_before_tax=Decimal("1000000"), vat_amount=Decimal("0"), total_amount=Decimal("1000000"),
        ))
    db.commit()
    schedule = payment_schedule(db, tenant_id, date(2040, 1, 1), 14)
    mine = [row for row in schedule["invoices"] if row["party"] == "NCC lịch trả"]
    assert [row["number"] for row in mine] == ["1"]
