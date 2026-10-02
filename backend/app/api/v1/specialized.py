"""
API Endpoints for Legal, IT, Finance, and Sales domain operations.
"""

import io
import json
import logging
import mimetypes
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_db
from app.core.security import PermissionRequired, get_current_active_user
from app.models.models import AIAgent, AgentWorkflow, ContractReview, User, WorkflowApproval
from app.core.tool_permissions import grant_decision
from app.plugins.resolver import resolve_skill_restriction
from app.domains.platform.approval_access import eligible_approvers, no_approver_warning
from app.domains.platform.audit_events import add_audit_event
from app.domains.platform.audit_service import log_audit_action
from app.domains.legal import contract_review_store
from app.domains.knowledge.document_markdown import to_markdown
from app.domains.knowledge.document_parser import DocumentParseError, extract_file_text
from app.domains.legal.legal_service import (
    audit_contract_text,
    check_software_licenses,
    compare_contract_texts,
    detect_sensitive_data,
)
from app.core.encryption import EncryptionKeyMissing
from app.domains.legal.contract_document import (
    MEDIA_TYPES,
    ContractEditError,
    apply_revisions,
    capture_form,
    document_format,
    read_review_marker,
    stamp_review_marker,
)
from app.domains.legal.contract_review.analyzer import ASSESSED_REVIEW_VERSION, VALID_PERSPECTIVES
from app.domains.legal.contract_review.llm_assessment import ProgressReporter, review_with_assessment
from app.domains.legal.contract_redline import build_redline_docx
from app.domains.legal.contract_translation import (
    ContractNotReviewable,
    mark_translated,
    text_for_review,
)
from app.agents.usage import _llm_usage_recorder
# Shared with the chat path so both entry points escalate identically.
from app.domains.legal.legal_approval_service import create_legal_approval as _create_legal_approval
from app.domains.legal.legal_draft_storage import delete_legal_artifact, read_legal_artifact, save_legal_artifact
from app.domains.legal.legal_document_submission import (
    LEGAL_DOCUMENT_GENERATE_PERMISSION,
    can_approve_legal_documents,
    submit_legal_document,
)
from app.domains.legal.legal_documents import list_document_schemas, validate_document_fields
from app.domains.knowledge.rag_service import hybrid_search_documents, user_search_scope
from app.core.agent_status import refuse_under_development
from app.domains.incubating.it_service import handle_it_request
from app.domains.incubating.sales_service import handle_sales_request

router = APIRouter(tags=["Specialized Domain APIs"])
logger = logging.getLogger(__name__)


def _legal_tool_required(tool_name: str):
    """Refuse a Legal action whose tool is switched off for the tenant's Legal agent.

    These endpoints used to run whatever the configuration page said: switching a Legal
    tool off there changed nothing, so an operator who revoked it had no way to know it
    was still in use.
    """

    def dependency(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> None:
        agent = db.query(AIAgent).filter(
            AIAgent.tenant_id == current_user.tenant_id,
            AIAgent.role_code == "LEGAL",
        ).first()
        # Plugin narrowing counts here too: these endpoints used to check the agent's
        # own grants only, so a tenant package withdrawing a Legal tool left its page
        # working.
        if agent is None or grant_decision(
            tool_name,
            tools_access=agent.tools_access,
            allowed_actions=agent.allowed_actions,
            disallowed_actions=agent.disallowed_actions,
            restriction=resolve_skill_restriction(db, current_user.tenant_id, "LEGAL"),
        ) is not None:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Công cụ `{tool_name}` chưa được bật cho Legal Counsel AI. "
                    "Admin hoặc Owner có thể bật trong phần Cấu hình AI Employees."
                ),
            )

    return Depends(dependency)
MAX_LEGAL_FILE_BYTES = 10 * 1024 * 1024


class ContractAuditRequest(BaseModel):
    document_name: str = "Contract.pdf"
    contract_text: str
    represented_party: str = "NEUTRAL"


class CreateJiraTicketRequest(BaseModel):
    summary: str
    description: Optional[str] = None
    priority: str = "MEDIUM"


class SalesQuotationRequest(BaseModel):
    customer_name: str = "Khách Hàng Doanh Nghiệp"
    item_query: str


class LegalDocumentGenerateRequest(BaseModel):
    document_type: str
    output_format: str = "docx"
    fields: dict[str, Any]


class LegalDocumentValidationRequest(BaseModel):
    document_type: str
    fields: dict[str, Any]


class ContractReviewDecisionRequest(BaseModel):
    decision: str
    revised_text: Optional[str] = None
    comment: Optional[str] = None


def _legal_draft_approval(
    db: Session,
    current_user: User,
    artifact_id: str,
) -> WorkflowApproval:
    approvals = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == current_user.tenant_id,
        WorkflowApproval.action_type == "LEGAL_DOCUMENT_APPROVAL",
    ).all()
    approval = next(
        (
            item
            for item in approvals
            if str((item.payload or {}).get("artifact_id")) == artifact_id
        ),
        None,
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Legal document draft not found")
    return approval


def _legal_draft_item(approval: WorkflowApproval, current_user: User) -> dict[str, Any]:
    payload = approval.payload or {}
    is_reviewer = can_approve_legal_documents(current_user)
    is_creator = approval.workflow.initiator_id == current_user.id
    approved = approval.status == "APPROVED"
    download_variant = "draft" if is_reviewer and not approved else "approved"
    artifact_id = str(payload.get("artifact_id"))
    return {
        "artifact_id": artifact_id,
        "approval_id": str(approval.id),
        "workflow_id": str(approval.workflow_id),
        "document_type": payload.get("document_type"),
        "document_type_label": payload.get("document_type_label"),
        "filename": payload.get("filename"),
        "output_format": payload.get("output_format"),
        "status": approval.status,
        "comments": approval.comments,
        "requester_name": payload.get("requester_name"),
        "requester_id": payload.get("requester_id"),
        "submitted_at": (
            approval.workflow.created_at.isoformat()
            if approval.workflow.created_at
            else None
        ),
        "updated_at": approval.updated_at.isoformat() if approval.updated_at else None,
        "can_preview": is_reviewer or is_creator,
        "can_download": is_reviewer or (is_creator and approved),
        "download_variant": download_variant,
        "preview_url": f"/api/v1/legal/document-drafts/{artifact_id}/preview",
        "download_url": f"/api/v1/legal/document-drafts/{artifact_id}/download?variant={download_variant}",
    }


def _contract_review_for_user(
    db: Session, current_user: User, review_id: str
) -> ContractReview:
    review = contract_review_store.get_contract_review(
        db, user=current_user, review_id=review_id
    )
    if not review:
        # A review in another tenant, and one this user may not read, are the same
        # 404: the existence of a contract is itself information.
        raise HTTPException(status_code=404, detail="Contract review not found")
    return review


def _audit_review_access(
    db: Session,
    current_user: User,
    request: Request,
    *,
    action: str,
    review_id: str | None = None,
    status: str = "SUCCESS",
    output: dict[str, Any] | None = None,
) -> None:
    """Record who read a contract review, from where, and whether they were let in.

    A review holds the whole contract, and only its writes were audited: anyone who
    could open it left no trace of having done so.
    """
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        action=action,
        actor_user=current_user,
        agent_role="LEGAL",
        resource_type="CONTRACT_REVIEW",
        resource_id=review_id,
        request=request,
        status=status,
        output_result=output,
    )
    db.commit()


def _readable_review(
    db: Session, current_user: User, request: Request, review_id: str, *, action: str
) -> ContractReview:
    """The review, with the read recorded -- a refused one as DENIED, then re-raised."""
    try:
        review = _contract_review_for_user(db, current_user, review_id)
    except HTTPException:
        # Not found and not allowed are one 404 to the caller, and one DENIED here.
        _audit_review_access(db, current_user, request, action=action, review_id=review_id, status="DENIED")
        raise
    _audit_review_access(db, current_user, request, action=action, review_id=str(review.id))
    return review


def _review_approval(db: Session, review: ContractReview) -> WorkflowApproval | None:
    if not review.workflow_id:
        return None
    return db.query(WorkflowApproval).filter(
        WorkflowApproval.workflow_id == review.workflow_id
    ).order_by(WorkflowApproval.updated_at.desc()).first()


def _approval_state(db: Session, approval: WorkflowApproval | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    payload = approval.payload or {}
    # Who can still decide it: a CRITICAL review sent by the only critical signer waits
    # on no one, and the reviewer has to be told rather than left waiting.
    approver_count = len(eligible_approvers(db, approval)) if approval.status == "WAITING" else None
    return {
        "approval_id": str(approval.id),
        "workflow_id": str(approval.workflow_id),
        "status": approval.status,
        "risk_level": approval.risk_level,
        "submitted_manually": bool(payload.get("submitted_manually")),
        "submitted_at": payload.get("submitted_at") or (
            approval.workflow.created_at.isoformat()
            if approval.workflow and approval.workflow.created_at else None
        ),
        "decided_at": (
            approval.updated_at.isoformat()
            if approval.status != "WAITING" and approval.updated_at else None
        ),
        "comments": approval.comments,
        "approver_name": approval.approver.full_name if getattr(approval, "approver", None) else None,
        "revised_document_attached": bool(payload.get("revised_document")),
        "eligible_approver_count": approver_count,
        "warning": no_approver_warning(approval) if approver_count == 0 else None,
    }


def _contract_review_item(
    review: ContractReview,
    decisions: list[dict[str, Any]],
    current_user: User,
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    accepted = sum(item["decision"] in {"ACCEPTED", "EDITED"} for item in decisions)
    approval = _approval_state(db, _review_approval(db, review)) if db is not None else None
    return {
        "review_id": str(review.id),
        "workflow_id": str(review.workflow_id) if review.workflow_id else None,
        "document_name": review.document_name,
        "source": review.source,
        "contract_type": review.contract_type,
        "contract_type_label": (review.result or {}).get("contract_type_label"),
        "represented_party": review.represented_party,
        "represented_party_label": (review.result or {}).get("represented_party_label"),
        "risk_score": review.risk_score,
        "risk_level": review.risk_level,
        "total_findings": review.total_findings,
        "decided_count": len(decisions),
        "accepted_count": accepted,
        "status": review.status,
        "created_by_name": review.created_by.full_name if review.created_by else None,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
        "redline_ready": accepted > 0,
        "redline_url": f"/api/v1/legal/contract-reviews/{review.id}/redline",
        "can_decide": contract_review_store.can_access_contract_review(current_user, review),
        "document": contract_review_store.document_state(review, decisions),
        "approval": approval,
        # The creator's, and not while an approval is waiting on it (the endpoint refuses).
        "can_delete": contract_review_store.can_delete_contract_review(current_user, review)
        and not (approval and approval["status"] == "WAITING"),
        "review_round": contract_review_store.review_round(review),
        "parent_review_id": str(review.parent_review_id) if review.parent_review_id else None,
    }


async def _legal_file_bytes(file: UploadFile) -> tuple[str, bytes]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > MAX_LEGAL_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Legal files are limited to 10 MB")
    return Path(file.filename or "document.txt").name, data


async def _read_legal_file(file: UploadFile) -> tuple[str, str, list[str]]:
    filename, data = await _legal_file_bytes(file)
    text, headers = _parse_legal_bytes(filename, data)
    return filename, text, headers


def _parse_legal_bytes(filename: str, data: bytes) -> tuple[str, list[str]]:
    extension = Path(filename).suffix.lower()
    headers: list[str] = []
    try:
        if extension == ".json":
            payload = json.loads(data.decode("utf-8-sig"))
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            headers = list(payload) if isinstance(payload, dict) else []
        elif extension == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows: list[str] = []
            for sheet in workbook.worksheets:
                rows.append(f"# {sheet.title}")
                for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                    values = ["" if value is None else str(value) for value in row]
                    if row_index == 0:
                        headers.extend(value for value in values if value)
                    rows.append(" | ".join(values))
            text = "\n".join(rows)
        else:
            # A PDF or DOCX is read as Markdown: tables in place, Word's article numbers
            # kept, page headers dropped. Anything else as plain text.
            markdown = to_markdown(filename, data)
            text = markdown if markdown is not None else extract_file_text(filename, data)
            if extension == ".csv" and text:
                headers = [part.strip() for part in text.splitlines()[0].split("|")]
    except (DocumentParseError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # A scanned PDF has no text layer but still yields its page markers, so it passed as
    # "text" and an empty contract was reviewed and scored.
    if not re.sub(r"\[\[PAGE:\d+\]\]", "", text).strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Không đọc được chữ trong file: có thể đây là bản scan hoặc ảnh chụp. "
                "Hệ thống chưa hỗ trợ nhận dạng chữ (OCR) cho tiếng Việt; hãy gửi bản DOCX "
                "hoặc PDF có thể chọn/copy được chữ."
            ),
        )
    return text, headers


# --- LEGAL ---
def _retrieve_contract_review_references(
    db: Session,
    current_user: User,
    contract_type_label: str,
) -> list[dict[str, Any]]:
    """Retrieve ACL-filtered internal policy/template context for Legal review."""
    try:
        chunks = hybrid_search_documents(
            db=db,
            tenant_id=current_user.tenant_id,
            query_text=(
                f"{contract_type_label} mẫu hợp đồng chuẩn policy pháp lý "
                "thanh toán trách nhiệm sở hữu trí tuệ chấm dứt bảo mật"
            ),
            top_k=6,
            **user_search_scope(db, current_user),
        )
    except Exception:
        # Contract analysis still works deterministically if the tenant has no
        # indexed legal knowledge or its retrieval service is temporarily down.
        db.rollback()
        return []

    references: list[dict[str, Any]] = []
    seen_documents: set[tuple[str, str]] = set()
    for chunk in chunks:
        name = str(chunk.get("document_title") or chunk.get("document_name") or "Tài liệu nội bộ")
        document_id = str(chunk.get("document_id") or chunk.get("document_name") or "")
        version = str(chunk.get("version") or "1.0")
        document_key = (document_id, version)
        if not document_id or document_key in seen_documents:
            continue
        seen_documents.add(document_key)
        normalized_name = name.lower()
        source_type = (
            "APPROVED_TEMPLATE"
            if any(token in normalized_name for token in ("template", "mẫu", "mau", "approved", "chuẩn"))
            else "COMPANY_POLICY"
        )
        references.append({
            "id": document_id,
            "document_id": document_id,
            "version": version,
            "type": source_type,
            "title": name,
            "section_title": chunk.get("section_title"),
            "citation_tag": chunk.get("citation_tag"),
            "score": chunk.get("score"),
            "url": "",
            "reader_url": (
                f"/api/v1/documents/{quote(document_id, safe='')}/reader"
                f"?version={quote(version, safe='')}"
            ),
            "note": "Ngữ cảnh nội bộ đã truy xuất theo ACL; Legal cần xác nhận tính áp dụng.",
        })
    return references


@router.get("/legal/document-templates", summary="List schema-driven legal document templates")
def list_legal_document_templates(
    current_user: User = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    return list_document_schemas()


@router.post("/legal/validate-document", summary="Validate a legal document draft before generation")
def validate_legal_document_endpoint(
    req: LegalDocumentValidationRequest,
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    try:
        return validate_document_fields(req.document_type, req.fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/legal/audit-contract",
    summary="Audit contract text for high-risk clauses",
    dependencies=[_legal_tool_required("audit_contract_risk")],
)
def audit_contract_endpoint(
    req: ContractAuditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    stored = contract_review_store.find_contract_review(
        db,
        user=current_user,
        contract_text=req.contract_text,
        represented_party=req.represented_party,
        review_version=ASSESSED_REVIEW_VERSION,
    )
    if stored is not None:
        result = dict(stored.result or {})
        result["review_id"] = str(stored.id)
        result["redline_url"] = f"/api/v1/legal/contract-reviews/{stored.id}/redline"
        return result
    on_usage = _llm_usage_recorder(db, current_user, "LEGAL")
    try:
        review_text = text_for_review(req.contract_text, on_usage=on_usage)
    except ContractNotReviewable as exc:
        raise HTTPException(status_code=422, detail=exc.reply) from exc
    try:
        result = mark_translated(
            review_with_assessment(
                review_text.text, req.document_name, req.represented_party, on_usage=on_usage
            ),
            review_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    review = contract_review_store.save_contract_review(
        db,
        user=current_user,
        result=result,
        contract_text=req.contract_text,
        source="API",
    )
    db.commit()
    result["review_id"] = str(review.id)
    result["redline_url"] = f"/api/v1/legal/contract-reviews/{review.id}/redline"
    return result


@router.post(
    "/legal/review-document",
    summary="Extract and review a legal document",
    dependencies=[_legal_tool_required("audit_contract_risk")],
)
async def review_legal_document(
    file: UploadFile = File(...),
    represented_party: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    filename, data = await _legal_file_bytes(file)
    return _review_uploaded_contract(db, current_user, filename, data, represented_party)


def _no_progress(stage: str, status: str, detail: str | None = None) -> None:
    return None


def _previous_review(
    db: Session,
    current_user: User,
    filename: str,
    data: bytes,
    represented_party: str,
    progress: ProgressReporter,
) -> ContractReview | None:
    """The review an uploaded file is the revised version of, when it carries its mark.

    Only a file this server wrote carries one (the revised-file download stamps it), and
    only a review the uploader may open, made for the same side by the model, is used.
    """
    fmt = document_format(filename)
    marker = read_review_marker(fmt, data) if fmt else None
    if not marker:
        progress("LINK", "skipped", "Không phải bản sửa tải từ hệ thống; rà soát như hợp đồng mới")
        return None
    previous = contract_review_store.review_from_marker(db, user=current_user, marker=marker)
    if previous is None:
        progress("LINK", "skipped", "File mang dấu của một bản rà soát bạn không mở được; rà soát như hợp đồng mới")
        return None
    earlier = previous.result or {}
    if previous.represented_party != represented_party.upper():
        progress("LINK", "skipped", "Vòng trước rà soát cho bên khác; rà soát lại toàn bộ cho bên đã chọn")
        return None
    if earlier.get("review_engine") != "LLM_ASSISTED" or earlier.get("translated_for_review") or not earlier.get("clauses"):
        progress("LINK", "skipped", "Vòng trước không có kết quả AI để đối chiếu; rà soát lại toàn bộ")
        return None
    progress(
        "LINK", "done",
        f"Bản sửa của “{previous.document_name}” (vòng {contract_review_store.review_round(previous)}) · "
        "đối chiếu với kết quả vòng trước",
    )
    return previous


def _previous_round(db: Session, previous: ContractReview) -> dict[str, Any]:
    """What the next round is checked against: the earlier review and its decisions."""
    return {
        "review_id": str(previous.id),
        "round": contract_review_store.review_round(previous),
        "result": previous.result or {},
        "decisions": contract_review_store.serialize_decisions(db, previous),
    }


def _review_uploaded_contract(
    db: Session,
    current_user: User,
    filename: str,
    data: bytes,
    represented_party: str,
    progress: ProgressReporter = _no_progress,
) -> Dict[str, Any]:
    """Review an uploaded contract, reporting each stage to ``progress`` as it goes.

    Shared by the plain endpoint and the streamed one, so both store, escalate and keep
    the original file identically.
    """
    # The form first, from the bytes as uploaded: parsing to text is one-way, and the form
    # is what lets an accepted revision be written back without breaking the layout.
    form: dict[str, Any] | None = None
    if document_format(filename):
        progress("FORM", "running", None)
        try:
            form = capture_form(filename, data)
            progress("FORM", "done", _form_summary(form))
        except ContractEditError as exc:
            progress("FORM", "failed", f"{exc} Vẫn rà soát được, nhưng không sửa trực tiếp được file.")
    else:
        progress("FORM", "skipped", "Chỉ ghi nhớ bố cục cho file DOCX/PDF")
    progress("PARSE", "running", None)
    text, _ = _parse_legal_bytes(filename, data)
    progress("PARSE", "done", f"{len(text):,} ký tự".replace(",", "."))
    stored = contract_review_store.find_contract_review(
        db,
        user=current_user,
        contract_text=text,
        represented_party=represented_party,
        review_version=ASSESSED_REVIEW_VERSION,
    )
    if stored is not None:
        # The same file for the same side: its review, redline decisions and escalation
        # are already there, and the model is not asked a second time.
        progress("CACHE", "done", "Hợp đồng này đã được rà soát trước đó cho cùng bên; dùng lại kết quả đã lưu")
        contract_review_store.attach_original(stored, filename=filename, data=data, form=form)
        db.commit()
        return _review_response(db, stored, current_user)
    previous = _previous_review(db, current_user, filename, data, represented_party, progress)
    on_usage = _llm_usage_recorder(db, current_user, "LEGAL")
    # Translated before anything reads it: the rule floor under the model's reading is a
    # set of Vietnamese rules.
    progress("LANGUAGE", "running", None)
    try:
        review_text = text_for_review(text, on_usage=on_usage)
    except ContractNotReviewable as exc:
        raise HTTPException(status_code=422, detail=exc.reply) from exc
    progress(
        "LANGUAGE", "done",
        f"Đã dịch từ {review_text.source_language} sang tiếng Việt để rà soát" if review_text.translated else "Tiếng Việt",
    )
    if review_text.translated:
        # The previous round's clauses are those of its translation; a new translation of
        # the revised file will not line up with them clause by clause.
        previous = None
    try:
        result = mark_translated(
            review_with_assessment(
                review_text.text,
                filename,
                represented_party,
                on_usage=on_usage,
                on_progress=progress,
                previous=_previous_round(db, previous) if previous is not None else None,
            ),
            review_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Looked up by the type the review settled on -- the model's, when it read the
    # contract -- rather than by a keyword guess made before it.
    progress("REFERENCES", "running", None)
    known = {source.get("id") for source in result["reference_sources"]}
    result["reference_sources"] = [
        *result["reference_sources"],
        *(
            source
            for source in _retrieve_contract_review_references(
                db, current_user, result["contract_type_label"]
            )
            if source.get("id") not in known
        ),
    ]
    progress("REFERENCES", "done", f"{len(result['reference_sources']) - len(known)} tài liệu nội bộ liên quan")
    progress("SAVE", "running", None)
    # Saved here rather than by a follow-up call from the browser: a second call
    # would have to accept the review body back from the client, which would let
    # anyone post a fabricated result and have it become the audit record.
    #
    # Nothing is sent for approval here, whatever the risk: the reviewer decides on the
    # findings, writes the accepted ones into the file and sends it themselves
    # (submit-approval). An automatic card went out before any of that, for a draft
    # the reviewer had not yet worked through.
    review = contract_review_store.save_contract_review(
        db,
        user=current_user,
        result=result,
        contract_text=text,
        source="UPLOAD",
    )
    contract_review_store.attach_original(review, filename=filename, data=data, form=form)
    carried = 0
    if previous is not None and result.get("parent_review_id") == str(previous.id):
        carried = contract_review_store.link_to_previous(db, review, previous)
    db.commit()
    progress(
        "SAVE", "done",
        "Đã lưu bản rà soát"
        + (f" · giữ {carried} quyết định từ vòng trước" if carried else "")
        + (" · rủi ro cao, hãy gửi phê duyệt khi đã xử lý xong" if result.get("requires_legal_approval") else ""),
    )
    return _review_response(db, review, current_user)


def _form_summary(form: dict[str, Any] | None) -> str:
    if not form:
        return ""
    if form.get("format") == "docx":
        counts = form.get("counts") or {}
        return (
            f"Word · {counts.get('paragraphs', 0)} đoạn, {counts.get('tables', 0)} bảng, "
            f"{len(form.get('sections') or [])} section"
        )
    pages = form.get("pages") or []
    return f"PDF · {len(pages)} trang, {sum(len(page.get('lines') or []) for page in pages)} dòng chữ"


def _review_response(db: Session, review: ContractReview, current_user: User) -> Dict[str, Any]:
    """A review as the client renders it, fresh or reopened: one shape for both."""
    decisions = contract_review_store.serialize_decisions(db, review)
    return {
        **(review.result or {}),
        **_contract_review_item(review, decisions, current_user, db=db),
        "decisions": decisions,
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post(
    "/legal/review-document/stream",
    summary="Review a legal document, streaming each pipeline stage over SSE",
    dependencies=[_legal_tool_required("audit_contract_risk")],
)
async def stream_review_legal_document(
    file: UploadFile = File(...),
    represented_party: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """The same review as /legal/review-document, told stage by stage.

    A review can run for a minute or more (translation, then three model stages); one
    spinner for all of it left the user unable to tell a slow stage from a stuck one.
    Each stage is sent as ``progress`` (stage, status, detail, elapsed_ms), the review
    itself as ``complete``, a refusal or failure as ``error``.
    """
    if represented_party.upper() not in VALID_PERSPECTIVES:
        raise HTTPException(status_code=422, detail="represented_party phải là PARTY_A, PARTY_B hoặc NEUTRAL")
    filename, data = await _legal_file_bytes(file)
    events: "queue.Queue[tuple[str, dict[str, Any]] | None]" = queue.Queue()
    started = time.monotonic()

    def progress(stage: str, status: str, detail: str | None = None) -> None:
        events.put(("progress", {
            "stage": stage, "status": status, "detail": detail,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }))

    def work() -> None:
        try:
            events.put(("complete", _review_uploaded_contract(
                db, current_user, filename, data, represented_party, progress
            )))
        except HTTPException as exc:
            db.rollback()
            events.put(("error", {"message": str(exc.detail), "status_code": exc.status_code}))
        except Exception:
            db.rollback()
            logger.exception("Streamed contract review failed")
            events.put(("error", {"message": "Không thể hoàn tất rà soát. Vui lòng thử lại."}))
        finally:
            # The request's own cleanup may already have run -- it does not wait for a
            # streamed body -- so the reads made after the last commit would leave a
            # transaction open, holding its locks, until the connection died.
            db.close()
            events.put(None)

    def stream():
        # Started here, not in the endpoint: by the time the body is iterated the request's
        # session is no longer shared with anything else, so the worker has it alone.
        yield _sse("progress", {
            "stage": "UPLOAD", "status": "done",
            "detail": f"{filename} · {max(1, len(data) // 1024)} KB", "elapsed_ms": 0,
        })
        threading.Thread(target=work, name="contract-review", daemon=True).start()
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/legal/contract-reviews", summary="List saved contract reviews")
def list_contract_reviews_endpoint(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    risk_level: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[dict[str, Any]]:
    reviews = contract_review_store.list_contract_reviews(
        db, user=current_user, limit=limit, offset=offset, risk_level=risk_level
    )
    _audit_review_access(
        db, current_user, request, action="contract_review.list",
        output={"review_ids": [str(review.id) for review in reviews]},
    )
    return [
        _contract_review_item(
            review, contract_review_store.serialize_decisions(db, review), current_user, db=db
        )
        for review in reviews
    ]


@router.get(
    "/legal/contract-reviews/{review_id}",
    summary="Reopen a saved contract review with its decisions",
)
def get_contract_review_endpoint(
    review_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    review = _readable_review(
        db, current_user, request, review_id, action="contract_review.view"
    )
    # The stored analyzer output is spread at the top level so the client renders a
    # reopened review through exactly the same shape as a fresh one.
    return _review_response(db, review, current_user)


@router.delete(
    "/legal/contract-reviews/{review_id}",
    summary="Delete a saved contract review, its decisions and its files",
)
def delete_contract_review_endpoint(
    review_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Delete a review its creator no longer needs.

    Refused while an approval is waiting on it: the approver would be left deciding on a
    contract that is gone. A decided approval stays in the approval history with its
    summary; only the review behind it goes.
    """
    try:
        review = _contract_review_for_user(db, current_user, review_id)
    except HTTPException:
        _audit_review_access(db, current_user, request, action="contract_review.delete", review_id=review_id, status="DENIED")
        raise
    if not contract_review_store.can_delete_contract_review(current_user, review):
        _audit_review_access(
            db, current_user, request, action="contract_review.delete", review_id=str(review.id), status="DENIED",
        )
        raise HTTPException(status_code=403, detail="Chỉ người tạo bản rà soát mới xoá được bản này.")
    approval = _review_approval(db, review)
    if approval is not None and approval.status == "WAITING":
        raise HTTPException(
            status_code=409,
            detail="Bản rà soát đang chờ phê duyệt nên chưa xoá được; hãy chờ phiếu được duyệt hoặc từ chối.",
        )
    deleted_id, document_name = str(review.id), review.document_name
    keys = contract_review_store.delete_contract_review(db, review)
    db.commit()
    _audit_review_access(
        db, current_user, request, action="contract_review.delete", review_id=deleted_id,
        output={"document_name": document_name, "files": len(keys)},
    )
    # After the commit: a failed delete must not leave the review pointing at missing files.
    for key in keys:
        try:
            delete_legal_artifact(key)
        except (OSError, ValueError):
            logger.warning("Could not remove a file of deleted contract review %s", deleted_id, exc_info=True)
    return {"status": "DELETED", "review_id": deleted_id}


@router.put(
    "/legal/contract-reviews/{review_id}/decisions/{finding_key}",
    summary="Record the reviewer decision for one finding",
)
def put_contract_review_decision(
    review_id: str,
    finding_key: str,
    req: ContractReviewDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    review = _contract_review_for_user(db, current_user, review_id)
    decision = (req.decision or "").upper()
    if decision not in contract_review_store.VALID_DECISIONS:
        raise HTTPException(
            status_code=422, detail="decision must be ACCEPTED, REJECTED or EDITED"
        )
    if finding_key not in contract_review_store.finding_keys(review):
        raise HTTPException(
            status_code=422, detail="This finding does not belong to the review"
        )
    if decision == "EDITED" and not (req.revised_text or "").strip():
        raise HTTPException(
            status_code=422, detail="revised_text is required when the decision is EDITED"
        )
    contract_review_store.upsert_decision(
        db,
        review=review,
        user=current_user,
        finding_key=finding_key,
        decision=decision,
        revised_text=req.revised_text,
        comment=req.comment,
    )
    decisions = contract_review_store.serialize_decisions(db, review)
    payload = {
        **_contract_review_item(review, decisions, current_user, db=db),
        "decisions": decisions,
    }
    # log_audit_action commits internally, so it has to be the last write: anything
    # after it would land in a separate transaction.
    log_audit_action(
        db,
        current_user.tenant_id,
        "LEGAL",
        "record_contract_review_decision",
        {"review_id": str(review.id), "finding_key": finding_key, "decision": decision},
        {"decided_count": len(decisions), "status": review.status},
    )
    return payload


@router.delete(
    "/legal/contract-reviews/{review_id}/decisions/{finding_key}",
    summary="Clear the reviewer decision for one finding",
)
def delete_contract_review_decision(
    review_id: str,
    finding_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    review = _contract_review_for_user(db, current_user, review_id)
    if not contract_review_store.clear_decision(db, review=review, finding_key=finding_key):
        raise HTTPException(status_code=404, detail="No decision recorded for this finding")
    decisions = contract_review_store.serialize_decisions(db, review)
    payload = {
        **_contract_review_item(review, decisions, current_user, db=db),
        "decisions": decisions,
    }
    log_audit_action(
        db,
        current_user.tenant_id,
        "LEGAL",
        "clear_contract_review_decision",
        {"review_id": str(review.id), "finding_key": finding_key},
        {"decided_count": len(decisions), "status": review.status},
    )
    return payload


@router.post(
    "/legal/compare-documents",
    summary="Compare two contract versions",
    dependencies=[_legal_tool_required("compare_contract_versions")],
)
async def compare_legal_documents(
    old_file: UploadFile = File(...),
    new_file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    old_name, old_text, _ = await _read_legal_file(old_file)
    new_name, new_text, _ = await _read_legal_file(new_file)
    result = compare_contract_texts(old_text, new_text)
    return {"old_document": old_name, "new_document": new_name, **result}


@router.post(
    "/legal/privacy-check",
    summary="Detect personal and restricted data",
    dependencies=[_legal_tool_required("check_sensitive_data")],
)
async def privacy_check_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    filename, text, headers = await _read_legal_file(file)
    result = {"document_name": filename, **detect_sensitive_data(text, headers)}
    result["workflow_id"] = _create_legal_approval(
        db, current_user, result, "LEGAL_PRIVACY_APPROVAL"
    )
    result["approval_created"] = result["workflow_id"] is not None
    return result


@router.post(
    "/legal/license-check",
    summary="Inspect a software dependency manifest",
    dependencies=[_legal_tool_required("check_software_licenses")],
)
async def license_check_manifest(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    filename, text, _ = await _read_legal_file(file)
    result = check_software_licenses(filename, text)
    result["workflow_id"] = _create_legal_approval(
        db, current_user, result, "LEGAL_LICENSE_APPROVAL"
    )
    result["approval_created"] = result["workflow_id"] is not None
    return result


@router.post("/legal/generate-document", summary="Generate an editable legal document draft")
def generate_legal_document_endpoint(
    req: LegalDocumentGenerateRequest,
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(
        status_code=410,
        detail="Direct generation is disabled; submit through /legal/document-drafts",
    )


@router.post(
    "/legal/document-drafts",
    status_code=201,
    summary="Generate and submit a legal document for approval",
    # The agent's grant and the person's: this page used to check only the first, so
    # anyone could draft here what the Legal agent refused them in chat.
    dependencies=[
        _legal_tool_required("generate_legal_document"),
        Depends(PermissionRequired(LEGAL_DOCUMENT_GENERATE_PERMISSION)),
    ],
)
def submit_legal_document_draft(
    req: LegalDocumentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        approval = submit_legal_document(
            db,
            current_user,
            document_type=req.document_type,
            output_format=req.output_format,
            fields=req.fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(approval)
    return _legal_draft_item(approval, current_user)


@router.get("/legal/document-drafts", summary="List generated legal documents")
def list_legal_document_drafts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[dict[str, Any]]:
    query = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == current_user.tenant_id,
        WorkflowApproval.action_type == "LEGAL_DOCUMENT_APPROVAL",
    )
    if not can_approve_legal_documents(current_user):
        query = query.filter(AgentWorkflow.initiator_id == current_user.id)
    approvals = query.order_by(AgentWorkflow.created_at.desc()).all()
    return [_legal_draft_item(approval, current_user) for approval in approvals]


@router.get(
    "/legal/document-drafts/{artifact_id}/preview",
    summary="Preview a generated legal document",
)
def preview_legal_document_draft(
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    approval = _legal_draft_approval(db, current_user, artifact_id)
    if (
        not can_approve_legal_documents(current_user)
        and approval.workflow.initiator_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="You cannot preview this legal document")
    payload = approval.payload or {}
    try:
        content = read_legal_artifact(str(payload["draft_storage_key"]))
        text = extract_file_text(str(payload["filename"]), content)
    except (KeyError, OSError, ValueError, DocumentParseError) as exc:
        raise HTTPException(status_code=404, detail="Legal document artifact is unavailable") from exc
    return {
        "artifact_id": artifact_id,
        "filename": payload.get("filename"),
        "document_type_label": payload.get("document_type_label"),
        "status": approval.status,
        "requester_name": payload.get("requester_name"),
        "content": text,
    }


@router.get(
    "/legal/document-drafts/{artifact_id}/download",
    summary="Download a generated legal document with approval enforcement",
)
def download_legal_document_draft(
    artifact_id: str,
    variant: str = "approved",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    approval = _legal_draft_approval(db, current_user, artifact_id)
    is_reviewer = can_approve_legal_documents(current_user)
    is_creator = approval.workflow.initiator_id == current_user.id
    if variant not in {"draft", "approved"}:
        raise HTTPException(status_code=422, detail="Unsupported document variant")
    if variant == "draft" and not is_reviewer:
        raise HTTPException(status_code=403, detail="Only executive approvers can download the draft")
    if variant == "approved" and approval.status != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="The approved artifact is unavailable until approval is completed",
        )
    if variant == "approved" and not is_reviewer and not is_creator:
        raise HTTPException(
            status_code=403,
            detail="Only the creator or an executive approver can download this document",
        )
    payload = approval.payload or {}
    storage_field = "draft_storage_key" if variant == "draft" else "approved_storage_key"
    try:
        content = read_legal_artifact(str(payload[storage_field]))
    except (KeyError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Legal document artifact is unavailable") from exc
    filename = Path(str(payload.get("filename") or "legal-document")).name
    media_type = str(payload.get("media_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    disposition_name = filename if variant == "draft" else f"approved-{filename}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{disposition_name}\"; "
                f"filename*=UTF-8''{quote(disposition_name)}"
            )
        },
    )


@router.get(
    "/legal/contract-reviews/{review_id}/redline",
    summary="Download the redline report built from the accepted revisions",
)
def download_contract_redline(
    review_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    review = _readable_review(db, current_user, request, review_id, action="contract_review.redline_download")
    decisions = contract_review_store.serialize_decisions(db, review)
    if not any(item["decision"] in {"ACCEPTED", "EDITED"} for item in decisions):
        raise HTTPException(
            status_code=409,
            detail="Chưa có đề xuất nào được chấp nhận để tạo redline.",
        )

    content: bytes | None = None
    if review.redline_storage_key:
        try:
            content = read_legal_artifact(str(review.redline_storage_key))
        except (OSError, ValueError):
            # The cached file is gone; fall through and rebuild it.
            content = None
    if content is None:
        content, filename = build_redline_docx(
            review.result or {},
            decisions,
            document_name=review.document_name,
            generated_by=current_user.full_name,
            review_id=str(review.id),
        )
        artifact_id = review.redline_artifact_id or uuid.uuid4().hex
        review.redline_artifact_id = artifact_id
        review.redline_filename = filename
        review.redline_storage_key = save_legal_artifact(
            tenant_id=review.tenant_id,
            artifact_id=artifact_id,
            variant="redline",
            filename=filename,
            content=content,
        )
        db.commit()

    filename = Path(str(review.redline_filename or "redline.docx")).name
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


# --- LEGAL: the contract file itself ---


class ContractApprovalSubmitRequest(BaseModel):
    note: Optional[str] = None


def _file_response(content: bytes, filename: str, fmt: str | None) -> Response:
    name = Path(filename).name
    return Response(
        content=content,
        media_type=MEDIA_TYPES.get(fmt or "", "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename=\"{quote(name)}\"; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        },
    )


def _write_revised_document(db: Session, review: ContractReview, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the accepted revisions into the original file and keep the result on the review.

    Raises HTTPException with a reason the reviewer can act on when nothing can be written.
    """
    if not review.original_storage_key:
        raise HTTPException(
            status_code=409,
            detail=(
                "Bản rà soát này không có file gốc để sửa (hợp đồng dán vào chat, hoặc tải lên "
                "trước khi có tính năng này). Hãy tải lại file DOCX/PDF để rà soát."
            ),
        )
    try:
        original = contract_review_store.read_original(review)
    except (OSError, ValueError, EncryptionKeyMissing) as exc:
        raise HTTPException(status_code=409, detail="Không đọc được file gốc của hợp đồng trên máy chủ.") from exc
    try:
        outcome = apply_revisions(
            data=original,
            filename=str(review.original_filename or review.document_name),
            form=review.form_snapshot,
            review_result=review.result or {},
            decisions=decisions,
        )
    except ContractEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = outcome["report"]
    if outcome["content"] is None:
        reasons = "; ".join(f"{item['clause']}: {item['reason']}" for item in report["skipped"][:4])
        raise HTTPException(status_code=422, detail=f"Không ghi được đề xuất nào vào file. {reasons}")
    contract_review_store.save_revised(review, content=outcome["content"], report=report)
    _sync_waiting_approval(db, review, decisions)
    return report


def _approval_attachment(review: ContractReview, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """What an approval carries of the review: decision counts and the revised file."""
    state = contract_review_store.document_state(review, decisions)
    counts = {"ACCEPTED": 0, "EDITED": 0, "REJECTED": 0}
    for item in decisions:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    report = review.revision_report or {}
    return {
        "review_url": f"/agents/LEGAL?review={review.id}",
        "decision_summary": {**counts, "PENDING": max(0, review.total_findings - len(decisions))},
        "revised_document": (
            {
                "filename": review.revised_filename,
                "format": review.original_format,
                "url": state["revised_url"],
                "revised_at": state["revised_at"],
                "applied": len(report.get("applied") or []),
                "skipped": len(report.get("skipped") or []),
            }
            if state["revised_ready"] and not state["revised_stale"]
            else None
        ),
        "original_document": (
            {"filename": review.original_filename, "format": review.original_format, "url": state["original_url"]}
            if state["original_available"] else None
        ),
    }


def _sync_waiting_approval(db: Session, review: ContractReview, decisions: list[dict[str, Any]]) -> None:
    """Keep an approval still waiting on this review pointed at the latest revised file."""
    approval = _review_approval(db, review)
    if approval is None or approval.status != "WAITING":
        return
    approval.payload = {**(approval.payload or {}), **_approval_attachment(review, decisions)}
    flag_modified(approval, "payload")


@router.post(
    "/legal/contract-reviews/{review_id}/revised-document",
    summary="Write the accepted revisions into the uploaded contract file",
    dependencies=[_legal_tool_required("audit_contract_risk")],
)
def apply_contract_revisions_endpoint(
    review_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    review = _contract_review_for_user(db, current_user, review_id)
    decisions = contract_review_store.serialize_decisions(db, review)
    report = _write_revised_document(db, review, decisions)
    response = contract_review_store.document_state(review, decisions)
    log_audit_action(
        db,
        current_user.tenant_id,
        "LEGAL",
        "apply_contract_revisions",
        {"review_id": str(review.id), "format": report["format"]},
        {
            "applied": [item["finding_key"] for item in report["applied"]],
            "skipped": [item["finding_key"] for item in report["skipped"]],
        },
    )
    return response


@router.get(
    "/legal/contract-reviews/{review_id}/revised-document",
    summary="Download the contract file with the accepted revisions written in",
)
def download_revised_contract(
    review_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    review = _readable_review(
        db, current_user, request, review_id, action="contract_review.revised_download"
    )
    if not review.revised_storage_key:
        raise HTTPException(status_code=404, detail="Chưa có bản hợp đồng đã sửa.")
    try:
        content = contract_review_store.read_revised(review)
    except (OSError, ValueError, EncryptionKeyMissing) as exc:
        raise HTTPException(status_code=404, detail="File hợp đồng đã sửa không còn trên máy chủ.") from exc
    # Stamped on the way out, so a file written before the mark existed carries it too:
    # uploaded again, it is reviewed as the next round of this review.
    content = stamp_review_marker(review.original_format, content, contract_review_store.review_marker(review))
    return _file_response(content, str(review.revised_filename), review.original_format)


@router.get(
    "/legal/contract-reviews/{review_id}/original-document",
    summary="Download the contract file as it was uploaded",
)
def download_original_contract(
    review_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    review = _readable_review(
        db, current_user, request, review_id, action="contract_review.original_download"
    )
    if not review.original_storage_key:
        raise HTTPException(status_code=404, detail="Bản rà soát này không lưu file gốc.")
    try:
        content = contract_review_store.read_original(review)
    except (OSError, ValueError, EncryptionKeyMissing) as exc:
        raise HTTPException(status_code=404, detail="File gốc không còn trên máy chủ.") from exc
    return _file_response(content, str(review.original_filename), review.original_format)


@router.post(
    "/legal/contract-reviews/{review_id}/submit-approval",
    summary="Send a contract review, with its revised file, to the approval center",
    dependencies=[_legal_tool_required("audit_contract_risk")],
)
def submit_contract_review_for_approval(
    review_id: str,
    req: ContractApprovalSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Open an approval for the review, or refresh the one already waiting on it.

    The only way a contract review reaches the approval center: nothing is sent when a
    review finishes, whatever its risk, and any risk level may be sent. The revised file
    goes with it -- written
    now when the decisions have moved on since it was last built; a file that cannot be
    written does not stop the submission, it is reported back instead.
    """
    review = _contract_review_for_user(db, current_user, review_id)
    decisions = contract_review_store.serialize_decisions(db, review)
    warnings: list[str] = []
    state = contract_review_store.document_state(review, decisions)
    has_accepted = any(item["decision"] in {"ACCEPTED", "EDITED"} for item in decisions)
    if state["editable"] and has_accepted and (not state["revised_ready"] or state["revised_stale"]):
        try:
            _write_revised_document(db, review, decisions)
        except HTTPException as exc:
            warnings.append(f"Chưa ghi được bản sửa vào file: {exc.detail}")
    extra = {
        **_approval_attachment(review, decisions),
        "submitted_manually": True,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "note": (req.note or "").strip()[:2000] or None,
    }
    approval = _review_approval(db, review)
    if approval is not None and approval.status == "WAITING":
        approval.payload = {**(approval.payload or {}), **extra}
        flag_modified(approval, "payload")
        outcome = "UPDATED"
    else:
        result = review.result or {}
        workflow_id = _create_legal_approval(
            db,
            current_user,
            {**result, "document_name": review.document_name},
            contract_review_id=str(review.id),
            force=True,
            extra_payload=extra,
            reason="Người rà soát gửi kết quả rà soát hợp đồng và bản đã sửa để phê duyệt.",
        )
        review.workflow_id = uuid.UUID(str(workflow_id))
        review.result = {**result, "workflow_id": str(workflow_id), "approval_created": True}
        outcome = "CREATED"
    db.flush()
    approval_state = _approval_state(db, _review_approval(db, review))
    if approval_state and approval_state["warning"]:
        warnings.append(approval_state["warning"])
    response = {
        "status": outcome,
        "warnings": warnings,
        "approval": approval_state,
        "document": contract_review_store.document_state(review, decisions),
    }
    log_audit_action(
        db,
        current_user.tenant_id,
        "LEGAL",
        "submit_contract_review_approval",
        {"review_id": str(review.id)},
        {"status": outcome, "approval_id": (response["approval"] or {}).get("approval_id")},
    )
    return response


# --- IT ---
@router.post("/it/tickets", summary="Create Jira ticket for technical issue")
def create_jira_ticket_endpoint(
    req: CreateJiraTicketRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    # The IT agent is under development: this issued a Jira key no Jira ever saw.
    refuse_under_development("IT")
    return handle_it_request(db, current_user, f"{req.summary} - {req.description or ''}")


# --- SALES ---
@router.post("/sales/quotation", summary="Generate sales quotation PDF payload")
def generate_quotation_endpoint(
    req: SalesQuotationRequest,
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    # The Sales agent is under development: every request was quoted the same camera.
    refuse_under_development("SALES")
    return handle_sales_request(req.item_query, customer_name=req.customer_name)


@router.get("/sales/download-quote/{file_id}", response_class=PlainTextResponse, summary="Download sales PDF quotation file")
def download_quote(file_id: str):
    refuse_under_development("SALES")
    return f"SIMULATED PDF QUOTATION FILE FOR {file_id}\nOfficial AI Workforce Quotation Document."
