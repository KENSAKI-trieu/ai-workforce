"""LLM-first routing for the Legal agent, with the deterministic rules as the floor.

The keyword scorer this fronts could only recognise what somebody had thought to list.
It scored "?" as evidence against a review, so "Rà soát hợp đồng này giúp tôi, có ổn
không?" routed to retrieval; it matched markers as substrings, so "công ty" contained a
yes; and it could not read "đừng" apart from "đúng" once tone marks were stripped.

Nothing here decides anything on its own. Each function returns a label the caller maps
onto a branch that already existed, and every failure -- no AI service, the echo
provider, a malformed reply, an unknown label -- falls back to the caller's deterministic
answer. The model widens what the agent understands; it never widens what it may do.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.services.agents.legal_prompts import resolve_slot
from app.services.agents.llm_json import (
    UsageReporter,
    extract_json_object,
    is_echo_provider,
    report_usage,
)
from app.services.ai_service_client import AIServiceClient, AIServiceError, get_ai_service_client

logger = logging.getLogger(__name__)

Source = Literal["llm", "fallback"]

# Every label maps onto a branch the Legal executor already implements, so the model can
# never name a flow that does not exist.
LEGAL_INTENT_LABELS = frozenset({
    "REVIEW",
    "QUESTION",
    "UNSURE",
    "CONFIRM_REVIEW",
    "DECLINE_REVIEW",
    "CANCEL",
})

# Labels that only mean something while the assistant is waiting on an answer. Accepting
# them with no pending question would let a stray reply start or cancel a review.
_PENDING_ONLY_LABELS = frozenset({"CONFIRM_REVIEW", "DECLINE_REVIEW", "CANCEL"})

PARTY_LABELS = frozenset({"PARTY_A", "PARTY_B", "NEUTRAL"})
_PERSPECTIVE_DECISIONS = frozenset({"ANSWER", "CANCEL", "OTHER"})

# A routing decision does not need the whole document: the opening of a contract is what
# identifies it as one. Sending 40 pages of pasted text to the router would bill the
# tenant for tokens that cannot change the label.
CLASSIFIER_MESSAGE_LIMIT = 4000


@dataclass(frozen=True)
class LegalIntentClassification:
    intent: str
    source: Source


@dataclass(frozen=True)
class LegalPerspective:
    represented_party: str | None
    decision: str
    source: Source


def classify_legal_request(
    message: str,
    *,
    pending_state: str,
    fallback_intent: str,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> LegalIntentClassification:
    """Route one Legal turn: review the text, answer a question, or ask which.

    ``pending_state`` is NONE or AWAITING_INTENT and is passed to the model, which is
    what lets a single prompt also read "yes"/"no" answers to the question the assistant
    asked last turn. ``fallback_intent`` is the deterministic label the caller already
    computed, returned untouched whenever the model cannot be used or believed.
    """
    fallback = LegalIntentClassification(fallback_intent, "fallback")
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return fallback

    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "legal_classifier")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "pending_state": pending_state,
                        "message": message[:CLASSIFIER_MESSAGE_LIMIT],
                    },
                    ensure_ascii=False,
                ),
            },
        ])
        if is_echo_provider(result):
            return fallback
        report_usage(on_usage, result)
        payload = extract_json_object(str(result.get("content") or ""))
        intent = str((payload or {}).get("intent") or "").strip().upper()
        if intent not in LEGAL_INTENT_LABELS:
            return fallback
        if intent in _PENDING_ONLY_LABELS and pending_state == "NONE":
            # Nothing is pending, so there is nothing to confirm or call off. Reading the
            # turn on its own terms is the only safe interpretation left.
            return LegalIntentClassification("QUESTION", "llm")
        return LegalIntentClassification(intent, "llm")
    except (AIServiceError, TypeError, ValueError):
        logger.warning(
            "Legal LLM intent classification failed; using deterministic fallback",
            exc_info=True,
        )
    return fallback


def extract_represented_party(
    message: str,
    *,
    fallback_party: str | None,
    fallback_cancel: bool,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> LegalPerspective:
    """Read which side the user acts for out of their free-text reply.

    The caller has already parsed the reply with the keyword rules and passes that result
    in, so a model that is unavailable, cheap-talking or unparsable costs nothing: the
    keyword answer is returned as-is.
    """
    fallback = LegalPerspective(
        fallback_party,
        "CANCEL" if fallback_cancel else ("ANSWER" if fallback_party else "OTHER"),
        "fallback",
    )
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return fallback

    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "legal_perspective")},
            {
                "role": "user",
                "content": json.dumps(
                    {"message": message[:CLASSIFIER_MESSAGE_LIMIT]}, ensure_ascii=False
                ),
            },
        ])
        if is_echo_provider(result):
            return fallback
        report_usage(on_usage, result)
        payload = extract_json_object(str(result.get("content") or ""))
        if payload is None:
            return fallback
        party_value = payload.get("represented_party")
        party = str(party_value or "").strip().upper()
        decision = str(payload.get("decision") or "").strip().upper()
        if decision not in _PERSPECTIVE_DECISIONS:
            decision = "ANSWER" if party in PARTY_LABELS else "OTHER"
        if decision == "ANSWER" and party not in PARTY_LABELS:
            # It claims to have read an answer but did not name a side; that is no answer.
            decision = "OTHER"
        return LegalPerspective(
            party if party in PARTY_LABELS and decision == "ANSWER" else None,
            decision,
            "llm",
        )
    except (AIServiceError, TypeError, ValueError):
        logger.warning(
            "Legal LLM perspective extraction failed; using deterministic fallback",
            exc_info=True,
        )
    return fallback
