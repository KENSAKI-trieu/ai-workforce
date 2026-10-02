"""Finance books: settings, chart of accounts and Excel imports.

What the Finance agent reads comes in here. Every route checks a position permission;
nothing in this router posts to the ledger on its own -- imports load history a company
already booked elsewhere, and new postings go through approval.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import PermissionRequired
from app.domains.finance import excel_import
from app.domains.finance.charts import CHARTS
from app.domains.finance.money import normalize_tax_code, plain
from app.domains.finance.settings import (
    get_settings,
    seed_chart,
    thresholds,
    validate_thresholds,
)
from app.agents.usage import _llm_usage_recorder
from app.domains.finance.einvoice_xml import InvoiceNotReadable
from app.domains.finance.invoice_intake import intake_invoice, serialize_invoice
from app.domains.finance.storage import read_invoice_file
from app.domains.platform.audit_events import add_audit_event
from app.models.models import FinAccount, FinImportBatch, FinInvoice, FinParty, User
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
