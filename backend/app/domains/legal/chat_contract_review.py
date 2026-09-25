"""Run one contract review for a chat user and record it the way every chat review is.

Shared by the Legal chat executor and the `audit_contract_risk` gateway tool, so a
contract reviewed through LangGraph is stored, escalated and linked exactly like one
reviewed in the deterministic chat: a CRITICAL result raises the same approval, and the
saved review carries the same redline link.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import ContractReview, User
from app.domains.legal import contract_review_store
from app.domains.legal.contract_review.clause_parser import split_contract_clauses
from app.domains.legal.legal_approval_service import create_legal_approval
from app.domains.legal.legal_service import audit_contract_text

CHAT_DOCUMENT_NAME = "Nội dung gửi qua chat"


def document_scope_from_structure(text: str) -> str:
    """FULL when the clause parser finds a numbered document, else EXCERPT.

    Only the fallback for when no model judged the text: three or more numbered clauses
    read as a contract in its own right, anything less as a piece of one.
    """
    try:
        clauses = split_contract_clauses(text)
    except Exception:  # noqa: BLE001 - a parsing failure must not block the review
        return "EXCERPT"
    numbered = sum(1 for clause in clauses if str(clause.get("number", "")).strip().isdigit())
    return "FULL" if numbered >= 3 else "EXCERPT"


def run_chat_contract_review(
    db: Session,
    user: User,
    contract_text: str,
    *,
    represented_party: str,
    document_scope: str,
) -> tuple[dict[str, Any], ContractReview]:
    """Review the text, save the review, and open an approval if the result needs one.

    Commits when an approval is raised (create_legal_approval commits); otherwise the
    review is only flushed and the caller's commit persists it.
    """
    result = audit_contract_text(
        contract_text,
        document_name=CHAT_DOCUMENT_NAME,
        represented_party=represented_party,
        document_scope=document_scope,
    )
    review = contract_review_store.save_contract_review(
        db,
        user=user,
        result=result,
        contract_text=contract_text,
        source="CHAT",
    )
    result["review_id"] = str(review.id)
    result["redline_url"] = f"/api/v1/legal/contract-reviews/{review.id}/redline"
    # Chat used to skip this, so a CRITICAL contract pasted here notified nobody while the
    # same file uploaded to the Legal page raised an approval.
    workflow_id = create_legal_approval(db, user, result, contract_review_id=str(review.id))
    if workflow_id:
        review.workflow_id = uuid.UUID(workflow_id)
        result["workflow_id"] = workflow_id
        result["approval_created"] = True
        # The row was flushed before the escalation existed, and a plain JSON column does
        # not track mutations of the dict it was given -- so the blob has to be reassigned,
        # or reopening the review from the saved list would show no sign that it had
        # raised an approval.
        review.result = {
            **(review.result or {}),
            "review_id": str(review.id),
            "workflow_id": workflow_id,
            "approval_created": True,
        }
    return result, review


def review_reply(result: dict[str, Any]) -> str:
    """What the user is told about a finished review; the card carries the findings."""
    reply = (
        f"Tôi đã rà soát nội dung hợp đồng theo góc nhìn "
        f"**{result['represented_party_label']}** và phát hiện "
        f"**{result['total_risks_found']} vấn đề**, với điểm rủi ro "
        f"**{result['risk_score']}/100 ({result['risk_level']})**.\n\n"
        "Mỗi phát hiện bên dưới kèm bằng chứng từ nội dung và hành động đề xuất."
    )
    if result.get("document_scope") == "EXCERPT":
        reply += (
            "\n\nNội dung bạn gửi là **một đoạn trích**, nên tôi chỉ đánh giá những "
            "gì có trong đó và không tính các điều khoản còn thiếu."
        )
    return reply
