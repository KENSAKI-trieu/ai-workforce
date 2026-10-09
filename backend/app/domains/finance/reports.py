"""Every figure the Finance agent states, computed here from the ledger and invoices.

Each function takes fixed parameters and returns exact decimal strings plus `source`,
which names the rows behind the figure. There is no free-form query: the model chooses
which report to read and for what, never how it is computed.

Balances follow Vietnamese practice: an account's balance at the start of a period is
everything posted before it plus the opening balances loaded for that period; an account
code covers its sub-accounts (331 includes 3311, 3312). An account that can sit on either
side (131, 331, 333 ...) is never netted across parties or sub-accounts: a customer who
owes 100 and another who paid 30 in advance are a debit of 100 and a credit of 30, as the
balance sheet reports them, not a debit of 70.
"""

from __future__ import annotations

import io
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from openpyxl import Workbook
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.domains.finance.charts import normal_balance
from app.domains.finance.money import ZERO, format_vnd, normalize_period, plain
from app.models.models import (
    FinAccount,
    FinBudget,
    FinInvoice,
    FinJournalEntry,
    FinLedgerLine,
    FinParty,
    FinPayment,
)

AGING_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("NOT_DUE", -10**6, 0),
    ("1_30", 1, 30),
    ("31_60", 31, 60),
    ("61_90", 61, 90),
    ("OVER_90", 91, None),
)


def period_bounds(period: str) -> tuple[date, date]:
    """[first day, first day of the next period) of a YYYY-MM period."""
    year, month = int(period[:4]), int(period[5:])
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def _under(code: str):
    """Lines of this account and every sub-account."""
    return FinLedgerLine.account_code.like(f"{code}%")


def _before(period: str):
    """Everything that makes up the balance at the start of `period`."""
    return or_(
        FinLedgerLine.period < period,
        and_(FinLedgerLine.period == period, FinLedgerLine.source == "OPENING"),
    )


def _in(period: str):
    return and_(FinLedgerLine.period == period, FinLedgerLine.source != "OPENING")


def _sums(db: Session, tenant_id: uuid.UUID, *conditions) -> tuple[Decimal, Decimal]:
    debit, credit = db.query(
        func.coalesce(func.sum(FinLedgerLine.debit), 0),
        func.coalesce(func.sum(FinLedgerLine.credit), 0),
    ).filter(FinLedgerLine.tenant_id == tenant_id, *conditions).one()
    return Decimal(debit), Decimal(credit)


def _side(net: Decimal) -> dict[str, str]:
    """A net debit-minus-credit as the side it sits on."""
    return {"debit": plain(net if net > 0 else ZERO), "credit": plain(-net if net < 0 else ZERO)}


def _two_sided(code: str) -> bool:
    return normal_balance(code) == "BOTH"


# (exact account code, party id): the level a two-sided balance is split at.
Detail = tuple[str, Any]


def _detail_sums(db: Session, tenant_id: uuid.UUID, *conditions) -> dict[Detail, tuple[Decimal, Decimal]]:
    """(debit, credit) per exact account code and party."""
    return {
        (code, party_id): (Decimal(debit or 0), Decimal(credit or 0))
        for code, party_id, debit, credit in db.query(
            FinLedgerLine.account_code,
            FinLedgerLine.party_id,
            func.sum(FinLedgerLine.debit),
            func.sum(FinLedgerLine.credit),
        ).filter(FinLedgerLine.tenant_id == tenant_id, *conditions)
        .group_by(FinLedgerLine.account_code, FinLedgerLine.party_id)
    }


def _closing_nets(
    opening: dict[Detail, tuple[Decimal, Decimal]], moving: dict[Detail, tuple[Decimal, Decimal]]
) -> tuple[dict[Detail, Decimal], dict[Detail, Decimal]]:
    """Opening and closing debit-minus-credit per detail."""
    zero = (ZERO, ZERO)
    opened = {key: opening.get(key, zero)[0] - opening.get(key, zero)[1] for key in opening.keys() | moving.keys()}
    closed = {key: opened[key] + moving.get(key, zero)[0] - moving.get(key, zero)[1] for key in opened}
    return opened, closed


def _sides(nets: Iterable[Decimal], two_sided: bool) -> tuple[Decimal, Decimal]:
    """(debit, credit) balance of some detail nets: netted, or each detail on its own side."""
    values = list(nets)
    if not two_sided:
        net = sum(values, ZERO)
        return max(net, ZERO), max(-net, ZERO)
    return sum((v for v in values if v > 0), ZERO), sum((-v for v in values if v < 0), ZERO)


def _side_of(debit: Decimal, credit: Decimal) -> dict[str, str]:
    return {"debit": plain(debit), "credit": plain(credit)}


def _account(db: Session, tenant_id: uuid.UUID, code: str) -> FinAccount | None:
    return db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id, FinAccount.code == code).first()


def _vn_month(period: str) -> str:
    return f"{period[5:7]}/{period[:4]}"


def coverage_note(db: Session, tenant_id: uuid.UUID, period: str) -> str | None:
    """What the books cannot say about `period`: before they begin, or after the last entry.

    A balance asked for a month the books have nothing for is still a number -- zero, or
    the last month's balance carried forward -- and read without this it passes for that
    month's real figure.
    """
    first, last = db.query(
        func.min(FinLedgerLine.period),
        func.max(case((FinLedgerLine.source != "OPENING", FinLedgerLine.period))),
    ).filter(FinLedgerLine.tenant_id == tenant_id).one()
    if first is None:
        return "Sổ sách chưa có số liệu nào."
    if period < first:
        return f"Sổ sách chỉ có số liệu từ kỳ {_vn_month(first)}; kỳ {_vn_month(period)} không có số liệu."
    if last is not None and period > last:
        return (
            f"Sổ sách chưa có phát sinh nào sau kỳ {_vn_month(last)}: số dư kỳ {_vn_month(period)} "
            "là số dư mang sang, chưa phải số liệu của kỳ đó."
        )
    return None


def account_balance(db: Session, tenant_id: uuid.UUID, code: str, period: str) -> dict[str, Any]:
    period = normalize_period(period)
    account = _account(db, tenant_id, code)
    two_sided = _two_sided(code)
    opened, closed = _closing_nets(
        _detail_sums(db, tenant_id, _under(code), _before(period)),
        _detail_sums(db, tenant_id, _under(code), _in(period)),
    )
    period_debit, period_credit = _sums(db, tenant_id, _under(code), _in(period))
    rows = db.query(func.count(FinLedgerLine.id)).filter(
        FinLedgerLine.tenant_id == tenant_id, _under(code),
        or_(_before(period), _in(period)),
    ).scalar()
    return {
        "account": code,
        "account_name": account.name if account else None,
        "known_account": account is not None,
        "period": period,
        # Two-sided: the debit and credit balances of its parties and sub-accounts, apart.
        "two_sided": two_sided,
        "opening": _side_of(*_sides(opened.values(), two_sided)),
        "period_debit": plain(period_debit),
        "period_credit": plain(period_credit),
        "closing": _side_of(*_sides(closed.values(), two_sided)),
        "ledger_rows": int(rows or 0),
        "note": coverage_note(db, tenant_id, period),
        "source": f"fin_ledger_lines account {code}* through {period}",
    }


def trial_balance(db: Session, tenant_id: uuid.UUID, period: str, *, level: int = 3) -> dict[str, Any]:
    """Bảng cân đối số phát sinh, accounts rolled up to `level` digits."""
    period = normalize_period(period)
    names = {
        account.code: account.name
        for account in db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id)
    }
    moving = _detail_sums(db, tenant_id, _in(period))
    opened, closed = _closing_nets(_detail_sums(db, tenant_id, _before(period)), moving)
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"opening": [], "closing": [], "period_debit": ZERO, "period_credit": ZERO}
    )
    for key in opened:
        group = groups[key[0][:level]]
        group["opening"].append(opened[key])
        group["closing"].append(closed[key])
        debit, credit = moving.get(key, (ZERO, ZERO))
        group["period_debit"] += debit
        group["period_credit"] += credit
    table = []
    totals = defaultdict(lambda: ZERO)
    for code in sorted(groups):
        values = groups[code]
        two_sided = _two_sided(code)
        opening = _sides(values["opening"], two_sided)
        closing = _sides(values["closing"], two_sided)
        line = {
            "account": code,
            "account_name": names.get(code),
            "opening": _side_of(*opening),
            "period_debit": plain(values["period_debit"]),
            "period_credit": plain(values["period_credit"]),
            "closing": _side_of(*closing),
        }
        table.append(line)
        for side, (debit, credit) in (("opening", opening), ("closing", closing)):
            totals[f"{side}_debit"] += debit
            totals[f"{side}_credit"] += credit
        totals["period_debit"] += values["period_debit"]
        totals["period_credit"] += values["period_credit"]
    return {
        "period": period,
        "rows": table,
        "totals": {key: plain(value) for key, value in totals.items()},
        "balanced": totals["period_debit"] == totals["period_credit"]
        and totals["closing_debit"] == totals["closing_credit"],
        "note": coverage_note(db, tenant_id, period),
        "source": f"fin_ledger_lines through {period}",
    }


def ledger_detail(
    db: Session,
    tenant_id: uuid.UUID,
    code: str,
    start: date,
    end: date,
    *,
    party_id: uuid.UUID | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Sổ chi tiết tài khoản: the lines between two dates, with the running balance.

    The range's totals and closing balance cover every line, also when only the first
    `limit` are listed. A two-sided account read across all its parties has no meaningful
    running balance -- it would net one customer's debt against another's advance -- so
    its lines carry none; asking for one party gives it.
    """
    conditions = [_under(code)]
    if party_id:
        conditions.append(FinLedgerLine.party_id == party_id)
    in_range = (
        FinLedgerLine.entry_date >= start,
        FinLedgerLine.entry_date <= end,
        FinLedgerLine.source != "OPENING",
    )
    split = _two_sided(code) and party_id is None
    # Opening balances are dated the first day of the period they were loaded for. One
    # dated inside the range -- the books start mid-year and the question reaches back
    # before that -- still opens it: there is nothing earlier, and leaving it out dropped
    # the whole balance (a payable of 350 read as 130).
    opened, closed = _closing_nets(
        _detail_sums(
            db, tenant_id, *conditions,
            or_(
                and_(FinLedgerLine.entry_date < start, FinLedgerLine.source != "OPENING"),
                and_(FinLedgerLine.source == "OPENING", FinLedgerLine.entry_date <= end),
            ),
        ),
        _detail_sums(db, tenant_id, *conditions, *in_range),
    )
    books_start = db.query(func.min(FinLedgerLine.entry_date)).filter(FinLedgerLine.tenant_id == tenant_id).scalar()
    range_debit, range_credit = _sums(db, tenant_id, *conditions, *in_range)
    running = sum(opened.values(), ZERO)
    query = db.query(FinLedgerLine).filter(
        FinLedgerLine.tenant_id == tenant_id, *conditions, *in_range,
    ).order_by(FinLedgerLine.entry_date, FinLedgerLine.voucher_no)
    total = query.count()
    lines = []
    for line in query.limit(limit):
        running += line.debit - line.credit
        lines.append({
            "date": line.entry_date.isoformat(),
            "voucher_no": line.voucher_no,
            "description": line.description,
            "account": line.account_code,
            "debit": plain(line.debit),
            "credit": plain(line.credit),
            "balance": None if split else _side(running),
        })
    return {
        "account": code,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "two_sided": split,
        "opening": _side_of(*_sides(opened.values(), split)),
        "period_debit": plain(range_debit),
        "period_credit": plain(range_credit),
        "closing": _side_of(*_sides(closed.values(), split)),
        "lines": lines,
        "line_count": total,
        "truncated": total > len(lines),
        "note": (
            f"Sổ sách bắt đầu từ {books_start:%d/%m/%Y}: trước đó không có số liệu, nên số dư "
            "đầu kỳ là số dư nhập lúc bắt đầu sổ."
            if books_start is not None and books_start > start else None
        ),
        "source": f"fin_ledger_lines account {code}* {start.isoformat()}..{end.isoformat()}",
    }


def budget_vs_actual(
    db: Session, tenant_id: uuid.UUID, period: str, *, department: str | None = None
) -> dict[str, Any]:
    """Budget against what the ledger shows spent, per department and account."""
    period = normalize_period(period)
    query = db.query(FinBudget).filter(FinBudget.tenant_id == tenant_id, FinBudget.period == period)
    if department:
        query = query.filter(FinBudget.department == department.upper())
    rows = []
    totals = {"budget": ZERO, "actual": ZERO}
    for budget in query.order_by(FinBudget.department, FinBudget.account_code):
        debit, credit = _sums(
            db, tenant_id, _under(budget.account_code), _in(period),
            func.upper(FinLedgerLine.department) == budget.department,
        )
        actual = debit - credit
        variance = actual - budget.amount
        rows.append({
            "department": budget.department,
            "account": budget.account_code,
            "budget": plain(budget.amount),
            "actual": plain(actual),
            "variance": plain(variance),
            # Both sides of the variance by name: read alone, a variance of -28 was
            # reported as "28 left, minus" -- a negative amount still to spend.
            "remaining": plain(max(-variance, ZERO)),
            "over_amount": plain(max(variance, ZERO)),
            "used_percent": plain(actual * 100 / budget.amount) if budget.amount else None,
            # Given outright so a reply never has to work a percentage out itself.
            "over_percent": plain(variance * 100 / budget.amount) if budget.amount and variance > 0 else None,
            "over_budget": variance > 0,
        })
        totals["budget"] += budget.amount
        totals["actual"] += actual
    total_variance = totals["actual"] - totals["budget"]
    return {
        "period": period,
        "department": department.upper() if department else None,
        "rows": rows,
        "totals": {
            "budget": plain(totals["budget"]),
            "actual": plain(totals["actual"]),
            "variance": plain(total_variance),
            "remaining": plain(max(-total_variance, ZERO)),
            "over_amount": plain(max(total_variance, ZERO)),
        },
        "source": f"fin_budgets + fin_ledger_lines {period}",
    }


@dataclass(frozen=True)
class Outstanding:
    invoice: FinInvoice
    paid: Decimal
    scheduled: Decimal
    # In a payment voucher still waiting for approval.
    drafted: Decimal = ZERO

    @property
    def remaining(self) -> Decimal:
        return self.invoice.total_amount - self.paid


def outstanding_invoices(
    db: Session,
    tenant_id: uuid.UUID,
    direction: str,
    *,
    party_id: uuid.UUID | None = None,
    as_of: date | None = None,
) -> list[Outstanding]:
    """Posted invoices not yet fully paid. Scheduled and drafted payments are shown, not deducted.

    Without `as_of`, what is open now. With it, what was open on that day: invoices issued
    by then, including those paid off since, less only the payments made by then. The
    issue date stands in for the posting date; a payment with no date (loaded by an
    import) counts as made before any day asked about.
    """
    query = db.query(FinInvoice).filter(
        FinInvoice.tenant_id == tenant_id,
        FinInvoice.direction == direction,
    )
    if as_of is None:
        query = query.filter(FinInvoice.status == "POSTED")
    else:
        query = query.filter(
            FinInvoice.status.in_(("POSTED", "PAID")),
            or_(FinInvoice.issue_date.is_(None), FinInvoice.issue_date <= as_of),
        )
    if party_id:
        query = query.filter(FinInvoice.party_id == party_id)
    invoices = query.all()
    payments: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(
        lambda: {"PAID": ZERO, "SCHEDULED": ZERO, "DRAFT": ZERO}
    )
    if invoices:
        conditions = [
            FinPayment.invoice_id.in_([invoice.id for invoice in invoices]),
            FinPayment.status.in_(("PAID", "SCHEDULED", "DRAFT")),
        ]
        if as_of is not None:
            conditions.append(or_(
                FinPayment.status != "PAID",
                FinPayment.payment_date.is_(None),
                FinPayment.payment_date <= as_of,
            ))
        for invoice_id, status, amount in db.query(
            FinPayment.invoice_id, FinPayment.status, func.sum(FinPayment.amount)
        ).filter(*conditions).group_by(FinPayment.invoice_id, FinPayment.status):
            payments[invoice_id][status] = Decimal(amount or 0)
    result = [
        Outstanding(
            invoice,
            payments[invoice.id]["PAID"],
            payments[invoice.id]["SCHEDULED"],
            payments[invoice.id]["DRAFT"],
        )
        for invoice in invoices
    ]
    return [item for item in result if item.remaining > 0]


def _bucket(days_overdue: int) -> str:
    for name, low, high in AGING_BUCKETS:
        if days_overdue >= low and (high is None or days_overdue <= high):
            return name
    return "NOT_DUE"


def aging(
    db: Session,
    tenant_id: uuid.UUID,
    direction: str,
    as_of: date,
    *,
    min_days_overdue: int | None = None,
    party_id: uuid.UUID | None = None,
    invoice_limit: int | None = 50,
) -> dict[str, Any]:
    """Báo cáo tuổi nợ on `as_of`: receivables (OUT invoices) or payables (IN), by overdue bucket."""
    parties: dict[uuid.UUID | None, dict[str, Any]] = {}
    totals = {name: ZERO for name, _, _ in AGING_BUCKETS}
    names = {
        party.id: party for party in db.query(FinParty).filter(FinParty.tenant_id == tenant_id)
    }
    invoices = []
    for item in outstanding_invoices(db, tenant_id, direction, party_id=party_id, as_of=as_of):
        invoice = item.invoice
        due = invoice.due_date or invoice.issue_date or as_of
        overdue = (as_of - due).days
        if min_days_overdue is not None and overdue < min_days_overdue:
            continue
        bucket = _bucket(overdue)
        totals[bucket] += item.remaining
        party = names.get(invoice.party_id)
        row = parties.setdefault(invoice.party_id, {
            "party_id": str(invoice.party_id) if invoice.party_id else None,
            "party": party.name if party else (invoice.seller_name or invoice.buyer_name),
            "tax_code": party.tax_code if party else None,
            **{name: ZERO for name, _, _ in AGING_BUCKETS},
            "total": ZERO,
        })
        row[bucket] += item.remaining
        row["total"] += item.remaining
        invoices.append({
            "invoice_id": str(invoice.id),
            "party": row["party"],
            "series": invoice.series,
            "number": invoice.number,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "due_date": due.isoformat(),
            "days_overdue": max(overdue, 0),
            "remaining": plain(item.remaining),
            "scheduled_payment": plain(item.scheduled),
        })
    party_rows = sorted(parties.values(), key=lambda row: row["total"], reverse=True)
    invoices.sort(key=lambda row: row["days_overdue"], reverse=True)
    # Against part of a party's debts the ledger has nothing to compare with.
    ledger = None if min_days_overdue is not None else _ledger_check(
        db, tenant_id, direction, as_of, party_id, party_rows, names,
    )
    return {
        "kind": "RECEIVABLE" if direction == "OUT" else "PAYABLE",
        "as_of": as_of.isoformat(),
        "min_days_overdue": min_days_overdue,
        "buckets": {name: plain(value) for name, value in totals.items()},
        "total": plain(sum(totals.values(), ZERO)),
        "parties": [
            {**row, **{key: plain(row[key]) for key in (*totals, "total")}} for row in party_rows
        ],
        "invoices": invoices[:invoice_limit] if invoice_limit else invoices,
        "invoice_count": len(invoices),
        "ledger_differences": ledger,
        "source": f"fin_invoices POSTED {direction} - fin_payments PAID, as of {as_of.isoformat()}"
        + (f"; fin_ledger_lines {CONTROL_ACCOUNTS[direction]}* by party" if ledger is not None else ""),
    }


# The account each side's debts sit on in the ledger.
CONTROL_ACCOUNTS = {"OUT": "131", "IN": "331"}


def _ledger_check(
    db: Session,
    tenant_id: uuid.UUID,
    direction: str,
    as_of: date,
    party_id: uuid.UUID | None,
    party_rows: list[dict[str, Any]],
    parties: dict[uuid.UUID, FinParty],
) -> list[dict[str, Any]] | None:
    """Parties whose open invoices and ledger balance (131 or 331) disagree.

    Aging reads invoices, so money received or paid that was never matched to an invoice
    -- a customer's advance, a payment booked straight to the ledger -- leaves the invoice
    open while the ledger has it settled. Read alone, aging then reports the customer who
    paid in advance as owing the full invoice. None when the ledger does not follow
    parties on this account at all, as then every party would differ.
    """
    control = CONTROL_ACCOUNTS[direction]
    conditions = [
        FinLedgerLine.tenant_id == tenant_id,
        _under(control),
        FinLedgerLine.party_id.isnot(None),
        FinLedgerLine.entry_date <= as_of,
    ]
    if db.query(FinLedgerLine.id).filter(*conditions).first() is None:
        return None
    if party_id:
        conditions.append(FinLedgerLine.party_id == party_id)
    nets = {
        key: Decimal(debit or 0) - Decimal(credit or 0)
        for key, debit, credit in db.query(
            FinLedgerLine.party_id, func.sum(FinLedgerLine.debit), func.sum(FinLedgerLine.credit),
        ).filter(*conditions).group_by(FinLedgerLine.party_id)
    }
    open_by_party = {row["party_id"]: row["total"] for row in party_rows if row["party_id"]}
    differences = []
    for key in sorted(set(nets) | {uuid.UUID(value) for value in open_by_party}, key=str):
        net = nets.get(key, ZERO)
        # What the party owes us (131) or we owe them (331), as the ledger has it.
        owed = net if direction == "OUT" else -net
        on_invoices = open_by_party.get(str(key), ZERO)
        if owed == on_invoices:
            continue
        party = parties.get(key)
        if owed < 0:
            reading = (
                f"sổ cái TK {control} ghi {'khách trả trước' if direction == 'OUT' else 'đã ứng trước cho nhà cung cấp'} "
                f"{format_vnd(-owed)}"
            )
        else:
            reading = f"sổ cái TK {control} ghi còn {'phải thu' if direction == 'OUT' else 'phải trả'} {format_vnd(owed)}"
        differences.append({
            "party_id": str(key),
            "party": party.name if party else None,
            "tax_code": party.tax_code if party else None,
            "open_on_invoices": plain(on_invoices),
            "ledger_balance": _side(net),
            "note": (
                f"Theo hoá đơn còn {format_vnd(on_invoices)}, nhưng {reading}: có khoản thu/chi "
                "chưa được cấn trừ với hoá đơn, hoặc hoá đơn chưa được nhập."
            ),
        })
    return differences


def payment_schedule(db: Session, tenant_id: uuid.UUID, as_of: date, horizon_days: int) -> dict[str, Any]:
    """Purchase invoices to pay by `as_of + horizon_days`, earliest due first.

    What still needs a voucher leaves out both approved payments and those in a voucher
    waiting for approval: a second voucher for them would be refused.
    """
    until = as_of + timedelta(days=horizon_days)
    names = {party.id: party.name for party in db.query(FinParty).filter(FinParty.tenant_id == tenant_id)}
    rows = []
    total = ZERO
    # A plan from the books as they stand: `as_of` only moves the window.
    for item in outstanding_invoices(db, tenant_id, "IN"):
        invoice = item.invoice
        due = invoice.due_date or invoice.issue_date or as_of
        if due > until:
            continue
        to_pay = max(item.remaining - item.scheduled - item.drafted, ZERO)
        total += to_pay
        rows.append({
            "invoice_id": str(invoice.id),
            "party": names.get(invoice.party_id) or invoice.seller_name,
            "series": invoice.series,
            "number": invoice.number,
            "due_date": due.isoformat(),
            "overdue": due < as_of,
            "remaining": plain(item.remaining),
            "already_scheduled": plain(item.scheduled),
            "in_pending_voucher": plain(item.drafted),
            "to_schedule": plain(to_pay),
        })
    rows.sort(key=lambda row: row["due_date"])
    return {
        "as_of": as_of.isoformat(),
        "until": until.isoformat(),
        "invoices": rows,
        "total_to_schedule": plain(total),
        "source": f"fin_invoices POSTED IN due by {until.isoformat()}",
    }


MAX_TREND_PERIODS = 24


def periods_between(start: str, end: str) -> list[str]:
    """Every YYYY-MM period from `start` to `end`, both included."""
    start, end = normalize_period(start), normalize_period(end)
    if start > end:
        start, end = end, start
    year, month = int(start[:4]), int(start[5:])
    periods = []
    while f"{year:04d}-{month:02d}" <= end:
        periods.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return periods


def account_trend(db: Session, tenant_id: uuid.UUID, code: str, start: str, end: str) -> dict[str, Any]:
    """One account month by month: opening, debits, credits and closing of each period.

    Each month is `account_balance` of that month, so the figures are the ones a single
    balance question would get; the range is capped to keep the answer readable.
    """
    periods = periods_between(start, end)[-MAX_TREND_PERIODS:]
    rows = []
    name, two_sided = None, _two_sided(code)
    for period in periods:
        balance = account_balance(db, tenant_id, code, period)
        name = name or balance["account_name"]
        rows.append({key: balance[key] for key in ("period", "opening", "period_debit", "period_credit", "closing")})
    return {
        "account": code,
        "account_name": name,
        "two_sided": two_sided,
        "periods": rows,
        "source": f"fin_ledger_lines account {code}* {periods[0]}..{periods[-1]}",
    }


# Classes 6 and 8 of TT200/TT133: production, selling, administrative, financial and other
# expenses. A close to 911 at period end moves them out again; it is not spending.
EXPENSE_PREFIXES = ("6", "8")


def _period_range(periods: list[str]):
    return and_(
        FinLedgerLine.period >= periods[0], FinLedgerLine.period <= periods[-1],
        FinLedgerLine.source != "OPENING",
    )


def _closing_vouchers(db: Session, tenant_id: uuid.UUID, in_range) -> set[tuple[Any, Any]]:
    """(voucher, day) of every period-end close to 911 in the range: not income, not spending."""
    return {
        (voucher, day)
        for voucher, day in db.query(FinLedgerLine.voucher_no, FinLedgerLine.entry_date).filter(
            FinLedgerLine.tenant_id == tenant_id, in_range,
            FinLedgerLine.account_code.like("911%"), FinLedgerLine.voucher_no.isnot(None),
        ).distinct()
    }


def fold_text(value: Any) -> str:
    """Lower case without Vietnamese accents, so "Quảng cáo" finds "QUANG CAO" and back.

    Done here rather than in SQL: ILIKE folds non-ASCII case only under some database
    locales, and nothing folds accents without an extension.
    """
    text = unicodedata.normalize("NFD", str(value or "")).replace("đ", "d").replace("Đ", "D")
    return " ".join("".join(ch for ch in text if unicodedata.category(ch) != "Mn").lower().split())


def _change(previous: Decimal | None, current: Decimal) -> dict[str, Any]:
    """How a month moved from the one before, given outright: a reply never works it out."""
    if previous is None:
        return {"change": None, "change_amount": None, "change_percent": None}
    difference = current - previous
    return {
        "change": "INCREASE" if difference > 0 else "DECREASE" if difference < 0 else "SAME",
        "change_amount": plain(abs(difference)),
        "change_percent": plain(abs(difference) * 100 / abs(previous)) if previous else None,
    }


def expense_breakdown(
    db: Session,
    tenant_id: uuid.UUID,
    start: str,
    end: str,
    *,
    group_by: str = "account",
    level: int = 3,
    keyword: str | None = None,
) -> dict[str, Any]:
    """What was spent between two periods, by expense account, department or month.

    `keyword` keeps the lines whose description contains it: a cost the chart of accounts
    has no account for -- advertising, freight -- is only findable by what the line says.
    """
    periods = periods_between(start, end)
    in_range = _period_range(periods)
    closing = _closing_vouchers(db, tenant_id, in_range)
    if group_by == "account":
        key = func.substr(FinLedgerLine.account_code, 1, level)
    elif group_by == "month":
        key = FinLedgerLine.period
    else:
        key = func.coalesce(func.upper(FinLedgerLine.department), "")
    wanted = fold_text(keyword) if keyword and keyword.strip() else None
    columns = [key, FinLedgerLine.voucher_no, FinLedgerLine.entry_date]
    if wanted:
        columns.append(FinLedgerLine.description)
    sums: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for group, voucher, day, *rest in db.query(
        *columns, func.sum(FinLedgerLine.debit), func.sum(FinLedgerLine.credit),
    ).filter(
        FinLedgerLine.tenant_id == tenant_id, in_range,
        or_(*(FinLedgerLine.account_code.like(f"{prefix}%") for prefix in EXPENSE_PREFIXES)),
    ).group_by(*columns):
        if (voucher, day) in closing:
            continue
        if wanted:
            description, debit, credit = rest
            if wanted not in fold_text(description):
                continue
        else:
            debit, credit = rest
        sums[group or ""] += Decimal(debit or 0) - Decimal(credit or 0)
    total = sum(sums.values(), ZERO)
    if group_by == "month":
        # Every month of the range, in order, an empty one as zero: a month with no such
        # cost is part of the comparison, not a gap in it.
        rows, previous = [], None
        for period in periods:
            amount = sums.get(period, ZERO)
            rows.append({"key": period, "name": _vn_month(period), "amount": plain(amount), **_change(previous, amount)})
            previous = amount
    else:
        names = {
            account.code: account.name
            for account in db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id)
        } if group_by == "account" else {}
        rows = [
            {
                "key": group,
                "name": names.get(group) if group_by == "account" else (group or "Chưa gắn phòng ban"),
                "amount": plain(amount),
                # Given outright so a reply never has to work a share out itself.
                "share_percent": plain(amount * 100 / total) if total > 0 else None,
            }
            for group, amount in sorted(sums.items(), key=lambda item: item[1], reverse=True)
            if amount != 0
        ]
    return {
        "from": periods[0],
        "to": periods[-1],
        "group_by": group_by,
        "level": level if group_by == "account" else None,
        "keyword": keyword.strip() if wanted else None,
        "rows": rows,
        "total": plain(total),
        "source": (
            f"fin_ledger_lines accounts 6*, 8* {periods[0]}..{periods[-1]} excluding closes to 911"
            + (f", description contains “{keyword.strip()}”" if wanted else "")
        ),
    }


@dataclass(frozen=True)
class StatementLine:
    code: str
    label: str
    # (account prefix, sign): +1 adds debit minus credit, -1 adds credit minus debit.
    accounts: tuple[tuple[str, int], ...] = ()
    # Lines computed from others: (code, sign).
    formula: tuple[tuple[str, int], ...] = ()


def _statement_lines(chart: str) -> tuple[StatementLine, ...]:
    """Báo cáo kết quả hoạt động kinh doanh, in the order and numbering of form B02.

    TT133 has no 641 and no 521: selling costs are 6421 under 642, and deductions are
    debited to 511 directly, which the net of 511 already takes off.
    """
    if chart == "TT133":
        selling, admin = (("6421", 1),), (("642", 1), ("6421", -1))
    else:
        selling, admin = (("641", 1),), (("642", 1),)
    return (
        StatementLine("01", "Doanh thu bán hàng và cung cấp dịch vụ", (("511", -1),)),
        StatementLine("02", "Các khoản giảm trừ doanh thu", (("521", 1),)),
        StatementLine("10", "Doanh thu thuần", formula=(("01", 1), ("02", -1))),
        StatementLine("11", "Giá vốn hàng bán", (("632", 1),)),
        StatementLine("20", "Lợi nhuận gộp", formula=(("10", 1), ("11", -1))),
        StatementLine("21", "Doanh thu hoạt động tài chính", (("515", -1),)),
        StatementLine("22", "Chi phí tài chính", (("635", 1),)),
        StatementLine("25", "Chi phí bán hàng", selling),
        StatementLine("26", "Chi phí quản lý doanh nghiệp", admin),
        StatementLine("30", "Lợi nhuận thuần từ hoạt động kinh doanh",
                      formula=(("20", 1), ("21", 1), ("22", -1), ("25", -1), ("26", -1))),
        StatementLine("31", "Thu nhập khác", (("711", -1),)),
        StatementLine("32", "Chi phí khác", (("811", 1),)),
        StatementLine("40", "Lợi nhuận khác", formula=(("31", 1), ("32", -1))),
        StatementLine("50", "Tổng lợi nhuận kế toán trước thuế", formula=(("30", 1), ("40", 1))),
        StatementLine("51", "Chi phí thuế thu nhập doanh nghiệp", (("821", 1),)),
        StatementLine("60", "Lợi nhuận sau thuế thu nhập doanh nghiệp", formula=(("50", 1), ("51", -1))),
    )


def income_statement(db: Session, tenant_id: uuid.UUID, start: str, end: str, *, chart: str = "TT200") -> dict[str, Any]:
    """Kết quả kinh doanh between two periods, from the ledger's movements.

    Read from the revenue and expense lines themselves, period-end closes to 911 left
    out: a company that closes every month and one that never closes get the same figures.
    """
    periods = periods_between(start, end)
    in_range = _period_range(periods)
    closing = _closing_vouchers(db, tenant_id, in_range)
    nets: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for code, voucher, day, debit, credit in db.query(
        FinLedgerLine.account_code, FinLedgerLine.voucher_no, FinLedgerLine.entry_date,
        func.sum(FinLedgerLine.debit), func.sum(FinLedgerLine.credit),
    ).filter(
        FinLedgerLine.tenant_id == tenant_id, in_range,
        or_(*(FinLedgerLine.account_code.like(f"{prefix}%") for prefix in ("5", "6", "7", "8"))),
    ).group_by(FinLedgerLine.account_code, FinLedgerLine.voucher_no, FinLedgerLine.entry_date):
        if (voucher, day) in closing:
            continue
        nets[code] += Decimal(debit or 0) - Decimal(credit or 0)
    values: dict[str, Decimal] = {}
    rows = []
    for line in _statement_lines(chart):
        if line.formula:
            amount = sum((values[code] * sign for code, sign in line.formula), ZERO)
        else:
            amount = sum(
                (net * sign for prefix, sign in line.accounts for code, net in nets.items() if code.startswith(prefix)),
                ZERO,
            )
        values[line.code] = amount
        rows.append({"code": line.code, "label": line.label, "amount": plain(amount), "subtotal": bool(line.formula)})
    note = coverage_note(db, tenant_id, periods[-1])
    if not any(code.startswith(("5", "7")) for code in nets):
        note = (note + " " if note else "") + "Sổ sách không có dòng doanh thu nào trong khoảng này."
    return {
        "from": periods[0],
        "to": periods[-1],
        "chart_of_accounts": chart,
        "rows": rows,
        "revenue_net": plain(values["10"]),
        "profit_before_tax": plain(values["50"]),
        "profit_after_tax": plain(values["60"]),
        "note": note,
        "source": f"fin_ledger_lines accounts 5*-8* {periods[0]}..{periods[-1]} excluding closes to 911",
    }


def _pairs(entry: FinJournalEntry) -> Iterable[tuple[str, str, Decimal]]:
    """(debit account, credit account, amount) as accounting software imports them."""
    debits = [line for line in entry.lines if line.debit]
    credits = [line for line in entry.lines if line.credit]
    if len(credits) == 1:
        for line in debits:
            yield line.account_code, credits[0].account_code, line.debit
    elif len(debits) == 1:
        for line in credits:
            yield debits[0].account_code, line.account_code, line.credit
    else:
        for line in entry.lines:
            yield (line.account_code if line.debit else "", line.account_code if line.credit else "", line.debit or line.credit)


def journal_workbook(entries: list[FinJournalEntry]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bút toán"
    sheet.append(["Ngày hạch toán", "Số chứng từ", "Diễn giải", "TK Nợ", "TK Có", "Số tiền", "MST đối tượng"])
    for entry in entries:
        invoice = entry.invoice
        voucher = f"{invoice.series}-{invoice.number}" if invoice else str(entry.id)[:8]
        party = invoice.party.tax_code if invoice and invoice.party else ""
        for debit, credit, amount in _pairs(entry):
            sheet.append([entry.entry_date, voucher, entry.description, debit, credit, float(amount), party])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def own_budget_department(user: Any) -> str | None:
    """The department someone limited to their own budget sees; None when they have none.

    "ALL" is what an account no department was set for carries, not a department, and an
    empty one must not switch the department filter off.
    """
    department = str(getattr(user, "department", "") or "").strip().upper()
    return None if department in {"", "ALL"} else department


class PartyNotFound(LookupError):
    """No single vendor or customer matches; the message says so in Vietnamese."""


def resolve_party(db: Session, tenant_id: uuid.UUID, term: str) -> FinParty:
    """One party by exact tax code, else by a name fragment that matches exactly one."""
    text = term.strip()
    party = db.query(FinParty).filter(FinParty.tenant_id == tenant_id, FinParty.tax_code == text).first()
    if party is not None:
        return party
    matches = db.query(FinParty).filter(
        FinParty.tenant_id == tenant_id, FinParty.name.ilike(f"%{text}%")
    ).limit(6).all()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PartyNotFound(f"Không có đối tượng nào khớp “{text}”")
    names = ", ".join(f"{party.name} ({party.tax_code or 'không MST'})" for party in matches[:5])
    raise PartyNotFound(f"Có nhiều đối tượng khớp “{text}”: {names}. Hãy nói rõ MST.")
