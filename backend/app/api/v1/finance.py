"""Finance books: settings, chart of accounts and Excel imports.

What the Finance agent reads comes in here. Every route checks a position permission;
nothing in this router posts to the ledger on its own -- imports load history a company
already booked elsewhere, and new postings go through approval.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import PermissionRequired, get_current_active_user
from app.domains.finance import excel_import
from app.domains.finance.approvals import is_finance_approval, withdraw_finance_approval
from app.domains.finance.charts import CHARTS
from app.domains.finance.money import normalize_period, normalize_tax_code, plain
from app.domains.finance.settings import (
    get_settings,
    seed_chart,
    thresholds,
    validate_thresholds,
)
from app.agents.usage import _llm_usage_recorder
from app.domains.finance.einvoice_xml import InvoiceNotReadable
from app.domains.finance.invoice_intake import intake_invoice, serialize_invoice
from app.domains.finance.journal_proposal import ProposalRefused, propose_for_invoice, serialize_entry
from app.domains.finance.payments import (
    DraftRefused,
    draft_payment_reminder,
    draft_payment_voucher,
    record_voucher_paid,
)
from app.domains.finance.reports import (
    account_balance,
    account_trend,
    aging,
    budget_vs_actual,
    expense_breakdown,
    journal_workbook,
    own_budget_department,
    payment_schedule,
    period_bounds,
    trial_balance,
)
from app.domains.finance.sheets import (
    SheetRejected,
    analyze as analyze_rows,
    create_sheet,
    describe as describe_sheet,
    own_sheet,
    own_sheets,
    preview,
    reread_sheet,
)
from app.domains.finance.visuals import (
    aging_chart,
    balance_chart,
    budget_chart,
    expense_chart,
    schedule_chart,
    sheet_chart,
    trend_chart,
)
from app.domains.platform.position_service import has_permission
from app.domains.finance.storage import delete_stored_file, read_invoice_file
from app.domains.platform.audit_events import add_audit_event
from app.models.models import (
    FinSheet,
    FinAccount,
    FinImportBatch,
    FinInvoice,
    FinJournalEntry,
    FinParty,
    AgentWorkflow,
    FinPayment,
    Tenant,
    User,
    WorkflowApproval,
)
from app.plugins.resolver import resolve_prompt_overlay

router = APIRouter(prefix="/finance", tags=["Finance"])

MAX_IMPORT_BYTES = 10 * 1024 * 1024
_READERS = (
    "finance.ledger.view",
    "finance.invoice.process",
    "finance.journal.draft",
    "finance.ar_ap.view",
    "finance.import.manage",
)


def _settings_payload(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings(db, tenant_id)
    return {
        "chart": settings.chart,
        "company_tax_code": settings.company_tax_code,
        "approval_thresholds": [
            {"up_to": plain(item["up_to"]), "permission": item["permission"]}
            for item in thresholds(settings)
        ],
        "po_tolerance_percent": plain(settings.po_tolerance_percent),
        "account_count": db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id).count(),
        "charts": sorted(CHARTS),
    }


@router.get("/settings", summary="Finance settings of the workspace")
def read_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired(*_READERS)),
):
    payload = _settings_payload(db, current_user.tenant_id)
    db.commit()  # the settings row is created on first read
    return payload


class SettingsUpdate(BaseModel):
    chart: str | None = Field(default=None, pattern="^(TT200|TT133)$")
    company_tax_code: str | None = None
    approval_thresholds: list[dict[str, Any]] | None = None
    po_tolerance_percent: Decimal | None = Field(default=None, ge=0, le=100)


@router.put("/settings", summary="Change the chart, approval thresholds or PO tolerance")
def update_settings(
    body: SettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.import.manage")),
):
    settings = get_settings(db, current_user.tenant_id)
    before = _settings_payload(db, current_user.tenant_id)
    added = 0
    if body.approval_thresholds is not None:
        try:
            settings.approval_thresholds = validate_thresholds(body.approval_thresholds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.company_tax_code is not None:
        try:
            settings.company_tax_code = normalize_tax_code(body.company_tax_code)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.po_tolerance_percent is not None:
        settings.po_tolerance_percent = body.po_tolerance_percent
    if body.chart is not None:
        settings.chart = body.chart
        # Additive: the company's own accounts and names stay as they are.
        added = seed_chart(db, current_user.tenant_id, body.chart)
    db.flush()
    after = _settings_payload(db, current_user.tenant_id)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.settings.updated",
        resource_type="FIN_SETTINGS",
        before_data=before,
        after_data=after,
        request=request,
    )
    db.commit()
    return {**after, "accounts_added": added}


@router.get("/accounts", summary="Chart of accounts")
def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired(*_READERS)),
):
    rows = db.query(FinAccount).filter(
        FinAccount.tenant_id == current_user.tenant_id
    ).order_by(FinAccount.code).all()
    return [
        {
            "code": row.code,
            "name": row.name,
            "parent_code": row.parent_code,
            "normal_balance": row.normal_balance,
            "is_active": row.is_active,
        }
        for row in rows
    ]


@router.get("/parties", summary="Vendors and customers")
def list_parties(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired(*_READERS)),
):
    rows = db.query(FinParty).filter(
        FinParty.tenant_id == current_user.tenant_id
    ).order_by(FinParty.name).all()
    # The bank account is left out: it is read only where a payment is prepared.
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "tax_code": row.tax_code,
            "name": row.name,
            "email": row.email,
            "payment_terms_days": row.payment_terms_days,
            "is_new": row.is_new,
        }
        for row in rows
    ]


@router.get("/import/kinds", summary="What can be imported, with its columns")
def import_kinds(current_user: User = Depends(PermissionRequired("finance.import.manage"))):
    return [
        {
            "kind": kind,
            "title": spec.title,
            "guide": spec.guide,
            "undoable": spec.undoable,
            "columns": [
                {"key": column.key, "label": column.label, "required": column.required}
                for column in spec.columns
            ],
        }
        for kind, spec in excel_import.KINDS.items()
    ]


@router.get("/import/{kind}/template", summary="Empty Excel template for one import")
def import_template(
    kind: str,
    current_user: User = Depends(PermissionRequired("finance.import.manage")),
):
    if kind not in excel_import.KINDS:
        raise HTTPException(status_code=404, detail="Unknown import kind")
    return Response(
        content=excel_import.template_workbook(kind),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="mau-nhap-{kind}.xlsx"'},
    )


@router.post("/import/{kind}", summary="Import one Excel file; all rows or none")
async def import_file(
    kind: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.import.manage")),
):
    if kind not in excel_import.KINDS:
        raise HTTPException(status_code=404, detail="Unknown import kind")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import files are limited to 10 MB")
    filename = Path(file.filename or f"{kind}.xlsx").name
    try:
        batch = excel_import.import_books(db, current_user, kind, filename, data)
    except excel_import.ImportRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"message": "File chưa được nhập vì có lỗi", "errors": exc.errors},
        ) from exc
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.import.committed",
        resource_type="FIN_IMPORT_BATCH",
        resource_id=str(batch.id),
        input_parameters={"kind": kind, "filename": filename},
        output_result={"row_count": batch.row_count},
        request=request,
    )
    db.commit()
    return _batch_payload(batch)


def _batch_payload(batch: FinImportBatch) -> dict[str, Any]:
    return {
        "id": str(batch.id),
        "kind": batch.kind,
        "filename": batch.filename,
        "row_count": batch.row_count,
        "status": batch.status,
        "undoable": excel_import.KINDS[batch.kind].undoable,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


@router.get("/import/batches", summary="Previous imports")
def list_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.import.manage")),
):
    rows = db.query(FinImportBatch).filter(
        FinImportBatch.tenant_id == current_user.tenant_id
    ).order_by(FinImportBatch.created_at.desc()).limit(100).all()
    return [_batch_payload(row) for row in rows]


@router.delete("/import/batches/{batch_id}", summary="Undo one import")
def undo_import(
    batch_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.import.manage")),
):
    batch = db.query(FinImportBatch).filter(
        FinImportBatch.id == batch_id,
        FinImportBatch.tenant_id == current_user.tenant_id,
    ).first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if batch.status == "ROLLED_BACK":
        raise HTTPException(status_code=409, detail="This import was already undone")
    try:
        removed = excel_import.undo_batch(db, batch)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.import.undone",
        resource_type="FIN_IMPORT_BATCH",
        resource_id=str(batch.id),
        output_result={"rows_removed": removed},
        request=request,
    )
    db.commit()
    return {**_batch_payload(batch), "rows_removed": removed}


# --------------------------------------------------------------------------- invoices


@router.post("/invoices/upload", summary="Upload e-invoices (XML, or PDF with a text layer)")
async def upload_invoices(
    request: Request,
    files: list[UploadFile] = File(...),
    direction: str = "IN",
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.invoice.process")),
):
    if direction not in {"IN", "OUT"}:
        raise HTTPException(status_code=422, detail="direction must be IN or OUT")
    if len(files) > 20:
        raise HTTPException(status_code=422, detail="At most 20 invoices per upload")
    prompts = resolve_prompt_overlay(db, current_user.tenant_id, "FINANCE")
    on_usage = _llm_usage_recorder(db, current_user, "FINANCE")
    results: list[dict[str, Any]] = []
    for upload in files:
        filename = Path(upload.filename or "invoice").name
        data = await upload.read()
        try:
            outcome = intake_invoice(
                db, current_user, filename=filename, data=data, direction=direction,
                prompts=prompts, on_usage=on_usage,
            )
        except InvoiceNotReadable as exc:
            db.rollback()
            results.append({"filename": filename, "created": False, "error": str(exc)})
            continue
        add_audit_event(
            db,
            tenant_id=current_user.tenant_id,
            actor_user=current_user,
            agent_role="FINANCE",
            action="finance.invoice.uploaded" if outcome.created else "finance.invoice.duplicate",
            resource_type="FIN_INVOICE",
            resource_id=str(outcome.invoice.id),
            input_parameters={"filename": filename, "direction": direction},
            output_result={"status": outcome.invoice.status},
            request=request,
        )
        db.commit()
        results.append({
            "filename": filename,
            "created": outcome.created,
            "message": outcome.message,
            "invoice": serialize_invoice(outcome.invoice),
        })
    return results


@router.get("/invoices", summary="Invoices, newest first")
def list_invoices(
    status: str | None = None,
    direction: str | None = None,
    party_id: uuid.UUID | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.invoice.process", "finance.ledger.view", "finance.ar_ap.view")),
):
    query = db.query(FinInvoice).filter(FinInvoice.tenant_id == current_user.tenant_id)
    if status:
        query = query.filter(FinInvoice.status == status.upper())
    if direction:
        query = query.filter(FinInvoice.direction == direction.upper())
    if party_id:
        query = query.filter(FinInvoice.party_id == party_id)
    rows = query.order_by(FinInvoice.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return [serialize_invoice(row) for row in rows]


def _invoice_or_404(db: Session, user: User, invoice_id: uuid.UUID) -> FinInvoice:
    invoice = db.query(FinInvoice).filter(
        FinInvoice.id == invoice_id, FinInvoice.tenant_id == user.tenant_id
    ).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/invoices/{invoice_id}", summary="One invoice with its lines")
def read_invoice_detail(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.invoice.process", "finance.ledger.view", "finance.ar_ap.view")),
):
    return serialize_invoice(_invoice_or_404(db, current_user, invoice_id), with_lines=True)


@router.get("/invoices/{invoice_id}/file", summary="Download the uploaded invoice file")
def download_invoice_file(
    invoice_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.invoice.process")),
):
    invoice = _invoice_or_404(db, current_user, invoice_id)
    if not invoice.source_storage_key:
        raise HTTPException(status_code=404, detail="This invoice has no uploaded file")
    content = read_invoice_file(invoice.source_storage_key)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.invoice.file_read",
        resource_type="FIN_INVOICE",
        resource_id=str(invoice.id),
        request=request,
    )
    db.commit()
    media = "application/xml" if invoice.source_format == "XML" else "application/pdf"
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.source_filename or "invoice"}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


class InvoiceReview(BaseModel):
    action: str = Field(pattern="^(ACCEPT|REJECT)$")
    note: str = Field(min_length=3, max_length=1000)


@router.post("/invoices/{invoice_id}/review", summary="Clear an invoice's exceptions, or reject it")
def review_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceReview,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.invoice.process")),
):
    invoice = _invoice_or_404(db, current_user, invoice_id)
    if invoice.status not in {"EXCEPTION", "MATCHED", "RECEIVED"}:
        raise HTTPException(status_code=409, detail=f"Invoice is already {invoice.status}")
    before = invoice.status
    invoice.status = "MATCHED" if body.action == "ACCEPT" else "REJECTED"
    # The exceptions stay on the record with who cleared them and why: an auditor asks
    # exactly this when a flagged invoice was paid.
    invoice.exceptions = [
        {**item, "resolved_by": current_user.full_name, "resolution": body.note}
        if item.get("severity") == "BLOCKING" and "resolved_by" not in item else item
        for item in (invoice.exceptions or [])
    ]
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action=f"finance.invoice.{body.action.lower()}",
        resource_type="FIN_INVOICE",
        resource_id=str(invoice.id),
        before_data={"status": before},
        after_data={"status": invoice.status, "note": body.note},
        request=request,
    )
    db.commit()
    return serialize_invoice(invoice, with_lines=True)


# --------------------------------------------------------------------------- journal entries


class ProposeEntry(BaseModel):
    main_account: str | None = Field(default=None, pattern=r"^\d{3,10}$")


@router.post("/invoices/{invoice_id}/propose-entry", summary="Draft the journal entry for an invoice")
def propose_entry(
    invoice_id: uuid.UUID,
    body: ProposeEntry,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.journal.draft")),
):
    try:
        entry, created = propose_for_invoice(
            db, current_user, invoice_id,
            main_account=body.main_account,
            prompts=resolve_prompt_overlay(db, current_user.tenant_id, "FINANCE"),
            on_usage=_llm_usage_recorder(db, current_user, "FINANCE"),
        )
    except ProposalRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.journal.proposed" if created else "finance.journal.existing",
        resource_type="FIN_JOURNAL_ENTRY",
        resource_id=str(entry.id),
        output_result={"status": entry.status, "confidence": entry.confidence},
        request=request,
    )
    db.commit()
    return {"created": created, "entry": serialize_entry(entry)}


@router.get("/journal-entries", summary="Journal entries drafted or posted here")
def list_journal_entries(
    status: str | None = None,
    period: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.journal.draft", "finance.ledger.view")),
):
    query = db.query(FinJournalEntry).filter(FinJournalEntry.tenant_id == current_user.tenant_id)
    if status:
        query = query.filter(FinJournalEntry.status == status.upper())
    if period:
        try:
            start, end = period_bounds(normalize_period(period))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        query = query.filter(FinJournalEntry.entry_date >= start, FinJournalEntry.entry_date < end)
    rows = query.order_by(FinJournalEntry.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return [serialize_entry(row) for row in rows]


@router.get("/export/journal.xlsx", summary="Posted entries of a period, to import into the accounting software")
def export_journal(
    period: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view")),
):
    try:
        normalized = normalize_period(period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    start, end = period_bounds(normalized)
    entries = db.query(FinJournalEntry).filter(
        FinJournalEntry.tenant_id == current_user.tenant_id,
        FinJournalEntry.status == "POSTED",
        FinJournalEntry.entry_date >= start,
        FinJournalEntry.entry_date < end,
    ).order_by(FinJournalEntry.entry_date, FinJournalEntry.created_at).all()
    content = journal_workbook(entries)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="but-toan-{normalized}.xlsx"'},
    )


# --------------------------------------------------------------------------- reports


def _with_charts(report: dict[str, Any], builder) -> dict[str, Any]:
    """The report and the charts drawn from it: the same builders the chat uses."""
    return {**report, "charts": builder(report)}


def _period_or_422(period: str) -> str:
    try:
        return normalize_period(period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/reports/balance", summary="Opening, movement and closing balance of one account")
def report_balance(
    account: str,
    period: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view")),
):
    return _with_charts(account_balance(db, current_user.tenant_id, account, _period_or_422(period)), balance_chart)


@router.get("/reports/trial-balance", summary="Trial balance of a period")
def report_trial_balance(
    period: str,
    level: int = 3,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view")),
):
    return trial_balance(db, current_user.tenant_id, _period_or_422(period), level=4 if level == 4 else 3)


@router.get("/reports/budget", summary="Budget against actual; own department only without the ledger box")
def report_budget(
    period: str,
    department: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view", "finance.budget.view_own")),
):
    if not has_permission(db, current_user, "finance.ledger.view"):
        department = own_budget_department(current_user)
        if department is None:
            raise HTTPException(status_code=403, detail="Tài khoản chưa gắn phòng ban nào để xem ngân sách phòng mình")
    return _with_charts(
        budget_vs_actual(db, current_user.tenant_id, _period_or_422(period), department=department), budget_chart,
    )


@router.get("/reports/aging", summary="Aging of receivables or payables")
def report_aging(
    kind: str = "RECEIVABLE",
    as_of: date | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ar_ap.view")),
):
    if kind not in {"RECEIVABLE", "PAYABLE"}:
        raise HTTPException(status_code=422, detail="kind must be RECEIVABLE or PAYABLE")
    return _with_charts(
        aging(db, current_user.tenant_id, "OUT" if kind == "RECEIVABLE" else "IN", as_of or date.today()), aging_chart,
    )


@router.get("/reports/payment-schedule", summary="Purchase invoices falling due")
def report_payment_schedule(
    horizon_days: int = 14,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ar_ap.view")),
):
    return _with_charts(
        payment_schedule(db, current_user.tenant_id, date.today(), max(1, min(horizon_days, 90))), schedule_chart,
    )


@router.get("/reports/account-trend", summary="One account month by month, with its charts")
def report_account_trend(
    account: str,
    from_period: str,
    to_period: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view")),
):
    if not account.isdigit():
        raise HTTPException(status_code=422, detail="Số tài khoản chỉ gồm chữ số")
    return _with_charts(
        account_trend(db, current_user.tenant_id, account, _period_or_422(from_period), _period_or_422(to_period)),
        trend_chart,
    )


@router.get("/reports/expenses", summary="Expenses by account or department, with a chart")
def report_expenses(
    from_period: str,
    to_period: str,
    group_by: str = "account",
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.ledger.view")),
):
    if group_by not in {"account", "department"}:
        raise HTTPException(status_code=422, detail="group_by must be account or department")
    return _with_charts(
        expense_breakdown(
            db, current_user.tenant_id, _period_or_422(from_period), _period_or_422(to_period), group_by=group_by,
        ),
        expense_chart,
    )


# --------------------------------------------------------------------------- vouchers and reminders


class VoucherDraft(BaseModel):
    invoice_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


@router.post("/payments/vouchers", summary="Draft a payment voucher for approval")
def create_voucher(
    body: VoucherDraft,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.journal.draft")),
):
    try:
        approval = draft_payment_voucher(db, current_user, body.invoice_ids)
    except DraftRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_event(
        db, tenant_id=current_user.tenant_id, actor_user=current_user, agent_role="FINANCE",
        action="finance.voucher.drafted", resource_type="APPROVAL", resource_id=str(approval.id),
        output_result={"amount": approval.payload.get("amount")}, request=request,
    )
    db.commit()
    return {"approval_id": str(approval.id), "workflow_id": str(approval.workflow_id), "payload": approval.payload}


class VoucherPaid(BaseModel):
    paid_on: date
    bank_reference: str = Field(min_length=2, max_length=60)


@router.post("/payments/vouchers/{workflow_id}/paid", summary="Record that an approved voucher was paid at the bank")
def mark_voucher_paid(
    workflow_id: uuid.UUID,
    body: VoucherPaid,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.journal.draft")),
):
    try:
        payments = record_voucher_paid(
            db, current_user, workflow_id, paid_on=body.paid_on, bank_reference=body.bank_reference
        )
    except DraftRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_event(
        db, tenant_id=current_user.tenant_id, actor_user=current_user, agent_role="FINANCE",
        action="finance.voucher.paid", resource_type="FIN_PAYMENT", resource_id=str(workflow_id),
        input_parameters={"paid_on": body.paid_on.isoformat(), "bank_reference": body.bank_reference},
        request=request,
    )
    db.commit()
    return {"paid": len(payments), "total": plain(sum((payment.amount for payment in payments), Decimal("0")))}


@router.get("/payments/vouchers", summary="Payment vouchers and their status")
def list_vouchers(
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.journal.draft", "finance.ar_ap.view")),
):
    rows = db.query(FinPayment).filter(
        FinPayment.tenant_id == current_user.tenant_id, FinPayment.workflow_id.isnot(None),
        FinPayment.direction == "PAY",
    ).order_by(FinPayment.created_at.desc()).limit(500).all()
    vouchers: dict[str, dict[str, Any]] = {}
    for payment in rows:
        item = vouchers.setdefault(str(payment.workflow_id), {
            "workflow_id": str(payment.workflow_id), "reference": payment.reference,
            "status": payment.status, "total": Decimal("0"), "invoice_count": 0,
            "party_id": str(payment.party_id) if payment.party_id else None,
            "created_at": payment.created_at.isoformat() if payment.created_at else None,
        })
        item["total"] += payment.amount
        item["invoice_count"] += 1
    return [{**item, "total": plain(item["total"])} for item in vouchers.values()]


class ReminderDraft(BaseModel):
    party_id: uuid.UUID
    level: int = Field(default=1, ge=1, le=3)


@router.post("/reminders", summary="Draft a payment reminder for approval")
def create_reminder(
    body: ReminderDraft,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(PermissionRequired("finance.reminder.send")),
):
    party = db.query(FinParty).filter(
        FinParty.id == body.party_id, FinParty.tenant_id == current_user.tenant_id
    ).first()
    if party is None:
        raise HTTPException(status_code=404, detail="Party not found")
    tenant = db.get(Tenant, current_user.tenant_id)
    try:
        approval = draft_payment_reminder(
            db, current_user, party, body.level, company_name=tenant.name if tenant else "", as_of=date.today(),
        )
    except DraftRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_event(
        db, tenant_id=current_user.tenant_id, actor_user=current_user, agent_role="FINANCE",
        action="finance.reminder.drafted", resource_type="APPROVAL", resource_id=str(approval.id),
        request=request,
    )
    db.commit()
    return {"approval_id": str(approval.id), "payload": approval.payload}


# --------------------------------------------------------------------------- withdrawing a draft


class Withdrawal(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/approvals/{approval_id}/withdraw", summary="Take back a finance draft nobody has decided")
def withdraw_draft(
    approval_id: uuid.UUID,
    body: Withdrawal,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """A waiting voucher locks its invoices and a waiting reminder its customer; this frees them."""
    approval = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        WorkflowApproval.id == approval_id,
        AgentWorkflow.tenant_id == current_user.tenant_id,
    ).with_for_update().first()
    if approval is None or not is_finance_approval(approval):
        raise HTTPException(status_code=404, detail="Approval request not found")
    withdraw_finance_approval(db, approval, current_user, body.reason)
    add_audit_event(
        db, tenant_id=current_user.tenant_id, actor_user=current_user, agent_role="FINANCE",
        action="finance.draft.withdrawn", resource_type="APPROVAL", resource_id=str(approval.id),
        workflow_id=approval.workflow_id,
        input_parameters={"action_type": approval.action_type, "reason": approval.comments},
        request=request,
    )
    db.commit()
    return {"id": str(approval.id), "status": approval.status}


# --------------------------------------------------------------------------- sheets to analyse
#
# Someone's own spreadsheet: whoever is signed in may upload and analyse one, and only they
# ever read it. Nothing here touches the books, so no finance box is required.


class SheetCorrection(BaseModel):
    sheet_name: str | None = Field(default=None, max_length=255)
    header_row: int | None = Field(default=None, ge=1, le=10_000)
    # {column index: NUMBER | DATE | TEXT}
    kinds: dict[int, str] | None = None
    skip_totals: bool | None = None


class SheetAnalysisRequest(BaseModel):
    operation: str = "sum"
    value_column: str | None = Field(default=None, max_length=255)
    group_by: str | None = Field(default=None, max_length=255)
    period: str | None = None
    filters: list[dict[str, str]] = Field(default_factory=list, max_length=5)


def _own_sheet_or_404(db: Session, user: User, sheet_id: uuid.UUID):
    sheet = own_sheet(db, user, sheet_id)
    if sheet is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy file")
    return sheet


@router.post("/sheets", summary="Upload a spreadsheet of one's own to analyse")
async def upload_sheet(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    filename = Path(file.filename or "bang-tinh.xlsx").name
    data = await file.read()
    try:
        sheet = create_sheet(db, current_user, filename, data)
    except SheetRejected as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="FINANCE",
        action="finance.sheet.uploaded",
        resource_type="FIN_SHEET",
        resource_id=str(sheet.id),
        after_data={"filename": filename, "rows": sheet.row_count},
    )
    db.commit()
    return preview(sheet)


@router.get("/sheets", summary="The spreadsheets one uploaded")
def list_sheets(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    sheets = own_sheets(db, current_user).order_by(FinSheet.created_at.desc()).limit(50).all()
    return [describe_sheet(sheet) for sheet in sheets]


@router.get("/sheets/{sheet_id}", summary="How a spreadsheet was read, with its first rows")
def get_sheet(sheet_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return preview(_own_sheet_or_404(db, current_user, sheet_id))


@router.patch("/sheets/{sheet_id}", summary="Read a spreadsheet again with corrections")
def correct_sheet(
    sheet_id: uuid.UUID,
    body: SheetCorrection,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    sheet = _own_sheet_or_404(db, current_user, sheet_id)
    try:
        reread_sheet(
            sheet,
            sheet_name=body.sheet_name,
            header_row=body.header_row,
            kinds=body.kinds,
            skip_totals=sheet.skip_totals if body.skip_totals is None else body.skip_totals,
        )
    except SheetRejected as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return preview(sheet)


@router.post("/sheets/{sheet_id}/analyze", summary="One fixed computation on a spreadsheet, with a chart")
def analyze_sheet(
    sheet_id: uuid.UUID,
    body: SheetAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    sheet = _own_sheet_or_404(db, current_user, sheet_id)
    try:
        result = analyze_rows(
            sheet,
            operation=body.operation,
            value_column=body.value_column or None,
            group_by=body.group_by or None,
            period=body.period or None,
            filters=body.filters,
        )
    except SheetRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _with_charts(result, sheet_chart)


@router.delete("/sheets/{sheet_id}", status_code=204, summary="Delete a spreadsheet and its file")
def delete_sheet(sheet_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    sheet = _own_sheet_or_404(db, current_user, sheet_id)
    storage_key = sheet.storage_key
    db.delete(sheet)
    db.commit()
    delete_stored_file(storage_key)
    return Response(status_code=204)
