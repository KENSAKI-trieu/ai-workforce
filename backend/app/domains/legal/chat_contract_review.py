"""Run one contract review for a chat user and record it the way every chat review is.

Shared by the Legal chat executor and the `audit_contract_risk` gateway tool, so a
contract reviewed through LangGraph is stored and linked exactly like one reviewed in the
deterministic chat: the saved review carries the same redline link. Neither sends it for
approval; the reviewer does that from the saved review.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import ContractReview, User
from app.domains.legal import contract_review_store
from app.agents.llm_json import UsageReporter
from app.domains.legal.contract_review.analyzer import ASSESSED_REVIEW_VERSION
from app.domains.legal.contract_review.clause_parser import split_contract_clauses
from app.domains.legal.contract_review.llm_assessment import DEFAULT_BUDGET_SECONDS, review_with_assessment
from app.domains.legal.contract_translation import (
    TRANSLATION_NOTICE,
    mark_translated,
    text_for_review,
)

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
    document_scope: str | None,
    on_usage: UsageReporter | None = None,
) -> tuple[dict[str, Any], ContractReview]:
    """Review the text and save the review.

    The review is only flushed; the caller's commit persists it. A contract that is not in
    Vietnamese is reviewed from its translation; when it cannot be translated this raises
    ContractNotReviewable before anything is stored. With no ``document_scope``, the scope
    is read from the structure of the text the analyzer sees.
    """
    # The same contract sent again, for the same side, reopens the review already made of
    # it -- without translating it or asking the model a second time.
    review = contract_review_store.find_contract_review(
        db,
        user=user,
        contract_text=contract_text,
        represented_party=represented_party,
        review_version=ASSESSED_REVIEW_VERSION,
    )
    if review is not None:
        result = dict(review.result or {})
    else:
        started = time.monotonic()
        review_text = text_for_review(contract_text, on_usage=on_usage)
        result = mark_translated(
            review_with_assessment(
                review_text.text,
                CHAT_DOCUMENT_NAME,
                represented_party,
                document_scope=document_scope or document_scope_from_structure(review_text.text),
                on_usage=on_usage,
                # One budget for translating and reading: the gateway tool that runs this
                # is cut off at 90s.
                budget_seconds=DEFAULT_BUDGET_SECONDS - (time.monotonic() - started),
            ),
            review_text,
        )
        # Stored under the text the user sent, so sending the same contract again finds
        # this review however the translation came out the second time.
        review = contract_review_store.save_contract_review(
            db,
            user=user,
            result=result,
            contract_text=contract_text,
            source="CHAT",
        )
    result["review_id"] = str(review.id)
    result["redline_url"] = f"/api/v1/legal/contract-reviews/{review.id}/redline"
    # Nothing is sent for approval here, as on the Legal page: the reviewer opens the saved
    # review, decides on the findings and sends it themselves.
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
    if result.get("translated_for_review"):
        reply += f"\n\n{TRANSLATION_NOTICE}"
    return reply
