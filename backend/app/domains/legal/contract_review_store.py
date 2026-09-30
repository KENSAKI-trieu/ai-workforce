"""Persistence for Legal Agent contract reviews and their redline decisions.

The analyzer output is stored verbatim so a review can be reopened exactly as it
was produced. Decisions are recorded against `finding_key` -- the content-derived
id from `contract_review.analyzer` -- never against the positional `finding-N`,
which is reassigned by the sort on every run.

Write helpers here flush rather than commit, following `create_notification`: the
caller owns the transaction. That matters most in the chat path, where
`log_audit_action` commits internally and must run last.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.encryption import seal_bytes, unseal_bytes
from app.domains.legal.contract_document import (
    decisions_fingerprint,
    document_format,
    revised_filename,
)
from app.domains.legal.legal_draft_storage import read_legal_artifact, save_legal_artifact
from app.domains.platform.position_service import has_permission
from app.models.models import ContractReview, ContractReviewDecision, User


# Ticked in org-structure as "Xem mọi bản rà soát hợp đồng". It replaced a check on the
# Owner/Admin/CEO role strings, which no box on a position could grant or take away.
VIEW_ALL_REVIEWS_PERMISSION = "legal.review.view_all"
VALID_DECISIONS = {"ACCEPTED", "REJECTED", "EDITED"}


def content_hash(contract_text: str) -> str:
    """Hash the contract with whitespace normalized, so reformatting is not a new document."""
    normalized = re.sub(r"\s+", " ", contract_text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _idempotency_key(
    *, created_by_id: uuid.UUID, text_hash: str, represented_party: str, review_version: str
) -> str:
    """Identify a repeat of the same review by the same person.

    `review_version` is part of the key on purpose: when the analyzer changes, the
    finding keys it produces may change too, so the next run must start a fresh
    review rather than resurfacing decisions taken against the old rule pack.
    """
    payload = f"{created_by_id}|{text_hash}|{represented_party}|{review_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def can_access_contract_review(user: User, review: ContractReview) -> bool:
    """Who may read a review and record decisions on it.

    A decision is a negotiation position rather than an approval, so the person who
    ran the review is allowed to mark it up; `decided_by_id` carries the
    accountability. The binding gate stays WorkflowApproval, which is unaffected.
    """
    if review.tenant_id != user.tenant_id:
        return False
    return review.created_by_id == user.id or has_permission(None, user, VIEW_ALL_REVIEWS_PERMISSION)


def find_contract_review(
    db: Session,
    *,
    user: User,
    contract_text: str,
    represented_party: str,
    review_version: str,
) -> ContractReview | None:
    """The review this person already has of this text, for this side and analyzer version.

    Callers look before a model-assisted review runs: its wording differs from run to
    run, so a fresh result shown over a stored row would carry finding keys the stored
    row does not have, and every decision taken on the card would be refused.
    """
    key = _idempotency_key(
        created_by_id=user.id,
        text_hash=content_hash(contract_text),
        represented_party=represented_party.upper(),
        review_version=review_version,
    )
    return db.query(ContractReview).filter(
        ContractReview.tenant_id == user.tenant_id,
        ContractReview.idempotency_key == key,
    ).first()


def save_contract_review(
    db: Session,
    *,
    user: User,
    result: dict[str, Any],
    contract_text: str,
    source: str,
    workflow_id: uuid.UUID | None = None,
) -> ContractReview:
    """Store one analyzer run, or return the existing row for a repeat of it.

    Flushes without committing. Returning the existing row on a repeat is what lets
    someone re-upload the same contract and find their redline still in place.
    """
    text_hash = content_hash(contract_text)
    review_version = str(result.get("review_version") or "2.0")
    represented_party = str(result.get("represented_party") or "NEUTRAL")
    key = _idempotency_key(
        created_by_id=user.id,
        text_hash=text_hash,
        represented_party=represented_party,
        review_version=review_version,
    )
    existing = find_contract_review(
        db,
        user=user,
        contract_text=contract_text,
        represented_party=represented_party,
        review_version=review_version,
    )
    if existing:
        # Late escalation: a review first run below the approval threshold can be
        # linked to a workflow on a later identical run.
        if workflow_id and not existing.workflow_id:
            existing.workflow_id = workflow_id
            db.flush()
        return existing

    review = ContractReview(
        tenant_id=user.tenant_id,
        created_by_id=user.id,
        workflow_id=workflow_id,
        source=source,
        document_name=str(result.get("document_name") or "Contract"),
        content_hash=text_hash,
        idempotency_key=key,
        represented_party=represented_party,
        contract_type=str(result.get("contract_type") or "SERVICE_AGREEMENT"),
        review_version=review_version,
        risk_score=int(result.get("risk_score") or 0),
        risk_level=str(result.get("risk_level") or "LOW"),
        total_findings=len(result.get("findings") or []),
        contract_text=contract_text,
        result=result,
        status="OPEN",
    )
    db.add(review)
    db.flush()
    return review


def get_contract_review(
    db: Session, *, user: User, review_id: str
) -> ContractReview | None:
    try:
        identifier = uuid.UUID(str(review_id))
    except (TypeError, ValueError):
        return None
    review = db.query(ContractReview).filter(ContractReview.id == identifier).first()
    if not review or not can_access_contract_review(user, review):
        return None
    return review


def list_contract_reviews(
    db: Session,
    *,
    user: User,
    limit: int = 50,
    offset: int = 0,
    risk_level: str | None = None,
) -> list[ContractReview]:
    query = db.query(ContractReview).filter(ContractReview.tenant_id == user.tenant_id)
    if not has_permission(db, user, VIEW_ALL_REVIEWS_PERMISSION):
        query = query.filter(ContractReview.created_by_id == user.id)
    if risk_level:
        query = query.filter(ContractReview.risk_level == risk_level.upper())
    return (
        query.order_by(ContractReview.created_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )


def finding_keys(review: ContractReview) -> set[str]:
    return {
        str(finding.get("finding_key"))
        for finding in (review.result or {}).get("findings", [])
        if finding.get("finding_key")
    }


def _finding_ref(review: ContractReview, finding_key: str) -> str | None:
    for finding in (review.result or {}).get("findings", []):
        if finding.get("finding_key") == finding_key:
            return str(finding.get("id") or "") or None
    return None


def _invalidate_redline(review: ContractReview) -> None:
    """Drop the cached redline file: it no longer reflects the decisions."""
    review.redline_artifact_id = None
    review.redline_storage_key = None
    review.redline_filename = None


def _refresh_status(db: Session, review: ContractReview) -> None:
    decided = db.query(ContractReviewDecision).filter(
        ContractReviewDecision.review_id == review.id
    ).count()
    review.status = "RESOLVED" if decided >= review.total_findings > 0 else "OPEN"


def upsert_decision(
    db: Session,
    *,
    review: ContractReview,
    user: User,
    finding_key: str,
    decision: str,
    revised_text: str | None = None,
    comment: str | None = None,
) -> ContractReviewDecision:
    """Record or replace the decision on one finding. Flushes without committing."""
    row = db.query(ContractReviewDecision).filter(
        ContractReviewDecision.review_id == review.id,
        ContractReviewDecision.finding_key == finding_key,
    ).first()
    if row is None:
        row = ContractReviewDecision(
            review_id=review.id,
            finding_key=finding_key,
            finding_ref=_finding_ref(review, finding_key),
        )
        db.add(row)
    row.decision = decision
    row.revised_text = revised_text if decision == "EDITED" else None
    row.comment = comment
    row.decided_by_id = user.id
    _invalidate_redline(review)
    db.flush()
    _refresh_status(db, review)
    db.flush()
    return row


def clear_decision(db: Session, *, review: ContractReview, finding_key: str) -> bool:
    row = db.query(ContractReviewDecision).filter(
        ContractReviewDecision.review_id == review.id,
        ContractReviewDecision.finding_key == finding_key,
    ).first()
    if row is None:
        return False
    db.delete(row)
    _invalidate_redline(review)
    db.flush()
    _refresh_status(db, review)
    db.flush()
    return True


def attach_original(
    review: ContractReview,
    *,
    filename: str,
    data: bytes,
    form: dict[str, Any] | None,
) -> None:
    """Keep the uploaded file (sealed) and its form with the review. Flushes nothing.

    The first upload wins: a later identical upload returns this same review, and
    swapping the file under revisions already written from it would orphan them.
    """
    if review.original_storage_key:
        return
    fmt = document_format(filename)
    if fmt is None:
        return
    review.original_storage_key = save_legal_artifact(
        tenant_id=review.tenant_id,
        artifact_id=f"review-{review.id}",
        variant="original",
        filename=filename,
        content=seal_bytes(data),
    )
    review.original_filename = Path(filename).name
    review.original_format = fmt
    review.form_snapshot = form


def read_original(review: ContractReview) -> bytes:
    return unseal_bytes(read_legal_artifact(str(review.original_storage_key)))


def save_revised(review: ContractReview, *, content: bytes, report: dict[str, Any]) -> None:
    filename = revised_filename(str(review.original_filename or review.document_name))
    review.revised_storage_key = save_legal_artifact(
        tenant_id=review.tenant_id,
        artifact_id=f"review-{review.id}",
        variant="revised",
        filename=filename,
        content=seal_bytes(content),
    )
    review.revised_filename = filename
    review.revision_report = report
    review.revised_at = datetime.now(timezone.utc)


def read_revised(review: ContractReview) -> bytes:
    return unseal_bytes(read_legal_artifact(str(review.revised_storage_key)))


def document_state(review: ContractReview, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """What the client needs to offer editing, viewing and downloading the contract file."""
    report = review.revision_report or {}
    ready = bool(review.revised_storage_key)
    return {
        "original_available": bool(review.original_storage_key),
        "original_filename": review.original_filename,
        "original_format": review.original_format,
        "editable": bool(review.original_storage_key) and not (review.result or {}).get("translated_for_review"),
        "revised_ready": ready,
        # Built from other decisions than the ones on the review now: re-apply to refresh.
        "revised_stale": ready and report.get("decisions_fingerprint") != decisions_fingerprint(decisions),
        "revised_filename": review.revised_filename,
        "revised_at": review.revised_at.isoformat() if review.revised_at else None,
        "revision_report": report or None,
        "revised_url": f"/api/v1/legal/contract-reviews/{review.id}/revised-document",
        "original_url": f"/api/v1/legal/contract-reviews/{review.id}/original-document",
    }


def serialize_decisions(db: Session, review: ContractReview) -> list[dict[str, Any]]:
    rows = db.query(ContractReviewDecision).filter(
        ContractReviewDecision.review_id == review.id
    ).order_by(ContractReviewDecision.created_at.asc()).all()
    return [
        {
            "finding_key": row.finding_key,
            "finding_ref": row.finding_ref,
            "decision": row.decision,
            "revised_text": row.revised_text,
            "comment": row.comment,
            "decided_by_id": str(row.decided_by_id),
            "decided_by_name": row.decided_by.full_name if row.decided_by else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]
