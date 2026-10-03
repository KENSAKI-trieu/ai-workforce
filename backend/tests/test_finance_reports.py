"""The figures the Finance agent reads: balances, trial balance, ledger, budget, aging."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domains.finance.reports import account_balance, aging, ledger_detail, payment_schedule, trial_balance
from app.domains.finance.settings import get_settings, seed_chart
from app.models.models import FinBudget, FinInvoice, FinLedgerLine, FinParty, FinPayment, User
from app.tools.executors.finance import get_budget_vs_actual, lookup_invoices
from app.tools.registry import ToolContext
from app.tools.schemas import BudgetVsActualInput, InvoiceLookupInput
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
            series="AG", number=number, issue_date=due - timedelta(days=30), due_date=due, status=status,
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


def _unique(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:4].translate(str.maketrans("abcdef", "123456"))


def test_a_two_sided_account_keeps_debts_and_advances_apart(tenant_id, transactional_db_session):
    db = transactional_db_session
    owes, advanced = (
        FinParty(tenant_id=tenant_id, kind="CUSTOMER", tax_code=unique_tax_code(), name=f"Khách {name}")
        for name in ("nợ", "trả trước")
    )
    db.add_all([owes, advanced])
    db.flush()
    account = _unique("1319")
    _line(db, tenant_id, account, date(2034, 2, 3), debit="100000000", party_id=owes.id)
    _line(db, tenant_id, account, date(2034, 2, 4), credit="30000000", party_id=advanced.id)
    db.commit()
    # 70.000.000 on the debit side would hide both the debt and the advance.
    balance = account_balance(db, tenant_id, account, "2034-02")
    assert balance["two_sided"]
    assert balance["closing"] == {"debit": "100000000.00", "credit": "30000000.00"}

    def closing_131(period):
        rows = trial_balance(db, tenant_id, period)["rows"]
        return next((row["closing"] for row in rows if row["account"] == "131"), {"debit": "0", "credit": "0"})

    before, after = closing_131("2034-01"), closing_131("2034-02")
    assert Decimal(after["debit"]) - Decimal(before["debit"]) == Decimal("100000000")
    assert Decimal(after["credit"]) - Decimal(before["credit"]) == Decimal("30000000")
    assert trial_balance(db, tenant_id, "2034-02")["balanced"]

    across = ledger_detail(db, tenant_id, account, date(2034, 2, 1), date(2034, 2, 28))
    assert across["closing"] == {"debit": "100000000.00", "credit": "30000000.00"}
    assert all(line["balance"] is None for line in across["lines"])
    one = ledger_detail(db, tenant_id, account, date(2034, 2, 1), date(2034, 2, 28), party_id=owes.id)
    assert one["lines"][-1]["balance"] == {"debit": "100000000.00", "credit": "0.00"}


def test_the_ledger_detail_totals_the_whole_range_when_truncated(tenant_id, transactional_db_session):
    db = transactional_db_session
    account = _unique("1129")
    for day, debit, credit in ((1, "1000000", "0"), (2, "0", "300000"), (3, "500000", "0")):
        _line(db, tenant_id, account, date(2036, 3, day), debit=debit, credit=credit)
    db.commit()
    detail = ledger_detail(db, tenant_id, account, date(2036, 3, 1), date(2036, 3, 31), limit=1)
    assert detail["truncated"] and len(detail["lines"]) == 1
    assert (detail["period_debit"], detail["period_credit"]) == ("1500000.00", "300000.00")
    assert detail["closing"] == {"debit": "1200000.00", "credit": "0.00"}


def test_aging_on_a_past_date_shows_what_was_open_then(tenant_id, transactional_db_session):
    db = transactional_db_session
    customer = FinParty(tenant_id=tenant_id, kind="CUSTOMER", tax_code=unique_tax_code(), name="Khách tuổi nợ quá khứ")
    db.add(customer)
    db.flush()
    settled = FinInvoice(
        tenant_id=tenant_id, direction="OUT", party_id=customer.id, seller_tax_code="", series="HX", number="1",
        issue_date=date(2035, 5, 1), due_date=date(2035, 5, 31), status="PAID",
        amount_before_tax=Decimal("50000000"), vat_amount=Decimal("0"), total_amount=Decimal("50000000"),
    )
    later = FinInvoice(
        tenant_id=tenant_id, direction="OUT", party_id=customer.id, seller_tax_code="", series="HX", number="2",
        issue_date=date(2035, 7, 10), due_date=date(2035, 8, 10), status="POSTED",
        amount_before_tax=Decimal("20000000"), vat_amount=Decimal("0"), total_amount=Decimal("20000000"),
    )
    db.add_all([settled, later])
    db.flush()
    db.add(FinPayment(tenant_id=tenant_id, invoice_id=settled.id, party_id=customer.id, direction="RECEIVE",
                      amount=Decimal("50000000"), status="PAID", payment_date=date(2035, 7, 20)))
    db.commit()
    # On 30/06 the first invoice was 30 days overdue and the second not yet issued.
    june = aging(db, tenant_id, "OUT", date(2035, 6, 30), party_id=customer.id)
    assert june["total"] == "50000000.00"
    assert [(row["number"], row["days_overdue"]) for row in june["invoices"]] == [("1", 30)]
    july = aging(db, tenant_id, "OUT", date(2035, 7, 31), party_id=customer.id)
    assert [row["number"] for row in july["invoices"]] == ["2"]


def test_an_account_without_a_department_sees_no_budget_of_its_own(tenant_id, transactional_db_session):
    db = transactional_db_session
    db.add(FinBudget(tenant_id=tenant_id, department="ALL", account_code="6428", period="2037-01", amount=Decimal("1")))
    db.commit()
    for department in ("ALL", ""):
        user = person(db, "finance.budget.view_own", department="X")
        user.department = department
        db.commit()
        request = BudgetVsActualInput.model_construct(tenant_id=tenant_id, audit=None, period="2037-01", department=None)
        assert get_budget_vs_actual(ToolContext(db=db, actor=user), request)["found"] is False


def test_invoices_are_found_by_their_counterparty_not_by_the_company_itself(tenant_id, transactional_db_session):
    db = transactional_db_session
    marker = uuid.uuid4().hex[:6]
    customer = FinParty(tenant_id=tenant_id, kind="CUSTOMER", tax_code=unique_tax_code(), name=f"Khách {marker}")
    db.add(customer)
    db.flush()
    db.add(FinInvoice(
        tenant_id=tenant_id, direction="OUT", party_id=customer.id, seller_tax_code="0100000000",
        seller_name=f"Công ty Chính {marker}", buyer_name=customer.name, buyer_tax_code=customer.tax_code,
        series="LK", number="1", issue_date=date(2038, 1, 1), status="POSTED",
        amount_before_tax=Decimal("1"), vat_amount=Decimal("0"), total_amount=Decimal("1"),
    ))
    db.commit()
    clerk = person(db, "finance.invoice.process")

    def find(term):
        request = InvoiceLookupInput.model_construct(
            tenant_id=tenant_id, audit=None, status=None, direction=None, party=term, number=None, period=None, limit=20,
        )
        return lookup_invoices(ToolContext(db=db, actor=clerk), request)["count"]

    assert find(f"Chính {marker}") == 0
    assert find(f"Khách {marker}") == 1
    assert find(customer.tax_code) == 1
