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
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import ContractReview, ContractReviewDecision, User


# Mirrors specialized.LEGAL_DOCUMENT_APPROVERS. "Owner" is retained alongside
# "CEO" because the q20d5f7b9e43 migration renamed the role and both may exist.
LEGAL_REVIEW_APPROVERS = {"Owner", "Admin", "CEO"}
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
    return user.role in LEGAL_REVIEW_APPROVERS or review.created_by_id == user.id


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
    existing = db.query(ContractReview).filter(
        ContractReview.tenant_id == user.tenant_id,
        ContractReview.idempotency_key == key,
    ).first()
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
    if user.role not in LEGAL_REVIEW_APPROVERS:
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
