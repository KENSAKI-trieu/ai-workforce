"""Finance tools: what the Finance agent reads from the books and drafts into them.

Each result carries `source`, naming the rows a figure came from, so an answer can say
where every number is from. Amounts are exact decimal strings: the model repeats them, it
never adds them up.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_

from app.agents.usage import _llm_usage_recorder
from app.domains.finance.invoice_intake import serialize_invoice
from app.domains.finance.journal_proposal import (
    ProposalRefused,
    describe,
    propose_for_invoice,
    serialize_entry,
)
from app.domains.finance.money import format_vnd
from app.domains.finance.payments import DraftRefused, draft_payment_reminder, draft_payment_voucher
from app.domains.finance.reports import (
    PartyNotFound,
    account_balance,
    aging,
    budget_vs_actual,
    ledger_detail,
    payment_schedule,
    period_bounds,
    resolve_party,
    trial_balance,
)
from app.domains.platform.position_service import user_permissions
from app.plugins.resolver import resolve_prompt_overlay
from app.domains.platform.audit_service import (
    get_cost_by_agent,
    get_cost_by_department,
    get_cost_by_employee,
    get_cost_by_workflow,
    get_llm_cost_summary,
)
from app.models.models import FinInvoice, FinParty, Tenant
from app.tools.registry import ToolContext
from app.tools.schemas import (
    AccountBalanceInput,
    AgingInput,
    BudgetVsActualInput,
    DraftPaymentReminderInput,
    DraftPaymentVoucherInput,
    ExpenseLookupInput,
    InvoiceLookupInput,
    LedgerDetailInput,
    PaymentScheduleInput,
    ProposeJournalEntryInput,
    TrialBalanceInput,
)


def lookup_expenses(
    context: ToolContext,
    request: ExpenseLookupInput,
) -> dict[str, Any] | list[dict[str, Any]]:
    handlers = {
        "SUMMARY": get_llm_cost_summary,
        "AGENT": get_cost_by_agent,
        "EMPLOYEE": get_cost_by_employee,
        "DEPARTMENT": get_cost_by_department,
        "WORKFLOW": get_cost_by_workflow,
    }
    return handlers[request.breakdown](context.db, context.actor.tenant_id, request.month)


def lookup_invoices(context: ToolContext, request: InvoiceLookupInput) -> dict[str, Any]:
    db, tenant_id = context.db, context.actor.tenant_id
    query = db.query(FinInvoice).filter(FinInvoice.tenant_id == tenant_id)
    if request.status:
        query = query.filter(FinInvoice.status == request.status)
    if request.direction:
        query = query.filter(FinInvoice.direction == request.direction)
    if request.number:
        query = query.filter(FinInvoice.number == request.number.lstrip("0"))
    if request.party:
        term = request.party.strip()
        query = query.outerjoin(FinParty, FinParty.id == FinInvoice.party_id).filter(or_(
            FinParty.tax_code == term,
            FinParty.name.ilike(f"%{term}%"),
            FinInvoice.seller_name.ilike(f"%{term}%"),
        ))
    if request.period:
        start, end = period_bounds(request.period)
        query = query.filter(FinInvoice.issue_date >= start, FinInvoice.issue_date < end)
    total = query.count()
    rows = query.order_by(FinInvoice.issue_date.desc().nullslast(), FinInvoice.created_at.desc()).limit(request.limit).all()
    return {
        "count": total,
        "shown": len(rows),
        "invoices": [serialize_invoice(row) for row in rows],
        "source": "fin_invoices",
    }


def propose_journal_entry(context: ToolContext, request: ProposeJournalEntryInput) -> dict[str, Any]:
    """Draft the entry for one invoice and send it for approval. Terminal: its reply is the answer."""
    db, actor = context.db, context.actor
    try:
        entry, created = propose_for_invoice(
            db, actor, request.invoice_id,
            main_account=request.main_account,
            prompts=resolve_prompt_overlay(db, actor.tenant_id, "FINANCE"),
            on_usage=_llm_usage_recorder(db, actor, "FINANCE"),
        )
    except ProposalRefused as exc:
        return {"status": "REFUSED", "created": False, "reply": str(exc)}
    # The gateway commits after the call, together with its audit row.
    total = sum((line.debit for line in entry.lines), start=0)
    if not created:
        state = "đã được ghi sổ" if entry.status == "POSTED" else "đang chờ duyệt"
        reply = f"Hoá đơn này đã có bút toán {state}: {describe(entry)}."
    else:
        doubt = (
            " Lưu ý cho người duyệt: " + "; ".join(entry.confidence_reasons) + "."
            if entry.confidence_reasons else ""
        )
        reply = (
            f"Đã lập bút toán nháp và gửi duyệt ({format_vnd(total)}): {describe(entry)}."
            f"{doubt} Bút toán chỉ được ghi sổ khi người có thẩm quyền duyệt."
        )
    return {"status": entry.status, "created": created, "reply": reply, "entry": serialize_entry(entry)}


# --------------------------------------------------------------------------- reading the books


def _today() -> date:
    return date.today()


def get_account_balance(context: ToolContext, request: AccountBalanceInput) -> dict[str, Any]:
    return account_balance(context.db, context.actor.tenant_id, request.account, request.period)


def get_trial_balance(context: ToolContext, request: TrialBalanceInput) -> dict[str, Any]:
    return trial_balance(context.db, context.actor.tenant_id, request.period, level=request.level)


def get_ledger_detail(context: ToolContext, request: LedgerDetailInput) -> dict[str, Any]:
    party_id = None
    if request.party:
        try:
            party_id = resolve_party(context.db, context.actor.tenant_id, request.party).id
        except PartyNotFound as exc:
            return {"found": False, "message": str(exc)}
    return ledger_detail(
        context.db, context.actor.tenant_id, request.account, request.date_from, request.date_to,
        party_id=party_id, limit=request.limit,
    )


def get_budget_vs_actual(context: ToolContext, request: BudgetVsActualInput) -> dict[str, Any]:
    """Every department for whoever reads the books; their own one for everyone else.

    The department is forced here, not left to the model: someone holding only "Xem ngân
    sách phòng mình" gets their own department whatever was asked for.
    """
    granted = user_permissions(context.db, context.actor)
    department = request.department
    if "finance.ledger.view" not in granted:
        if "finance.budget.view_own" not in granted:
            raise HTTPException(status_code=403, detail="Access denied for tool 'budget_vs_actual'")
        own = (context.actor.department or "").upper()
        if department and department.upper() != own:
            return {"found": False, "message": f"Bạn chỉ được xem ngân sách của phòng {own}."}
        department = own
    return budget_vs_actual(context.db, context.actor.tenant_id, request.period, department=department)


def get_aging(context: ToolContext, request: AgingInput) -> dict[str, Any]:
    party_id = None
    if request.party:
        try:
            party_id = resolve_party(context.db, context.actor.tenant_id, request.party).id
        except PartyNotFound as exc:
            return {"found": False, "message": str(exc)}
    return aging(
        context.db, context.actor.tenant_id,
        "OUT" if request.kind == "RECEIVABLE" else "IN",
        request.as_of or _today(),
        min_days_overdue=request.min_days_overdue,
        party_id=party_id,
    )


def get_payment_schedule(context: ToolContext, request: PaymentScheduleInput) -> dict[str, Any]:
    return payment_schedule(context.db, context.actor.tenant_id, request.as_of or _today(), request.horizon_days)


# --------------------------------------------------------------------------- drafts for approval


def draft_voucher(context: ToolContext, request: DraftPaymentVoucherInput) -> dict[str, Any]:
    """Terminal: a payment voucher waiting for whoever its amount requires."""
    try:
        approval = draft_payment_voucher(context.db, context.actor, list(request.invoice_ids))
    except DraftRefused as exc:
        return {"status": "REFUSED", "created": False, "reply": str(exc)}
    payload = approval.payload
    warnings = " Lưu ý: " + "; ".join(payload["warnings"]) + "." if payload.get("warnings") else ""
    return {
        "status": "PENDING_APPROVAL",
        "created": True,
        "approval_id": str(approval.id),
        "reply": (
            f"Đã lập phiếu chi nháp {format_vnd(Decimal(payload['amount']))} cho {payload['party']['name']} "
            f"({len(payload['invoices'])} hoá đơn) và gửi duyệt.{warnings} Hệ thống không tự chuyển tiền: "
            "sau khi được duyệt, người thực hiện chuyển khoản trên ngân hàng rồi ghi nhận đã thanh toán."
        ),
    }


def draft_reminder(context: ToolContext, request: DraftPaymentReminderInput) -> dict[str, Any]:
    """Terminal: a reminder letter waiting for approval; nothing is sent before that."""
    db, actor = context.db, context.actor
    try:
        party = resolve_party(db, actor.tenant_id, request.party)
        tenant = db.get(Tenant, actor.tenant_id)
        approval = draft_payment_reminder(
            db, actor, party, request.level, company_name=tenant.name if tenant else "", as_of=_today(),
        )
    except (PartyNotFound, DraftRefused) as exc:
        return {"status": "REFUSED", "created": False, "reply": str(exc)}
    payload = approval.payload
    return {
        "status": "PENDING_APPROVAL",
        "created": True,
        "approval_id": str(approval.id),
        "reply": (
            f"Đã soạn thư nhắc nợ mức {request.level} gửi {party.name} ({payload['recipient']}), "
            f"tổng {format_vnd(Decimal(payload['amount']))}, và gửi duyệt. Thư chỉ được gửi đi khi "
            "người duyệt đồng ý."
        ),
    }
