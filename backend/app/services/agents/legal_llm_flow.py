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
from typing import Any, Literal

from app.core.config import settings
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

# Whether pasted text is a whole contract or a piece of one. Only a whole contract can be
# faulted for the clauses it lacks; an excerpt simply was not sent with them.
DOCUMENT_SCOPES = frozenset({"FULL", "EXCERPT"})
_SCOPED_INTENTS = frozenset({"REVIEW", "UNSURE"})

PARTY_LABELS = frozenset({"PARTY_A", "PARTY_B", "NEUTRAL"})
_PERSPECTIVE_DECISIONS = frozenset({"ANSWER", "CANCEL", "OTHER"})

# A routing decision does not need the whole document: the opening of a contract is what
# identifies it as one. Sending 40 pages of pasted text to the router would bill the
# tenant for tokens that cannot change the label.
CLASSIFIER_MESSAGE_LIMIT = 4000
# The request, though, is often written after the paste ("...điều trên có ổn không?"),
# so a long turn keeps its closing lines as well as its opening.
_CLASSIFIER_TAIL_CHARS = 1000
_ELISION = "\n[...]\n"


def bounded_message(message: str) -> str:
    """The part of a turn the router sees: all of it, or its opening and its close."""
    if len(message) <= CLASSIFIER_MESSAGE_LIMIT:
        return message
    head = CLASSIFIER_MESSAGE_LIMIT - _CLASSIFIER_TAIL_CHARS - len(_ELISION)
    return message[:head] + _ELISION + message[-_CLASSIFIER_TAIL_CHARS:]


@dataclass(frozen=True)
class LegalIntentClassification:
    intent: str
    source: Source
    # FULL or EXCERPT when the model judged the pasted text, otherwise None and the caller
    # falls back to its own reading. Never set for a turn that carries no document.
    document_scope: str | None = None


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
                        "message": bounded_message(message),
                    },
                    ensure_ascii=False,
                ),
            },
        ], timeout=settings.AI_SERVICE_ROUTER_TIMEOUT_SECONDS)
        if not isinstance(result, dict) or is_echo_provider(result):
            # A body that is valid JSON but not an object is as unusable as no reply;
            # reading fields off it would raise past the handler below.
            return fallback
        report_usage(on_usage, result)
        payload = extract_json_object(str(result.get("content") or ""))
        intent = str((payload or {}).get("intent") or "").strip().upper()
        if intent not in LEGAL_INTENT_LABELS:
            return fallback
        if intent in _PENDING_ONLY_LABELS and pending_state == "NONE":
            # Nothing is pending, so there is nothing to confirm or call off: the model
            # misread the turn, and a misread reply is one we do not believe. Forcing
            # QUESTION instead would send a pasted contract the scorer had recognised
            # straight into retrieval.
            return fallback
        if intent == "CANCEL":
            # The prompt no longer offers CANCEL, but a tenant's override may. Calling
            # off the review the assistant offered is declining it; callers see one label.
            intent = "DECLINE_REVIEW"
        scope = str((payload or {}).get("scope") or "").strip().upper()
        return LegalIntentClassification(
            intent,
            "llm",
            scope if intent in _SCOPED_INTENTS and scope in DOCUMENT_SCOPES else None,
        )
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
                    {"message": bounded_message(message)}, ensure_ascii=False
                ),
            },
        ], timeout=settings.AI_SERVICE_ROUTER_TIMEOUT_SECONDS)
        if not isinstance(result, dict) or is_echo_provider(result):
            # A body that is valid JSON but not an object is as unusable as no reply;
            # reading fields off it would raise past the handler below.
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


# Enough of each excerpt to answer from; a whole policy document per excerpt would bill
# the tenant for text the answer cannot use.
EVIDENCE_CHARS_PER_EXCERPT = 2000


@dataclass(frozen=True)
class LegalGroundedAnswer:
    answerable: bool
    answer: str
    # Indexes into the evidence list the caller passed, in the order the model cited them.
    used_evidence: tuple[int, ...]


def _evidence_ref(index: int) -> str:
    return f"S{index + 1}"


def answer_from_legal_evidence(
    question: str,
    evidence: list[dict[str, Any]],
    *,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> LegalGroundedAnswer | None:
    """Answer a Legal question from retrieved excerpts, or report that they do not.

    Retrieval always returns its nearest excerpts, related or not, so it cannot tell "found"
    from "closest". The model makes that call and says so in a field, rather than the
    caller searching its prose for a refusal. None means the model could not be used or
    believed -- including an answer that cites nothing it was given -- and the caller
    keeps its deterministic reply.
    """
    if not evidence:
        return None
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return None

    payload_evidence = [
        {
            "ref": _evidence_ref(index),
            "document": item.get("document_title") or item.get("document_name"),
            "section": item.get("section_title"),
            "version": item.get("version"),
            "effective_date": item.get("effective_date"),
            "expiration_date": item.get("expiration_date"),
            "content": str(item.get("content") or "")[:EVIDENCE_CHARS_PER_EXCERPT],
        }
        for index, item in enumerate(evidence)
    ]
    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "legal_answer")},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": bounded_message(question), "evidence": payload_evidence},
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ])
        if not isinstance(result, dict) or is_echo_provider(result):
            return None
        report_usage(on_usage, result)
        payload = extract_json_object(str(result.get("content") or ""))
        if payload is None or not isinstance(payload.get("answerable"), bool):
            return None
        if not payload["answerable"]:
            return LegalGroundedAnswer(False, "", ())
        answer = str(payload.get("answer") or "").strip()
        refs = {_evidence_ref(index): index for index in range(len(evidence))}
        cited = payload.get("sources")
        used: list[int] = []
        for ref in cited if isinstance(cited, list) else []:
            index = refs.get(str(ref).strip().upper())
            if index is not None and index not in used:
                used.append(index)
        if not answer or not used:
            # An answer that names none of the excerpts it was given is not grounded in
            # them, whatever it says.
            return None
        return LegalGroundedAnswer(True, answer, tuple(used))
    except (AIServiceError, TypeError, ValueError):
        logger.warning(
            "Legal grounded answer failed; using the retrieved excerpts", exc_info=True
        )
    return None
