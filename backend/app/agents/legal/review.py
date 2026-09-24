"""Contract review drafts carried across turns, and the routing log."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session
from app.models.models import ChatConversation, ChatMessage, User
from app.domains.legal import contract_review_store
from app.agents.legal.llm_flow import LegalIntentClassification

logger = logging.getLogger(__name__)


def _log_legal_routing(
    *,
    pending_state: str,
    classification: LegalIntentClassification,
    keyword_intent: str,
) -> None:
    """Record who routed this turn, and to what.

    A tenant prompt override that stops producing JSON does not fail anything: every turn
    quietly falls back to the keyword scorer, and without the source on record that
    regression is invisible. This goes to the log rather than into tools_executed, which
    is persisted as the tools a turn ran and switches the chat stream to TOOL_CALLING.
    """
    logger.info(
        "Legal turn routed: pending_state=%s source=%s label=%s keyword=%s",
        pending_state,
        classification.source,
        classification.intent,
        keyword_intent,
    )


def _log_legal_overrule(label: str, intent: str, reason: str) -> None:
    logger.info("Legal routing overruled: label=%s intent=%s reason=%s", label, intent, reason)


def _legal_document_scope(
    classification: LegalIntentClassification, detection_signals: dict[str, Any]
) -> str:
    """FULL or EXCERPT: the router's reading of the text, else the clause parser's.

    The parser's answer is only the fallback for when no model read the turn: a document
    it can split into numbered clauses is taken as whole, anything else as a piece.
    """
    if classification.document_scope:
        return classification.document_scope
    return "FULL" if detection_signals.get("clause_structure") else "EXCERPT"


def _legal_review_card(
    *,
    status: str,
    contract_fingerprint: str,
    contract_char_count: int,
    excerpt: str = "",
    represented_party: str | None = None,
    document_scope: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "CONTRACT_REVIEW_DRAFT",
        "status": status,
        "represented_party": represented_party,
        "contract_fingerprint": contract_fingerprint,
        "contract_char_count": contract_char_count,
        "contract_excerpt": excerpt[:200],
        # Decided when the text arrived and carried to the review, which can be two
        # turns later and no longer has the router's reading of it.
        "document_scope": document_scope,
    }


def _contract_fingerprint(text: str) -> str:
    return contract_review_store.content_hash(text)[:16]


def _load_legal_review_draft(
    db: Session,
    user: User,
    thread_id: str | None,
) -> tuple[dict[str, Any], str] | None:
    """Find an open perspective question and the contract text it was asked about.

    The contract is not copied into the card: the user's own message is committed
    before this runs, so the text is already durable in the thread. The card carries
    a fingerprint of it, and the text is recovered by matching that hash rather than
    by message order -- so the agent can only ever review the exact text the question
    was asked about, and never a later message that happens to sit in the right slot.
    (Message timestamps are not a reliable tiebreaker: rows written in one
    transaction share a `now()`, which is the norm under the test fixtures.)
    """
    if not thread_id:
        return None
    conversation = db.query(ChatConversation).filter(
        ChatConversation.tenant_id == user.tenant_id,
        ChatConversation.user_id == user.id,
        ChatConversation.thread_id == thread_id,
    ).first()
    if not conversation:
        return None
    messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
    ).order_by(ChatMessage.created_at.desc()).limit(20).all()

    draft: dict[str, Any] | None = None
    for chat_message in messages:
        if chat_message.sender != "ASSISTANT":
            continue
        for attachment in chat_message.attachments or []:
            payload = attachment.get("payload") or {}
            if payload.get("type") != "CONTRACT_REVIEW_DRAFT":
                # A finished review closes the question that led to it. Its card is the
                # analyzer output, which carries no "type", so scanning past it would
                # resurrect the COLLECTING card from before the review and re-ask a
                # question the user already answered -- leaving the thread unable to
                # answer anything else, and re-running the audit on the old contract
                # as soon as a later message happened to name a party.
                if attachment.get("type") == "LEGAL_RISK_CARD":
                    return None
                continue
            if payload.get("status") not in {"COLLECTING", "AWAITING_INTENT"}:
                # The newest card is already resolved or cancelled; nothing pending.
                return None
            draft = payload
            break
        if draft:
            break
    if not draft:
        return None

    fingerprint = draft.get("contract_fingerprint")
    for chat_message in messages:
        if chat_message.sender != "USER":
            continue
        content = chat_message.content or ""
        if _contract_fingerprint(content) == fingerprint:
            return draft, content
    return None
