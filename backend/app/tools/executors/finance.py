"""Finance tools: what the Finance agent reads from the books and drafts into them.

Each result carries `source`, naming the rows a figure came from, so an answer can say
where every number is from. Amounts are exact decimal strings: the model repeats them, it
never adds them up.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import or_

from app.domains.finance.invoice_intake import serialize_invoice
from app.domains.platform.audit_service import (
    get_cost_by_agent,
    get_cost_by_department,
    get_cost_by_employee,
    get_cost_by_workflow,
    get_llm_cost_summary,
)
from app.models.models import FinInvoice, FinParty
from app.tools.registry import ToolContext
from app.tools.schemas import ExpenseLookupInput, InvoiceLookupInput


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


def _period_bounds(period: str) -> tuple[date, date]:
    year, month = int(period[:4]), int(period[5:])
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


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
        start, end = _period_bounds(request.period)
        query = query.filter(FinInvoice.issue_date >= start, FinInvoice.issue_date < end)
    total = query.count()
    rows = query.order_by(FinInvoice.issue_date.desc().nullslast(), FinInvoice.created_at.desc()).limit(request.limit).all()
    return {
        "count": total,
        "shown": len(rows),
        "invoices": [serialize_invoice(row) for row in rows],
        "source": "fin_invoices",
    }
