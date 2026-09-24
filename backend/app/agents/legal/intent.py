"""Keyword reading of a Legal turn: the fallback when the LLM router cannot be used."""

from __future__ import annotations

import re
from typing import Any

from app.domains.legal.contract_review import split_contract_clauses
from app.agents.hr.leave import LEAVE_CANCEL_MESSAGES
from app.agents.text import _normalize_intent_text


# --- Legal: is this turn a contract to review, or a question? -----------------
# The rule this replaces was `len(message) >= 180`, which sent long questions to the
# analyzer and let short pasted clauses fall through to a four-word glossary that
# answered "chưa tìm thấy văn bản phù hợp" -- leaving the user believing their
# contract had been reviewed when it never was.

_LEGAL_CONTRACT_NOUNS = (
    "hop dong", "contract", "agreement", "nda", "msa", "sow",
    "dieu khoan", "clause", "phu luc", "thoa thuan",
)


_LEGAL_REVIEW_VERBS = (
    "ra soat", "review", "kiem tra", "audit", "xem giup", "xem ho", "check", "danh gia",
)


# "phat" alone is also the first half of "phát triển" and "phát hành", so a penalty is
# only recognised in the phrasings contracts actually use for one.
_LEGAL_RISK_TERMS = (
    "rui ro", "phat vi pham", "muc phat", "tien phat", "chiu phat", "penalty",
    "unlimited liability", "khong gioi han", "don phuong cham dut", "boi thuong",
    "trach nhiem",
)


_LEGAL_PENALTY_AMOUNT = re.compile(r"(?<!\w)phat \d")


_LEGAL_BOILERPLATE = (
    "can cu", "cac ben thoa thuan", "co hieu luc tu", "ky ket",
    "dai dien theo phap luat", "whereas", "hereby", "shall",
)


# "should" is left out: English contract prose uses it, and a question asked with it
# almost always carries a "?" anyway.
_LEGAL_QUESTION_OPENERS = (
    "la gi", "the nao", "nhu the nao", "co nen", "khi nao", "tai sao",
    "what", "how", "can i",
)


# "có được" is a question only when "không" closes the sentence ("có được phạt 30% không");
# otherwise it is the ordinary contract grant "Bên B có được quyền ...".
_LEGAL_CO_DUOC_QUESTION = re.compile(
    r"(?<!\w)co duoc(?!\w)[^.?!\n]*(?<!\w)khong\s*(?:[.?!\n]|$)"
)


def _has_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    """Whole-word match: as substrings "nda" is in "standard" and "how" in "show"."""
    return any(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) for phrase in phrases
    )


def _legal_clause_structure(message: str) -> int:
    """How many document-numbered clauses the real parser finds in this text.

    This is what replaces the length heuristic: it reuses the same splitter the
    analyzer uses, so "looks like a contract" means "parses like one" rather than
    "is long".
    """
    try:
        clauses = split_contract_clauses(message)
    except Exception:  # noqa: BLE001 - detection must never break the chat turn
        return 0
    return sum(1 for clause in clauses if str(clause.get("number", "")).strip().isdigit())


def _classify_legal_contract_intent(message: str) -> tuple[str, dict[str, Any]]:
    """Return REVIEW, UNSURE or QUESTION plus the signals behind the decision."""
    normalized = _normalize_intent_text(message)
    numbered_clauses = _legal_clause_structure(message)
    has_structure = numbered_clauses >= 3

    signals: dict[str, Any] = {"numbered_clauses": numbered_clauses}
    score = 0

    names_contract = _has_phrase(normalized, _LEGAL_CONTRACT_NOUNS)
    asks_review = _has_phrase(normalized, _LEGAL_REVIEW_VERBS)
    if names_contract and asks_review:
        score += 3
        signals["explicit_request"] = True
    if has_structure:
        score += 3
        signals["clause_structure"] = True
    if re.search(
        r"^\s*(hop dong|contract|agreement|thoa thuan|phu luc)|ben a\s*:|ben b\s*:|party a\s*:",
        normalized,
    ):
        score += 3
        signals["contract_header"] = True
    boilerplate = sum(
        1 for marker in _LEGAL_BOILERPLATE if _has_phrase(normalized, (marker,))
    )
    if boilerplate >= 2:
        score += 2
        signals["legal_boilerplate"] = boilerplate
    if _has_phrase(normalized, _LEGAL_RISK_TERMS) or _LEGAL_PENALTY_AMOUNT.search(
        normalized
    ):
        score += 1
        signals["risk_terms"] = True

    # A question mark anywhere counts: "... là bao lâu? Tôi muốn nắm rõ." is still a
    # question, and only checking the final character would miss it.
    is_question = (
        "?" in message
        or _has_phrase(normalized, _LEGAL_QUESTION_OPENERS)
        or bool(_LEGAL_CO_DUOC_QUESTION.search(normalized))
    )
    if is_question and not has_structure:
        score -= 3
        signals["interrogative"] = True
    if len(message.strip()) < 60 and not has_structure:
        score -= 2
        signals["too_short"] = True

    signals["score"] = score
    if score >= 4:
        return "REVIEW", signals
    if score >= 2:
        return "UNSURE", signals
    return "QUESTION", signals


# --- Legal: which party is the user acting for? ------------------------------
# Perspective flips severity in the analyzer (an unlimited-liability clause is HIGH
# for whoever carries it and LOW for the other side), so reviewing without asking
# produced a score that did not apply to the person reading it.

_PARTY_A_MARKERS = (
    "ben a", "party a", "nha cung cap", "ben cung cap", "ben ban",
    "vendor", "supplier", "nha thau", "ben thuc hien", "developer",
)


_PARTY_B_MARKERS = (
    "ben b", "party b", "khach hang", "ben mua", "client", "customer",
    "ben thue", "chu dau tu", "ben su dung",
)


_NEUTRAL_MARKERS = (
    "trung lap", "khach quan", "trung tinh", "khong dai dien", "ca hai ben",
    "doc lap", "neutral",
)


_FIRST_PERSON_MARKERS = ("chung toi", "cong ty toi", "ben toi", "toi", "minh", "em")


LEGAL_CANCEL_MARKERS = ("huy ra soat", "thoi khong ra soat", "bo qua", "khong ra soat nua")


_REVIEW_CONFIRM_PHRASES = ("ra soat", "review", "phan tich", "kiem tra", "danh gia")


_REVIEW_CONFIRM_WORDS = frozenset({
    "dung", "co", "ok", "oke", "okay", "yes", "duoc", "dong y", "u", "uh", "um", "vang",
})


# Read off the original text, not the normalized one: stripping tone marks collapses
# "đừng" (don't) and "đúng" (yes) onto the same "dung", so the decline can only be
# recognised before normalization.
_REVIEW_DECLINE_MARKERS = ("đừng", "không", "khỏi", "chỉ hỏi", "don't", "no thanks")


# A reply's first word is its answer: "có, không vấn đề gì" is a yes and "không, chỉ hỏi
# thôi" a no, although both contain "không". Matched with tone marks, for the same reason.
# "thôi" is not a leading no: "thôi được, rà soát đi" agrees.
_REVIEW_LEADING_YES = (
    "ok", "oke", "okay", "có", "ừ", "ừm", "uh", "đúng", "được", "vâng", "yes",
    "đồng ý", "rà soát",
)


_REVIEW_LEADING_NO = ("không", "đừng", "khỏi", "no")


_REVIEW_WHOLE_NO = frozenset({"thôi", "thôi nhé", "thôi ạ"})


def _parse_represented_party(message: str) -> str | None:
    """Read the perspective out of a free-text reply, or None to ask again."""
    normalized = _normalize_intent_text(message)
    if any(marker in normalized for marker in _NEUTRAL_MARKERS):
        return "NEUTRAL"

    # Word boundaries, not substrings: "bên bán" starts with "bên b", so a plain
    # `in` test reads a request from the seller as one from the customer.
    def _earliest(markers: tuple[str, ...]) -> int:
        positions = [
            match.start()
            for marker in markers
            if (match := re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized))
        ]
        return min(positions, default=-1)

    position_a = _earliest(_PARTY_A_MARKERS)
    position_b = _earliest(_PARTY_B_MARKERS)
    if position_a < 0 and position_b < 0:
        # A bare "a" or "b" answers the menu, but only as the whole reply: as a
        # substring it matches almost anything.
        stripped = normalized.strip(" .!?")
        if stripped in {"a", "ben a"}:
            return "PARTY_A"
        if stripped in {"b", "ben b"}:
            return "PARTY_B"
        return None
    if position_a >= 0 and position_b >= 0:
        # Both sides named ("tôi là bên A, đối tác là bên B"): the one the speaker
        # claims is the one that follows the first-person pronoun.
        subject = min(
            (normalized.find(marker) for marker in _FIRST_PERSON_MARKERS if marker in normalized),
            default=-1,
        )
        if subject >= 0:
            after_a = position_a > subject
            after_b = position_b > subject
            if after_a and not after_b:
                return "PARTY_A"
            if after_b and not after_a:
                return "PARTY_B"
            return "PARTY_A" if position_a < position_b else "PARTY_B"
        return "PARTY_A" if position_a < position_b else "PARTY_B"
    return "PARTY_A" if position_a >= 0 else "PARTY_B"


def _is_legal_review_cancel(normalized_message: str) -> bool:
    return (
        normalized_message.strip(" .!?") in LEAVE_CANCEL_MESSAGES
        or any(marker in normalized_message for marker in LEGAL_CANCEL_MARKERS)
    )


def _review_reply_opens_with(lowered: str, words: tuple[str, ...]) -> bool:
    return any(re.match(rf"\s*{re.escape(word)}(?!\w)", lowered) for word in words)


def _is_review_decline(message: str) -> bool:
    """Did the user turn down reviewing the text the agent asked about?"""
    lowered = message.lower().strip()
    if lowered.strip(" .!") in _REVIEW_WHOLE_NO:
        return True
    if _review_reply_opens_with(lowered, _REVIEW_LEADING_NO):
        return True
    if _review_reply_opens_with(lowered, _REVIEW_LEADING_YES):
        return False
    return any(marker in lowered for marker in _REVIEW_DECLINE_MARKERS)


def _is_review_confirmation(message: str) -> bool:
    """Did the user say yes to reviewing the text the agent asked about?

    Takes the raw message because the decline markers only survive with their tone
    marks. The confirm phrases are matched on word boundaries: as bare substrings
    "co" matched "công ty" and "dung" matched "sử dụng", so nearly any reply --
    including "đừng rà soát" -- was read as a yes.
    """
    if _is_review_decline(message):
        return False
    if _review_reply_opens_with(message.lower(), _REVIEW_LEADING_YES):
        return True
    normalized = _normalize_intent_text(message)
    if normalized.strip(" .!?") in _REVIEW_CONFIRM_WORDS:
        return True
    return any(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
        for phrase in _REVIEW_CONFIRM_PHRASES
    )


def _legal_pending_fallback(
    message: str, keyword_intent: str, detection_signals: dict[str, Any]
) -> str:
    """The keyword reading of a reply to "review this, or were you asking?".

    It covers the same five answers the model can give, so a model outage changes how
    well the reply is read but never which branches exist. A reply that carries its own
    contract is a new submission, never a yes: confirming would review the text the
    question was asked about, not the one just sent.
    """
    if detection_signals.get("clause_structure"):
        return "REVIEW"
    if _is_legal_review_cancel(_normalize_intent_text(message)):
        return "DECLINE_REVIEW"
    if _is_review_confirmation(message):
        return "CONFIRM_REVIEW"
    # Long replies are read on their own terms: "không, cho tôi hỏi về thời hạn bảo hành
    # theo luật hiện hành" declines, but it also asks something that deserves an answer.
    if len(message.strip()) < 60 and _is_review_decline(message):
        return "DECLINE_REVIEW"
    return keyword_intent
