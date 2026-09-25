"""LLM-first routing and grounded answer synthesis for the HR agent."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.agents.hr.prompts import resolve_slot
from app.agents.llm_json import (
    UsageReporter,
    extract_json_object,
    is_echo_provider,
    report_usage,
)
from app.clients.ai_service_client import AIServiceClient, AIServiceError, get_ai_service_client

logger = logging.getLogger(__name__)

HRRequestKind = Literal["QUESTION", "ACTION"]

# Kept as module attributes because this module's readers expect them here; the
# implementations are shared with the Legal router in `llm_json`.
_extract_json_object = extract_json_object
_report_usage = report_usage

ACTION_INTENTS = frozenset({
    "ACTION_EXPORT",
    "ACTION_LEAVE_REQUEST",
    "ACTION_ONBOARDING",
})

# The closed label set the router may choose from. Every label already has a governed
# branch in the HR executor, so the model can never name a business flow the
# deterministic dispatcher does not implement.
HR_INTENT_LABELS = frozenset({
    "ACTION_EXPORT",
    "ACTION_LEAVE_REQUEST",
    "ACTION_ONBOARDING",
    "CONTRACT_EXPIRY",
    "EMPLOYEE_DIRECTORY",
    "EMPLOYEE_LEAVE_STATUS_COUNT",
    "EMPLOYEE_SEARCH",
    "FULL_PROFILE",
    "MANAGER_DIRECTORY",
    "PENDING_APPROVALS",
    "POLICY_QUERY",
    "QUERY_LEAVE_BALANCE",
    "SELF_COMPENSATION",
    "SELF_CONTRACT",
    "SELF_PRIVATE_PROFILE",
    "SELF_PROFILE",
    "UNKNOWN",
})

# Only these intents hand their retrieved evidence to the answer model. Everything left
# out either already produces an exact deterministic answer (leave balance) or carries
# personal data that must not leave the governed path (salary, contact details,
# contracts, another employee's deep profile).
SYNTHESIZABLE_INTENTS = frozenset({
    "CONTRACT_EXPIRY",
    "EMPLOYEE_DIRECTORY",
    "EMPLOYEE_SEARCH",
    "MANAGER_DIRECTORY",
    "POLICY_QUERY",
})


@dataclass(frozen=True)
class HRRequestClassification:
    kind: HRRequestKind
    source: Literal["llm", "fallback"]
    intent: str | None = None


def classify_hr_request(
    raw_message: str,
    *,
    detailed_intent: str,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> HRRequestClassification:
    """Send the unchanged user message to the LLM and return a safe route.

    One call returns both the read/write gate and the detailed business intent. An
    intent outside ``HR_INTENT_LABELS`` is dropped so the caller keeps its
    deterministic keyword label.
    """
    fallback_kind: HRRequestKind = (
        "ACTION" if detailed_intent in ACTION_INTENTS else "QUESTION"
    )
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return HRRequestClassification(fallback_kind, "fallback")

    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "classifier")},
            # Keep this content byte-for-byte equivalent to the incoming chat text.
            {"role": "user", "content": raw_message},
        ])
        # The deterministic local provider only echoes its input and is not a classifier.
        # It also bills nothing, so the meter is only told about the calls above it.
        if str(result.get("provider") or "").lower() == "local":
            return HRRequestClassification(fallback_kind, "fallback")
        _report_usage(on_usage, result)
        payload = _extract_json_object(str(result.get("content") or ""))
        kind = str((payload or {}).get("kind") or "").strip().upper()
        intent = str((payload or {}).get("intent") or "").strip().upper()
        if kind in {"QUESTION", "ACTION"}:
            return HRRequestClassification(
                kind,  # type: ignore[arg-type]
                "llm",
                intent if intent in HR_INTENT_LABELS else None,
            )
    except (AIServiceError, TypeError, ValueError):
        logger.warning("HR LLM intent classification failed; using safe fallback", exc_info=True)
    return HRRequestClassification(fallback_kind, "fallback")


LEAVE_DRAFT_TURNS = frozenset({"CONTINUE", "CANCEL", "UNRELATED"})


@dataclass(frozen=True)
class LeaveDraftTurn:
    turn: str
    source: Literal["llm", "fallback"]


def classify_leave_draft_turn(
    raw_message: str,
    *,
    draft: dict[str, Any],
    fallback_turn: str,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> LeaveDraftTurn:
    """Decide what this turn does to an open leave draft: continue, cancel or neither.

    This used to be two keyword rules. They read any date-like token as an answer to the
    slot the assistant last asked for, and recognised a cancellation only from four fixed
    phrases -- so "thôi tôi không xin nữa đâu" kept the draft open while "hôm nay công ty
    có họp không?" was filed as the reason for leave. ``fallback_turn`` is what those
    rules concluded, and it is returned untouched whenever the model cannot be used.
    """
    fallback = LeaveDraftTurn(fallback_turn, "fallback")
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return fallback

    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "leave_draft")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "draft": {
                            key: draft.get(key)
                            for key in ("start_date", "end_date", "reason")
                        },
                        "missing_fields": draft.get("missing_fields") or [],
                        "message": raw_message,
                    },
                    ensure_ascii=False,
                ),
            },
        ])
        if is_echo_provider(result):
            return fallback
        report_usage(on_usage, result)
        payload = extract_json_object(str(result.get("content") or ""))
        turn = str((payload or {}).get("turn") or "").strip().upper()
        if turn in LEAVE_DRAFT_TURNS:
            return LeaveDraftTurn(turn, "llm")
    except (AIServiceError, TypeError, ValueError):
        logger.warning(
            "HR LLM leave-draft turn classification failed; using keyword fallback",
            exc_info=True,
        )
    return fallback


def _validated_iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    try:
        parsed = date.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == cleaned else None


def extract_leave_request_slots(
    raw_message: str,
    *,
    existing: dict[str, Any] | None,
    reference_date: date,
    timezone_name: str,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> dict[str, str | None]:
    """Extract only leave fields stated in this turn, with relative dates resolved by LLM."""
    empty_result: dict[str, str | None] = {
        "start_date": None,
        "end_date": None,
        "reason": None,
    }
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return empty_result

    draft = {
        key: (existing or {}).get(key)
        for key in ("start_date", "end_date", "reason")
    }
    missing_fields = [key for key, value in draft.items() if not value]
    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "leave_slot")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "reference_date": reference_date.isoformat(),
                        "timezone": timezone_name,
                        "existing_draft": draft,
                        "missing_fields": missing_fields,
                        "message": raw_message,
                    },
                    ensure_ascii=False,
                ),
            },
        ])
        # The local provider echoes input and cannot perform semantic extraction.
        if str(result.get("provider") or "").lower() == "local":
            return empty_result
        _report_usage(on_usage, result)
        payload = _extract_json_object(str(result.get("content") or ""))
        if payload is None:
            return empty_result

        reason_value = payload.get("reason")
        reason = reason_value.strip(" .") if isinstance(reason_value, str) else ""
        if len(reason) > 2000:
            reason = ""
        return {
            "start_date": _validated_iso_date(payload.get("start_date")),
            "end_date": _validated_iso_date(payload.get("end_date")),
            "reason": reason or None,
        }
    except (AIServiceError, TypeError, ValueError):
        logger.warning("HR LLM leave-slot extraction failed; using parser fallback", exc_info=True)
        return empty_result


_NUMBER_PATTERN = re.compile(r"\d[\d.,]*")


def _headline_card_numbers(hr_card: Any) -> set[str]:
    """Collect the figures a card asserts in its own right.

    Only scalars the card states directly, one level deep, count as headline figures:
    counts and totals the reply is built around. Digits buried in list items, ids and
    ISO dates are evidence rather than assertions, and requiring them all would reject
    every summary the answer model legitimately writes.
    """
    if not isinstance(hr_card, dict):
        return set()
    found: set[str] = set()
    for value in hr_card.values():
        candidates = value.values() if isinstance(value, dict) else (value,)
        for candidate in candidates:
            if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
                continue
            found.add(f"{candidate:g}")
    return found


_DECLINED_ANSWER_MARKERS = (
    "khong tim thay",
    "khong co thong tin",
    "khong du thong tin",
    "chua tim thay",
    "chua co thong tin",
    "not enough information",
    "no information",
    "could not find",
    "insufficient",
)


def _declines_to_answer(answer: str) -> bool:
    """Return whether the model reported that the evidence did not answer the question."""
    folded = unicodedata.normalize("NFD", answer.lower().replace("đ", "d"))
    stripped = "".join(
        char for char in folded if unicodedata.category(char) != "Mn"
    )
    return any(marker in stripped for marker in _DECLINED_ANSWER_MARKERS)


def _preserves_card_numbers(answer: str, hr_card: Any) -> bool:
    """Return whether every headline figure survived the rewrite.

    The answer model is told to preserve numeric values, but nothing enforced it. HR
    figures are the one thing that must be exact, so a rewrite that drops or alters one
    is discarded in favour of the retrieved answer.
    """
    headline = _headline_card_numbers(hr_card)
    if not headline:
        return True
    written = {match.rstrip(".,") for match in _NUMBER_PATTERN.findall(answer)}
    written |= {value.replace(".", "").replace(",", "") for value in written}
    return headline <= written


def _grounded_evidence(response: dict[str, Any]) -> dict[str, Any]:
    citations = []
    for item in (response.get("citations") or [])[:5]:
        citations.append({
            key: item.get(key)
            for key in (
                "id",
                "document_id",
                "document_title",
                "section_title",
                "page",
                "content",
                "citation_tag",
                "effective_date",
                "expiration_date",
            )
            if item.get(key) is not None
        })
    read_results = []
    for item in response.get("tools_executed") or []:
        read_results.append({
            key: item.get(key)
            for key in ("tool_name", "result", "result_count")
            if item.get(key) is not None
        })
    return {
        "retrieved_answer": response.get("reply") or "",
        "citations": citations,
        "read_results": read_results,
        "hr_card": response.get("hr_card"),
    }


def generate_grounded_hr_answer(
    raw_question: str,
    response: dict[str, Any],
    *,
    client: AIServiceClient | None = None,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Use the retrieved, ACL-filtered evidence to synthesize the final HR answer."""
    fallback_answer = str(response.get("reply") or "")
    ai_client = client or get_ai_service_client()
    if not ai_client.enabled:
        return response

    evidence = _grounded_evidence(response)
    # Do not ask a model to improvise when the retrieval layer produced no evidence.
    if not evidence["citations"] and not evidence["read_results"] and not evidence["hr_card"]:
        return response

    try:
        result = ai_client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "answer")},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": raw_question, "governed_evidence": evidence},
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ])
        if str(result.get("provider") or "").lower() == "local":
            return response
        _report_usage(on_usage, result)
        answer = str(result.get("content") or "").strip()
        if not answer:
            return response
        if not _preserves_card_numbers(answer, response.get("hr_card")):
            logger.warning(
                "HR grounded answer dropped a headline figure; using retrieved answer"
            )
            return response

        citation_tags = [
            str(item.get("citation_tag"))
            for item in evidence["citations"]
            if item.get("citation_tag")
        ]
        # An answer that reports finding nothing must not be given sources it did not use.
        if (
            citation_tags
            and not _declines_to_answer(answer)
            and not any(tag in answer for tag in citation_tags)
        ):
            answer = f"{answer}\n\nNguồn: {' '.join(citation_tags[:3])}"
        return {**response, "reply": answer}
    except (AIServiceError, TypeError, ValueError):
        logger.warning("HR grounded answer generation failed; using retrieved answer", exc_info=True)
        return {**response, "reply": fallback_answer}
