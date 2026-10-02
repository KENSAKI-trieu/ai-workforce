"""Every figure the Finance agent states, computed here from the ledger and invoices.

Each function takes fixed parameters and returns exact decimal strings plus `source`,
which names the rows behind the figure. There is no free-form query: the model chooses
which report to read and for what, never how it is computed.

Balances follow Vietnamese practice: an account's balance at the start of a period is
everything posted before it plus the opening balances loaded for that period; an account
code covers its sub-accounts (331 includes 3311, 3312).
"""

from __future__ import annotations

import io
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from openpyxl import Workbook
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.domains.finance.money import ZERO, normalize_period, plain
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


def _account(db: Session, tenant_id: uuid.UUID, code: str) -> FinAccount | None:
    return db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id, FinAccount.code == code).first()


def account_balance(db: Session, tenant_id: uuid.UUID, code: str, period: str) -> dict[str, Any]:
    period = normalize_period(period)
    account = _account(db, tenant_id, code)
    opening_debit, opening_credit = _sums(db, tenant_id, _under(code), _before(period))
    period_debit, period_credit = _sums(db, tenant_id, _under(code), _in(period))
    opening = opening_debit - opening_credit
    closing = opening + period_debit - period_credit
    rows = db.query(func.count(FinLedgerLine.id)).filter(
        FinLedgerLine.tenant_id == tenant_id, _under(code),
        or_(_before(period), _in(period)),
    ).scalar()
    return {
        "account": code,
        "account_name": account.name if account else None,
        "known_account": account is not None,
        "period": period,
        "opening": _side(opening),
        "period_debit": plain(period_debit),
        "period_credit": plain(period_credit),
        "closing": _side(closing),
        "ledger_rows": int(rows or 0),
        "source": f"fin_ledger_lines account {code}* through {period}",
    }


def trial_balance(db: Session, tenant_id: uuid.UUID, period: str, *, level: int = 3) -> dict[str, Any]:
    """Bảng cân đối số phát sinh, accounts rolled up to `level` digits."""
    period = normalize_period(period)
    names = {
        account.code: account.name
        for account in db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id)
    }
    head = func.substr(FinLedgerLine.account_code, 1, level)
    rows: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for key, condition in (("opening", _before(period)), ("period", _in(period))):
        for code, debit, credit in db.query(
            head, func.sum(FinLedgerLine.debit), func.sum(FinLedgerLine.credit)
        ).filter(FinLedgerLine.tenant_id == tenant_id, condition).group_by(head):
            rows[code][f"{key}_debit"] += Decimal(debit or 0)
            rows[code][f"{key}_credit"] += Decimal(credit or 0)
    table = []
    totals = defaultdict(lambda: ZERO)
    for code in sorted(rows):
        values = rows[code]
        opening = values["opening_debit"] - values["opening_credit"]
        closing = opening + values["period_debit"] - values["period_credit"]
        line = {
            "account": code,
            "account_name": names.get(code),
            "opening": _side(opening),
            "period_debit": plain(values["period_debit"]),
            "period_credit": plain(values["period_credit"]),
            "closing": _side(closing),
        }
        table.append(line)
        for side, net in (("opening", opening), ("closing", closing)):
            totals[f"{side}_debit"] += max(net, ZERO)
            totals[f"{side}_credit"] += max(-net, ZERO)
        totals["period_debit"] += values["period_debit"]
        totals["period_credit"] += values["period_credit"]
    return {
        "period": period,
        "rows": table,
        "totals": {key: plain(value) for key, value in totals.items()},
        "balanced": totals["period_debit"] == totals["period_credit"]
        and totals["closing_debit"] == totals["closing_credit"],
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
    """Sổ chi tiết tài khoản: the lines between two dates, with the running balance."""
    period = f"{start.year:04d}-{start.month:02d}"
    conditions = [_under(code)]
    if party_id:
        conditions.append(FinLedgerLine.party_id == party_id)
    before_debit, before_credit = _sums(
        db, tenant_id, *conditions,
        or_(FinLedgerLine.entry_date < start, and_(FinLedgerLine.period == period, FinLedgerLine.source == "OPENING")),
    )
    running = before_debit - before_credit
    query = db.query(FinLedgerLine).filter(
        FinLedgerLine.tenant_id == tenant_id, *conditions,
        FinLedgerLine.entry_date >= start, FinLedgerLine.entry_date <= end,
        FinLedgerLine.source != "OPENING",
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
            "balance": _side(running),
        })
    return {
        "account": code,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "opening": _side(before_debit - before_credit),
        "lines": lines,
        "line_count": total,
        "truncated": total > len(lines),
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
            "used_percent": plain(actual * 100 / budget.amount) if budget.amount else None,
            # Given outright so a reply never has to work a percentage out itself.
            "over_percent": plain(variance * 100 / budget.amount) if budget.amount and variance > 0 else None,
            "over_budget": variance > 0,
        })
        totals["budget"] += budget.amount
        totals["actual"] += actual
    return {
        "period": period,
        "department": department.upper() if department else None,
        "rows": rows,
        "totals": {
            "budget": plain(totals["budget"]),
            "actual": plain(totals["actual"]),
            "variance": plain(totals["actual"] - totals["budget"]),
        },
        "source": f"fin_budgets + fin_ledger_lines {period}",
    }


@dataclass(frozen=True)
class Outstanding:
    invoice: FinInvoice
    paid: Decimal
    scheduled: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.invoice.total_amount - self.paid


def outstanding_invoices(
    db: Session, tenant_id: uuid.UUID, direction: str, *, party_id: uuid.UUID | None = None
) -> list[Outstanding]:
    """Posted invoices not yet fully paid. Scheduled payments are shown, not deducted."""
    query = db.query(FinInvoice).filter(
        FinInvoice.tenant_id == tenant_id,
        FinInvoice.direction == direction,
        FinInvoice.status == "POSTED",
    )
    if party_id:
        query = query.filter(FinInvoice.party_id == party_id)
    invoices = query.all()
    payments: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: {"PAID": ZERO, "SCHEDULED": ZERO})
    if invoices:
        for invoice_id, status, amount in db.query(
            FinPayment.invoice_id, FinPayment.status, func.sum(FinPayment.amount)
        ).filter(
            FinPayment.invoice_id.in_([invoice.id for invoice in invoices]),
            FinPayment.status.in_(("PAID", "SCHEDULED")),
        ).group_by(FinPayment.invoice_id, FinPayment.status):
            payments[invoice_id][status] = Decimal(amount or 0)
    result = [
        Outstanding(invoice, payments[invoice.id]["PAID"], payments[invoice.id]["SCHEDULED"])
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
) -> dict[str, Any]:
    """Báo cáo tuổi nợ: receivables (OUT invoices) or payables (IN), by overdue bucket."""
    parties: dict[uuid.UUID | None, dict[str, Any]] = {}
    totals = {name: ZERO for name, _, _ in AGING_BUCKETS}
    names = {
        party.id: party for party in db.query(FinParty).filter(FinParty.tenant_id == tenant_id)
    }
    invoices = []
    for item in outstanding_invoices(db, tenant_id, direction, party_id=party_id):
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
    return {
        "kind": "RECEIVABLE" if direction == "OUT" else "PAYABLE",
        "as_of": as_of.isoformat(),
        "min_days_overdue": min_days_overdue,
        "buckets": {name: plain(value) for name, value in totals.items()},
        "total": plain(sum(totals.values(), ZERO)),
        "parties": [
            {**row, **{key: plain(row[key]) for key in (*totals, "total")}} for row in party_rows
        ],
        "invoices": invoices[:50],
        "invoice_count": len(invoices),
        "source": f"fin_invoices POSTED {direction} - fin_payments PAID, as of {as_of.isoformat()}",
    }


def payment_schedule(db: Session, tenant_id: uuid.UUID, as_of: date, horizon_days: int) -> dict[str, Any]:
    """Purchase invoices to pay by `as_of + horizon_days`, earliest due first."""
    until = as_of + timedelta(days=horizon_days)
    rows = []
    total = ZERO
    for item in outstanding_invoices(db, tenant_id, "IN"):
        invoice = item.invoice
        due = invoice.due_date or invoice.issue_date or as_of
        if due > until:
            continue
        to_pay = item.remaining - item.scheduled
        total += max(to_pay, ZERO)
        rows.append({
            "invoice_id": str(invoice.id),
            "party": invoice.party.name if invoice.party else invoice.seller_name,
            "series": invoice.series,
            "number": invoice.number,
            "due_date": due.isoformat(),
            "overdue": due < as_of,
            "remaining": plain(item.remaining),
            "already_scheduled": plain(item.scheduled),
            "to_schedule": plain(max(to_pay, ZERO)),
        })
    rows.sort(key=lambda row: row["due_date"])
    return {
        "as_of": as_of.isoformat(),
        "until": until.isoformat(),
        "invoices": rows,
        "total_to_schedule": plain(total),
        "source": f"fin_invoices POSTED IN due by {until.isoformat()}",
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
