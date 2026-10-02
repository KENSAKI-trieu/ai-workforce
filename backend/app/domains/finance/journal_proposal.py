"""Proposing the journal entry for an invoice, and what approving it does.

The amounts come from the invoice, never from a model. What has to be chosen is the one
account the amount before tax goes to; the tax and receivable/payable sides follow from
the invoice's direction. That account comes, in order, from the user if they named it,
from a posting rule an approver confirmed earlier for this party, or from a model picking
among the company's own accounts. Anything uncertain -- a model's choice, a new party, an
amount far from this party's usual -- is marked low confidence with the reason, for the
approver to read.

Approving posts the lines to the ledger. Approving unchanged teaches the rule for this
party; changing the account first records the correction, which the next model proposal
for the party is shown.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.finance.prompts import resolve_slot
from app.agents.llm_json import UsageReporter, extract_json_object, is_echo_provider, report_usage
from app.clients.ai_service_client import AIServiceError, get_ai_service_client
from app.domains.finance.approvals import JOURNAL_APPROVAL, open_finance_approval, register_handler
from app.domains.finance.money import CENT, ZERO, format_vnd, period_of, plain
from app.models.models import (
    FinAccount,
    FinInvoice,
    FinJournalEntry,
    FinJournalLine,
    FinLedgerLine,
    FinParty,
    FinPostingRule,
    User,
    WorkflowApproval,
)

logger = logging.getLogger(__name__)

# Where the main amount of each kind of invoice may go. Purchases: inventory, fixed
# assets, prepaid and construction (15x, 21x, 24x), expenses (6xx, 8xx). Sales: revenue.
_MAIN_PREFIXES = {
    "IN": ("15", "211", "213", "241", "242", "6", "8"),
    "OUT": ("511", "515", "711"),
}
# The other sides, by preference: the detailed account if the chart has it, else its parent.
_VAT_ACCOUNTS = {"IN": ("1331", "133"), "OUT": ("33311", "3331", "333")}
_COUNTER_ACCOUNTS = {"IN": ("331",), "OUT": ("131",)}
# How far from the party's usual amount still reads as usual.
_ANOMALY_FACTOR = Decimal("3")
_HISTORY = 6


class ProposalRefused(ValueError):
    """The entry cannot be proposed; the message tells the user why, in Vietnamese."""


def _accounts(db: Session, tenant_id: uuid.UUID) -> dict[str, FinAccount]:
    return {
        account.code: account
        for account in db.query(FinAccount).filter(
            FinAccount.tenant_id == tenant_id, FinAccount.is_active.is_(True)
        )
    }


def _leaves(accounts: dict[str, FinAccount]) -> set[str]:
    parents = {account.parent_code for account in accounts.values() if account.parent_code}
    return {code for code in accounts if code not in parents}


def candidate_accounts(accounts: dict[str, FinAccount], direction: str) -> list[FinAccount]:
    """Postable accounts the main amount of this kind of invoice may go to."""
    leaves = _leaves(accounts)
    return sorted(
        (accounts[code] for code in leaves if code.startswith(_MAIN_PREFIXES[direction])),
        key=lambda account: account.code,
    )


def _first_present(accounts: dict[str, FinAccount], codes: tuple[str, ...], label: str) -> str:
    for code in codes:
        if code in accounts:
            return code
    raise ProposalRefused(f"Hệ thống tài khoản chưa có TK {label} ({' hoặc '.join(codes)})")


def _vnd(invoice: FinInvoice, amount: Decimal) -> Decimal:
    rate = invoice.exchange_rate or Decimal("1")
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)


def _last_correction(db: Session, invoice: FinInvoice) -> dict[str, Any] | None:
    entry = db.query(FinJournalEntry).join(FinInvoice, FinInvoice.id == FinJournalEntry.invoice_id).filter(
        FinJournalEntry.tenant_id == invoice.tenant_id,
        FinInvoice.party_id == invoice.party_id,
        FinInvoice.direction == invoice.direction,
        FinJournalEntry.corrections.isnot(None),
    ).order_by(FinJournalEntry.posted_at.desc().nullslast()).first()
    return entry.corrections if entry else None


def _model_choice(
    db: Session,
    invoice: FinInvoice,
    candidates: list[FinAccount],
    *,
    prompts: Mapping[str, str] | None,
    on_usage: UsageReporter | None,
) -> tuple[str, str]:
    client = get_ai_service_client()
    if not client.enabled:
        raise ProposalRefused("Chưa chọn được tài khoản tự động lúc này; hãy cho biết TK hạch toán (ví dụ 6422).")
    request = {
        "direction": "Hoá đơn mua vào" if invoice.direction == "IN" else "Hoá đơn bán ra",
        "party": invoice.party.name if invoice.party else invoice.seller_name,
        "lines": [str(line.get("name") or "")[:200] for line in (invoice.lines or [])][:20],
        "accounts": [{"code": account.code, "name": account.name} for account in candidates],
    }
    correction = _last_correction(db, invoice)
    if correction:
        request["previous_correction_for_this_party"] = correction
    try:
        result = client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "finance_account_choice")},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ])
    except AIServiceError as exc:
        logger.warning("Account choice failed", exc_info=True)
        raise ProposalRefused("Chưa chọn được tài khoản tự động lúc này; hãy cho biết TK hạch toán.") from exc
    if is_echo_provider(result):
        raise ProposalRefused("Chưa chọn được tài khoản tự động lúc này; hãy cho biết TK hạch toán.")
    report_usage(on_usage, result)
    answer = extract_json_object(str(result.get("content") or "")) or {}
    code = str(answer.get("account") or "").strip()
    if code not in {account.code for account in candidates}:
        raise ProposalRefused(
            f"Mô hình chọn TK {code or '(trống)'} không thuộc danh mục được phép; hãy cho biết TK hạch toán."
        )
    return code, str(answer.get("reason") or "")[:500]


def _amount_reasons(db: Session, invoice: FinInvoice) -> list[str]:
    history = [
        amount for (amount,) in db.query(FinInvoice.total_amount).filter(
            FinInvoice.tenant_id == invoice.tenant_id,
            FinInvoice.party_id == invoice.party_id,
            FinInvoice.direction == invoice.direction,
            FinInvoice.status.in_(("POSTED", "PAID")),
            FinInvoice.id != invoice.id,
        ).order_by(FinInvoice.issue_date.desc().nullslast()).limit(_HISTORY)
    ]
    if len(history) < 2:
        return []
    usual = sum(history, ZERO) / len(history)
    if usual and not (usual / _ANOMALY_FACTOR <= invoice.total_amount <= usual * _ANOMALY_FACTOR):
        return [f"Số tiền {format_vnd(invoice.total_amount)} lệch mạnh so với mức thường ({format_vnd(usual)})"]
    return []


def _line_payload(entry: FinJournalEntry, accounts: dict[str, FinAccount]) -> list[dict[str, Any]]:
    return [
        {
            "line_no": line.line_no,
            "account_code": line.account_code,
            "account_name": accounts[line.account_code].name if line.account_code in accounts else "",
            "debit": plain(line.debit),
            "credit": plain(line.credit),
        }
        for line in entry.lines
    ]


def describe(entry: FinJournalEntry) -> str:
    sides = "; ".join(
        f"{'Nợ' if line.debit else 'Có'} {line.account_code} {format_vnd(line.debit or line.credit)}"
        for line in entry.lines
    )
    return sides


def propose_for_invoice(
    db: Session,
    actor: User,
    invoice_id: uuid.UUID,
    *,
    main_account: str | None = None,
    prompts: Mapping[str, str] | None = None,
    on_usage: UsageReporter | None = None,
) -> tuple[FinJournalEntry, bool]:
    """(entry, created). Raises ProposalRefused with a reason the user can act on."""
    invoice = db.query(FinInvoice).filter(
        FinInvoice.id == invoice_id, FinInvoice.tenant_id == actor.tenant_id
    ).first()
    if invoice is None:
        raise ProposalRefused("Không tìm thấy hoá đơn này")
    live = db.query(FinJournalEntry).filter(
        FinJournalEntry.invoice_id == invoice.id,
        FinJournalEntry.status.in_(("PENDING_APPROVAL", "POSTED")),
    ).first()
    if live is not None:
        return live, False
    if invoice.status == "EXCEPTION":
        open_items = [item["message"] for item in invoice.exceptions or [] if item.get("severity") == "BLOCKING"]
        raise ProposalRefused(
            "Hoá đơn còn ngoại lệ chưa được xử lý: " + "; ".join(open_items[:3])
            + ". Người xử lý hoá đơn cần xem và xác nhận trước."
        )
    if invoice.status != "MATCHED":
        raise ProposalRefused(f"Hoá đơn đang ở trạng thái {invoice.status}, không đề xuất bút toán được")

    accounts = _accounts(db, actor.tenant_id)
    candidates = candidate_accounts(accounts, invoice.direction)
    if not candidates:
        raise ProposalRefused("Chưa có hệ thống tài khoản; người quản trị Tài chính cần chọn TT200/TT133 hoặc nhập danh mục TK")
    allowed = {account.code for account in candidates}
    party = db.get(FinParty, invoice.party_id) if invoice.party_id else None
    reasons: list[str] = []
    model_reason = ""
    if main_account:
        code = main_account.strip()
        if code not in allowed:
            raise ProposalRefused(f"TK {code} không dùng được cho {'hoá đơn mua vào' if invoice.direction == 'IN' else 'hoá đơn bán ra'} (phải là TK chi tiết đang dùng)")
        proposed_by = "USER"
    else:
        rule = db.query(FinPostingRule).filter(
            FinPostingRule.tenant_id == actor.tenant_id,
            FinPostingRule.party_id == invoice.party_id,
            FinPostingRule.direction == invoice.direction,
        ).first() if invoice.party_id else None
        if rule is not None and rule.main_account in allowed:
            code, proposed_by = rule.main_account, "RULE"
            model_reason = f"Theo luật hạch toán đã được duyệt {rule.times_confirmed} lần cho đối tác này"
        else:
            code, model_reason = _model_choice(db, invoice, candidates, prompts=prompts, on_usage=on_usage)
            proposed_by = "MODEL"
            reasons.append("TK do mô hình AI chọn, chưa có luật hạch toán đã duyệt cho đối tác này")
    if party is not None and party.is_new:
        reasons.append("Đối tác mới, chưa từng được hạch toán")
    reasons.extend(_amount_reasons(db, invoice))

    vat_account = _first_present(accounts, _VAT_ACCOUNTS[invoice.direction], "thuế GTGT")
    counter_account = _first_present(accounts, _COUNTER_ACCOUNTS[invoice.direction], "công nợ")
    total = _vnd(invoice, invoice.total_amount)
    vat = _vnd(invoice, invoice.vat_amount)
    base = total - vat  # balances to the dong even when the rate rounded each side
    if base <= 0:
        raise ProposalRefused("Tiền trước thuế của hoá đơn không hợp lệ")

    label = f"Hoá đơn {invoice.series} số {invoice.number} - {party.name if party else invoice.seller_name}"
    entry = FinJournalEntry(
        id=uuid.uuid4(),
        tenant_id=actor.tenant_id,
        entry_date=invoice.issue_date or datetime.now(timezone.utc).date(),
        description=label[:1000],
        source="INVOICE",
        invoice_id=invoice.id,
        status="PENDING_APPROVAL",
        proposed_by=proposed_by,
        confidence="LOW" if reasons else "HIGH",
        confidence_reasons=reasons,
        created_by_id=actor.id,
    )
    if invoice.direction == "IN":
        sides = [(code, base, ZERO), (vat_account, vat, ZERO), (counter_account, ZERO, total)]
    else:
        sides = [(counter_account, total, ZERO), (code, ZERO, base), (vat_account, ZERO, vat)]
    entry.lines = [
        FinJournalLine(line_no=index, account_code=account, debit=debit, credit=credit,
                       party_id=invoice.party_id, description=label[:500])
        for index, (account, debit, credit) in enumerate(
            (side for side in sides if side[1] or side[2]), start=1
        )
    ]
    db.add(entry)
    db.flush()
    approval = open_finance_approval(
        db, actor,
        action_type=JOURNAL_APPROVAL,
        title=f"Duyệt bút toán: {label}"[:255],
        amount=total,
        record_id=str(entry.id),
        payload={
            "reason": model_reason or "Bút toán hạch toán hoá đơn",
            "invoice": {
                "id": str(invoice.id),
                "series": invoice.series,
                "number": invoice.number,
                "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
                "party": party.name if party else invoice.seller_name,
                "total_amount": plain(total),
            },
            "lines": _line_payload(entry, accounts),
            "proposed_by": proposed_by,
            "confidence": entry.confidence,
            "confidence_reasons": reasons,
            "data_sources": [f"fin_invoices/{invoice.id}"],
        },
    )
    entry.workflow_id = approval.workflow_id
    db.flush()
    return entry, True


# --------------------------------------------------------------------------- approval


def _entry_for(db: Session, approval: WorkflowApproval) -> FinJournalEntry:
    record_id = (approval.payload or {}).get("finance_record_id")
    entry = db.query(FinJournalEntry).filter(
        FinJournalEntry.id == uuid.UUID(str(record_id)),
        FinJournalEntry.tenant_id == approval.workflow.tenant_id,
    ).first() if record_id else None
    if entry is None:
        raise HTTPException(status_code=409, detail="The journal entry behind this approval no longer exists")
    return entry


def _validate_edit(db: Session, approval: WorkflowApproval, edited: dict[str, Any]) -> dict[str, Any]:
    """An approver may re-point lines to other accounts; amounts and sides stay as drafted."""
    entry = _entry_for(db, approval)
    accounts = _accounts(db, entry.tenant_id)
    leaves = _leaves(accounts)
    wanted = {
        int(item["line_no"]): str(item["account_code"]).strip()
        for item in (edited.get("lines") or [])
        if isinstance(item, dict) and "line_no" in item and "account_code" in item
    }
    if not wanted:
        raise HTTPException(status_code=422, detail="Sửa bút toán: gửi lines với line_no và account_code mới")
    known = {line.line_no for line in entry.lines}
    for line_no, code in wanted.items():
        if line_no not in known:
            raise HTTPException(status_code=422, detail=f"Bút toán không có dòng {line_no}")
        if code not in leaves:
            raise HTTPException(status_code=422, detail=f"TK {code} không phải TK chi tiết đang dùng")
    payload = dict(approval.payload or {})
    payload["lines"] = [
        {**line, "account_code": wanted.get(line["line_no"], line["account_code"]),
         "account_name": accounts[wanted[line["line_no"]]].name if line["line_no"] in wanted else line["account_name"]}
        for line in payload.get("lines") or []
    ]
    payload["edited_by_approver"] = True
    return payload


def _finalize(db: Session, approval: WorkflowApproval, approver: User, approved: bool) -> None:
    entry = _entry_for(db, approval)
    if entry.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"Journal entry is already {entry.status}")
    if not approved:
        entry.status = "REJECTED"
        entry.approved_by_id = approver.id
        return
    payload = approval.payload or {}
    edited = bool(payload.get("edited_by_approver"))
    if edited:
        new_accounts = {int(line["line_no"]): line["account_code"] for line in payload.get("lines") or []}
        changes = [
            {"line_no": line.line_no, "from": line.account_code, "to": new_accounts[line.line_no]}
            for line in entry.lines
            if new_accounts.get(line.line_no) and new_accounts[line.line_no] != line.account_code
        ]
        for line in entry.lines:
            line.account_code = new_accounts.get(line.line_no, line.account_code)
        entry.corrections = {"changes": changes, "by": approver.full_name}
    entry.status = "POSTED"
    entry.approved_by_id = approver.id
    entry.posted_at = datetime.now(timezone.utc)
    period = period_of(entry.entry_date)
    for line in entry.lines:
        db.add(FinLedgerLine(
            tenant_id=entry.tenant_id, period=period, entry_date=entry.entry_date,
            account_code=line.account_code, debit=line.debit, credit=line.credit,
            party_id=line.party_id, description=line.description or entry.description,
            source="JOURNAL", journal_entry_id=entry.id,
            voucher_no=(f"{entry.invoice.series}-{entry.invoice.number}" if entry.invoice else None),
        ))
    invoice = entry.invoice
    if invoice is not None:
        invoice.status = "POSTED"
        party = db.get(FinParty, invoice.party_id) if invoice.party_id else None
        if party is not None:
            party.is_new = False
        main_line = entry.lines[0] if invoice.direction == "IN" else entry.lines[1]
        if party is not None and not edited:
            # Only an unchanged approval teaches the rule; a corrected one is shown to the
            # model next time instead, until an approver accepts a proposal as it is.
            rule = db.query(FinPostingRule).filter(
                FinPostingRule.tenant_id == entry.tenant_id,
                FinPostingRule.party_id == party.id,
                FinPostingRule.direction == invoice.direction,
            ).first()
            if rule is None:
                db.add(FinPostingRule(
                    tenant_id=entry.tenant_id, party_id=party.id, direction=invoice.direction,
                    main_account=main_line.account_code, times_confirmed=1,
                ))
            elif rule.main_account == main_line.account_code:
                rule.times_confirmed += 1
            else:
                rule.main_account = main_line.account_code
                rule.times_confirmed = 1
    db.flush()


register_handler(JOURNAL_APPROVAL, _finalize, _validate_edit)


def serialize_entry(entry: FinJournalEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "entry_date": entry.entry_date.isoformat(),
        "description": entry.description,
        "status": entry.status,
        "source": entry.source,
        "invoice_id": str(entry.invoice_id) if entry.invoice_id else None,
        "proposed_by": entry.proposed_by,
        "confidence": entry.confidence,
        "confidence_reasons": entry.confidence_reasons or [],
        "corrections": entry.corrections,
        "workflow_id": str(entry.workflow_id) if entry.workflow_id else None,
        "posted_at": entry.posted_at.isoformat() if entry.posted_at else None,
        "lines": [
            {
                "line_no": line.line_no,
                "account_code": line.account_code,
                "debit": plain(line.debit),
                "credit": plain(line.credit),
            }
            for line in entry.lines
        ],
    }
