"""Payment vouchers (phiếu chi) and payment reminders (nhắc nợ): drafts a person approves.

Nothing here moves money. An approved voucher schedules the payment; the person who
makes the transfer in the bank then records it as paid, which posts Dr 331 / Cr 112. The
bank account shown on a voucher is the one on file for the vendor -- never one read from
an invoice -- and a voucher for a vendor whose invoices flagged a different account says
so on its face.

A reminder's figures come from the aging report; its wording from a fixed template per
level. No model writes to a customer.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domains.finance.approvals import (
    PAYMENT_VOUCHER,
    REMINDER_SEND,
    open_finance_approval,
    register_handler,
)
from app.domains.finance.money import ZERO, format_vnd, period_of, plain
from app.domains.finance.reports import aging, outstanding_invoices
from app.domains.platform.email_delivery import deliver_email
from app.models.models import (
    AgentWorkflow,
    FinAccount,
    FinInvoice,
    FinJournalEntry,
    FinJournalLine,
    FinLedgerLine,
    FinParty,
    FinPayment,
    User,
    WorkflowApproval,
)


class DraftRefused(ValueError):
    """The draft cannot be made; the message tells the user why, in Vietnamese."""


def _mask(account: str | None) -> str | None:
    if not account:
        return None
    digits = account.replace(" ", "")
    return "•••• " + digits[-4:] if len(digits) > 4 else digits


# --------------------------------------------------------------------------- vouchers


def draft_payment_voucher(db: Session, actor: User, invoice_ids: list[uuid.UUID]) -> WorkflowApproval:
    if not invoice_ids:
        raise DraftRefused("Chưa chọn hoá đơn nào để lập phiếu chi")
    # Locked until the voucher is stored: two people drafting for the same invoice at
    # once would otherwise both pass the check below and schedule it twice.
    invoices = db.query(FinInvoice).filter(
        FinInvoice.tenant_id == actor.tenant_id, FinInvoice.id.in_(invoice_ids)
    ).order_by(FinInvoice.id).with_for_update().all()
    if len(invoices) != len(set(invoice_ids)):
        raise DraftRefused("Có hoá đơn không tìm thấy")
    if any(invoice.direction != "IN" for invoice in invoices):
        raise DraftRefused("Phiếu chi chỉ lập cho hoá đơn mua vào")
    parties = {invoice.party_id for invoice in invoices}
    if len(parties) != 1 or None in parties:
        raise DraftRefused("Một phiếu chi chỉ trả cho một nhà cung cấp; hãy tách theo nhà cung cấp")
    party = db.get(FinParty, parties.pop())
    drafting = db.query(FinPayment).filter(
        FinPayment.invoice_id.in_(invoice_ids), FinPayment.status == "DRAFT"
    ).first()
    if drafting:
        invoice = next(item for item in invoices if item.id == drafting.invoice_id)
        workflow = db.get(AgentWorkflow, drafting.workflow_id) if drafting.workflow_id else None
        pending = f" ({workflow.title})" if workflow else ""
        raise DraftRefused(
            f"Hoá đơn {invoice.series} số {invoice.number} đã nằm trong một phiếu chi đang chờ "
            f"duyệt{pending}. Xem phiếu đó ở trang Phê duyệt, tab \"Tôi đã gửi\"; muốn lập "
            "lại thì rút phiếu cũ trước"
        )
    open_amounts = {
        item.invoice.id: item.remaining - item.scheduled
        for item in outstanding_invoices(db, actor.tenant_id, "IN", party_id=party.id)
    }
    lines: list[dict[str, Any]] = []
    total = ZERO
    for invoice in invoices:
        amount = open_amounts.get(invoice.id, ZERO)
        if invoice.status != "POSTED" or amount <= 0:
            raise DraftRefused(
                f"Hoá đơn {invoice.series} số {invoice.number} chưa ghi sổ hoặc không còn số phải trả"
            )
        lines.append({
            "invoice_id": str(invoice.id),
            "series": invoice.series,
            "number": invoice.number,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "amount": plain(amount),
        })
        total += amount
    warnings = []
    if not party.bank_account:
        warnings.append("Nhà cung cấp chưa có số tài khoản ngân hàng trong danh mục")
    if any(
        item.get("code") == "BANK_ACCOUNT_DIFFERS"
        for invoice in invoices for item in (invoice.exceptions or [])
    ):
        warnings.append(
            "Hoá đơn của nhà cung cấp này từng in số tài khoản khác số đã lưu; "
            "xác minh trực tiếp trước khi chuyển tiền"
        )
    voucher_id = uuid.uuid4()
    approval = open_finance_approval(
        db, actor,
        action_type=PAYMENT_VOUCHER,
        title=f"Duyệt phiếu chi {format_vnd(total)} cho {party.name}"[:255],
        amount=total,
        record_id=str(voucher_id),
        payload={
            "reason": f"Thanh toán {len(lines)} hoá đơn cho {party.name}",
            "party": {"name": party.name, "tax_code": party.tax_code, "bank_name": party.bank_name,
                      "bank_account": _mask(party.bank_account)},
            "invoices": lines,
            "warnings": warnings,
            "data_sources": [f"fin_invoices/{line['invoice_id']}" for line in lines],
        },
    )
    for line in lines:
        db.add(FinPayment(
            tenant_id=actor.tenant_id, invoice_id=uuid.UUID(line["invoice_id"]), party_id=party.id,
            direction="PAY", amount=Decimal(line["amount"]), status="DRAFT",
            reference=f"PC-{voucher_id.hex[:8].upper()}", workflow_id=approval.workflow_id,
            created_by_id=actor.id,
        ))
    db.flush()
    return approval


def _voucher_payments(db: Session, approval: WorkflowApproval) -> list[FinPayment]:
    return db.query(FinPayment).filter(FinPayment.workflow_id == approval.workflow_id).all()


def _finalize_voucher(db: Session, approval: WorkflowApproval, approver: User, approved: bool) -> None:
    payments = _voucher_payments(db, approval)
    if any(payment.status != "DRAFT" for payment in payments):
        raise HTTPException(status_code=409, detail="This voucher was already decided")
    for payment in payments:
        payment.status = "SCHEDULED" if approved else "CANCELLED"
    db.flush()


register_handler(PAYMENT_VOUCHER, _finalize_voucher)


def _bank_account_code(db: Session, tenant_id: uuid.UUID) -> str:
    codes = {code for (code,) in db.query(FinAccount.code).filter(
        FinAccount.tenant_id == tenant_id, FinAccount.code.in_(("1121", "112"))
    )}
    for code in ("1121", "112"):
        if code in codes:
            return code
    raise DraftRefused("Hệ thống tài khoản chưa có TK 112 (tiền gửi ngân hàng)")


def record_voucher_paid(
    db: Session, actor: User, workflow_id: uuid.UUID, *, paid_on: date, bank_reference: str
) -> list[FinPayment]:
    """The transfer was made: post Dr 331 / Cr 112 and settle the invoices."""
    payments = db.query(FinPayment).filter(
        FinPayment.tenant_id == actor.tenant_id, FinPayment.workflow_id == workflow_id
    ).all()
    if not payments:
        raise DraftRefused("Không tìm thấy phiếu chi")
    if any(payment.status != "SCHEDULED" for payment in payments):
        raise DraftRefused("Phiếu chi chưa được duyệt hoặc đã ghi nhận thanh toán")
    bank = _bank_account_code(db, actor.tenant_id)
    total = sum((payment.amount for payment in payments), ZERO)
    party = db.get(FinParty, payments[0].party_id) if payments[0].party_id else None
    entry = FinJournalEntry(
        id=uuid.uuid4(), tenant_id=actor.tenant_id, entry_date=paid_on,
        description=f"Thanh toán {party.name if party else ''} - {bank_reference}"[:1000],
        source="PAYMENT", status="POSTED", proposed_by="USER", confidence="HIGH",
        workflow_id=workflow_id, created_by_id=actor.id, approved_by_id=actor.id,
        posted_at=datetime.now(timezone.utc),
    )
    entry.lines = [
        FinJournalLine(line_no=1, account_code="331", debit=total, credit=ZERO, party_id=party.id if party else None),
        FinJournalLine(line_no=2, account_code=bank, debit=ZERO, credit=total),
    ]
    db.add(entry)
    for line in entry.lines:
        db.add(FinLedgerLine(
            tenant_id=actor.tenant_id, period=period_of(paid_on), entry_date=paid_on,
            account_code=line.account_code, debit=line.debit, credit=line.credit,
            party_id=line.party_id, description=entry.description, source="JOURNAL",
            journal_entry_id=entry.id, voucher_no=payments[0].reference,
        ))
    for payment in payments:
        payment.status = "PAID"
        payment.payment_date = paid_on
        payment.reference = f"{payment.reference} / {bank_reference}"[:100]
    db.flush()
    for payment in payments:
        invoice = db.get(FinInvoice, payment.invoice_id) if payment.invoice_id else None
        if invoice is not None and not any(
            item.invoice.id == invoice.id for item in outstanding_invoices(db, actor.tenant_id, "IN", party_id=invoice.party_id)
        ):
            invoice.status = "PAID"
    db.flush()
    return payments


# --------------------------------------------------------------------------- reminders

_TEMPLATES = {
    1: (
        "Thông báo công nợ đến hạn - {company}",
        "Kính gửi Quý {party},\n\n"
        "{company} xin thông báo các hoá đơn dưới đây đã đến hạn thanh toán:\n\n"
        "{lines}\n\nTổng số còn phải thanh toán: {total}.\n\n"
        "Nếu Quý khách đã thanh toán, xin vui lòng bỏ qua thông báo này và gửi giúp chúng tôi "
        "chứng từ để đối chiếu. Trân trọng cảm ơn.\n\n{company}",
    ),
    2: (
        "Nhắc thanh toán công nợ quá hạn - {company}",
        "Kính gửi Quý {party},\n\n"
        "Đến nay {company} vẫn chưa nhận được thanh toán cho các hoá đơn đã quá hạn sau:\n\n"
        "{lines}\n\nTổng số quá hạn: {total}.\n\n"
        "Kính đề nghị Quý khách thu xếp thanh toán trong vòng 07 ngày kể từ ngày nhận thư này, "
        "hoặc liên hệ với chúng tôi nếu có vướng mắc về số liệu. Trân trọng.\n\n{company}",
    ),
    3: (
        "Đề nghị thanh toán công nợ quá hạn lần cuối - {company}",
        "Kính gửi Quý {party},\n\n"
        "{company} đã nhiều lần đề nghị thanh toán các hoá đơn quá hạn dưới đây nhưng chưa "
        "nhận được phản hồi:\n\n{lines}\n\nTổng số quá hạn: {total}.\n\n"
        "Đề nghị Quý khách thanh toán trong vòng 05 ngày làm việc. Sau thời hạn này, chúng tôi "
        "buộc phải tạm ngừng cung cấp hàng hoá/dịch vụ và áp dụng các biện pháp theo hợp đồng.\n\n{company}",
    ),
}


def _vn_date(iso: str) -> str:
    """2026-07-29 -> 29/07/2026, as a letter to a Vietnamese customer writes it."""
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def draft_payment_reminder(
    db: Session, actor: User, party: FinParty, level: int | None, *, company_name: str, as_of: date
) -> WorkflowApproval:
    if party.kind not in {"CUSTOMER", "BOTH"}:
        raise DraftRefused(f"{party.name} không phải khách hàng")
    if not party.email:
        raise DraftRefused(f"Khách hàng {party.name} chưa có email trong danh mục đối tượng")
    # Locked so two reminders drafted at once cannot both pass the pending check below.
    db.query(FinParty).filter(FinParty.id == party.id).with_for_update().one()
    if level is None:
        # No level asked for: a debt already overdue is not "due", and a final demand is
        # never chosen for the user.
        overdue = aging(db, actor.tenant_id, "OUT", as_of, party_id=party.id, min_days_overdue=1, invoice_limit=1)
        level = 2 if overdue["invoices"] else 1
    # Every open invoice: the letter lists what its total adds up.
    report = aging(
        db, actor.tenant_id, "OUT", as_of, party_id=party.id,
        min_days_overdue=0 if level == 1 else 1, invoice_limit=None,
    )
    if not report["invoices"]:
        raise DraftRefused(f"{party.name} không có khoản nợ {'đến hạn' if level == 1 else 'quá hạn'} nào")
    pending = db.query(WorkflowApproval).filter(
        WorkflowApproval.action_type == REMINDER_SEND,
        WorkflowApproval.status == "WAITING",
        WorkflowApproval.payload["party_id"].astext == str(party.id),
    ).first()
    if pending is not None:
        workflow = db.get(AgentWorkflow, pending.workflow_id) if pending.workflow_id else None
        title = f" ({workflow.title})" if workflow else ""
        raise DraftRefused(
            f"Đã có một thư nhắc nợ gửi {party.name} đang chờ duyệt{title}. Xem thư đó ở trang "
            "Phê duyệt, tab \"Tôi đã gửi\"; muốn soạn lại thì rút thư cũ trước"
        )
    lines = "\n".join(
        f"- Hoá đơn {item['series']} số {item['number']}, hạn {_vn_date(item['due_date'])}, "
        f"còn {format_vnd(Decimal(item['remaining']))} (quá hạn {item['days_overdue']} ngày)"
        for item in report["invoices"]
    )
    subject, body = _TEMPLATES[level]
    values = {"company": company_name, "party": party.name, "lines": lines, "total": format_vnd(Decimal(report["total"]))}
    return open_finance_approval(
        db, actor,
        action_type=REMINDER_SEND,
        title=f"Duyệt thư nhắc nợ mức {level} gửi {party.name}"[:255],
        amount=Decimal(report["total"]),
        record_id=str(party.id),
        # A letter's risk is its wording, not its amount: the first signing level reads it.
        fixed_permission="finance.journal.approve",
        payload={
            "reason": f"Nhắc nợ mức {level}: {report['invoice_count']} hoá đơn, tổng {format_vnd(Decimal(report['total']))}",
            "party_id": str(party.id),
            "level": level,
            "recipient": party.email,
            "subject": subject.format(**values),
            "body": body.format(**values),
            "data_sources": [report["source"]],
        },
    )


def _validate_reminder_edit(db: Session, approval: WorkflowApproval, edited: dict[str, Any]) -> dict[str, Any]:
    """The approver may reword the letter. Recipient and figures stay as drafted."""
    payload = dict(approval.payload or {})
    for key in ("subject", "body"):
        if key in edited:
            text = str(edited[key]).strip()
            if not text:
                raise HTTPException(status_code=422, detail=f"{key} không được để trống")
            payload[key] = text[:255] if key == "subject" else text[:20000]
    return payload


def _finalize_reminder(db: Session, approval: WorkflowApproval, approver: User, approved: bool) -> None:
    if not approved:
        return
    payload = dict(approval.payload or {})
    try:
        payload["delivery"] = deliver_email(payload["recipient"], payload["subject"], payload["body"])
    except Exception as exc:  # noqa: BLE001 - the decision stands; the failed send is recorded
        payload["delivery"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    approval.payload = payload


register_handler(REMINDER_SEND, _finalize_reminder, _validate_reminder_edit)
