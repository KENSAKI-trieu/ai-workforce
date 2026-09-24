"""Leave-request drafts: dates, slots, continuation and cancellation."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session
from app.models.models import ChatConversation, ChatMessage, Tenant, User
from app.agents.hr.llm_flow import UsageReporter, extract_leave_request_slots
from app.agents.hr.intent import _is_informational_message
from app.agents.text import _normalize_intent_text


_LEAVE_DATE_PATTERN = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?)\b"
)


def _parse_leave_date(value: str) -> date | None:
    cleaned = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", cleaned):
            year, month, day = (int(part) for part in cleaned.split("-"))
        else:
            parts = [int(part) for part in re.split(r"[/-]", cleaned)]
            if len(parts) == 2:
                day, month = parts
                year = date.today().year
            else:
                day, month, year = parts
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _load_leave_draft(
    db: Session,
    user: User,
    thread_id: str | None,
) -> dict[str, Any] | None:
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
        ChatMessage.sender == "ASSISTANT",
    ).order_by(ChatMessage.created_at.desc()).limit(20).all()
    for chat_message in messages:
        for attachment in chat_message.attachments or []:
            attachment_type = str(attachment.get("type", ""))
            payload = attachment.get("payload") or {}
            if (
                attachment_type == "APPROVAL_CARD"
                and payload.get("action_type") in {"XIN NGHỈ PHÉP", "LEAVE_REQUEST"}
            ):
                return None
            if (
                attachment_type == "HR_CARD"
                and payload.get("type") == "LEAVE_REQUEST_DRAFT"
            ):
                return payload if payload.get("status") == "COLLECTING" else None
    return None


def _extract_leave_slots(
    message: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slots = {
        "start_date": (existing or {}).get("start_date"),
        "end_date": (existing or {}).get("end_date"),
        "reason": (existing or {}).get("reason"),
    }
    normalized = _normalize_intent_text(message)
    parsed_dates = [
        parsed
        for raw in _LEAVE_DATE_PATTERN.findall(message)
        if (parsed := _parse_leave_date(raw)) is not None
    ]
    if len(parsed_dates) >= 2:
        slots["start_date"] = parsed_dates[0].isoformat()
        slots["end_date"] = parsed_dates[1].isoformat()
    elif len(parsed_dates) == 1:
        parsed_value = parsed_dates[0].isoformat()
        if any(marker in normalized for marker in ("ket thuc", "den ngay", "toi ngay")):
            slots["end_date"] = parsed_value
        elif any(marker in normalized for marker in ("bat dau", "tu ngay")):
            slots["start_date"] = parsed_value
        elif not slots["start_date"]:
            slots["start_date"] = parsed_value
        elif not slots["end_date"]:
            slots["end_date"] = parsed_value
        elif (existing or {}).get("validation_error"):
            # Both dates are already filled, so neither branch above can take this one.
            # The previous turn rejected the pair and asked for the end date again, so a
            # bare date now is that correction. Without this the deterministic parser
            # drops it and the draft loops forever whenever the LLM extractor is off.
            slots["end_date"] = parsed_value

    reason_match = re.search(
        r"(?:vì|lý\s*do(?:\s+là)?|ly\s*do(?:\s+la)?)\s*[:\-]?\s*(.+)$",
        message,
        re.IGNORECASE,
    )
    if reason_match:
        reason = reason_match.group(1).strip(" .")
        if reason:
            slots["reason"] = reason
    elif existing and not slots["reason"] and not parsed_dates:
        # In an active slot-filling turn, a short plain answer can be the reason
        # even when the user chooses to provide that field before the dates.
        previous_missing = existing.get("missing_fields") or []
        if "reason" in previous_missing and not _is_informational_message(message):
            reason = message.strip(" .")
            if reason:
                slots["reason"] = reason
    return slots


def _extract_leave_slots_with_llm(
    message: str,
    existing: dict[str, Any] | None,
    *,
    reference_date: date,
    timezone_name: str,
    on_usage: UsageReporter | None = None,
    prompts: Mapping[str, str] | None = None,
) -> dict[str, Any]:

    """Merge semantic LLM extraction over the safe deterministic parser fallback."""
    slots = _extract_leave_slots(message, existing)
    extracted = extract_leave_request_slots(
        message,
        existing=existing,
        reference_date=reference_date,
        timezone_name=timezone_name,
        on_usage=on_usage,
        prompts=prompts,
    )
    for field in ("start_date", "end_date", "reason"):
        if extracted.get(field):
            slots[field] = extracted[field]
    return slots


def _leave_date_context(db: Session, user: User) -> tuple[date, str]:
    # Streaming responses begin after the request transaction commits, so the
    # User relationship may already be detached. Read the tenant timezone
    # explicitly through the active stream session instead of lazy-loading it.
    timezone_name = str(
        db.query(Tenant.timezone).filter(Tenant.id == user.tenant_id).scalar()
        or "Asia/Ho_Chi_Minh"
    )
    try:
        local_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        timezone_name = "Asia/Ho_Chi_Minh"
        local_timezone = ZoneInfo(timezone_name)
    return datetime.now(local_timezone).date(), timezone_name


def _leave_missing_fields(slots: dict[str, Any]) -> list[str]:
    return [
        field
        for field in ("start_date", "end_date", "reason")
        if not slots.get(field)
    ]


def _is_leave_draft_continuation(message: str, draft: dict[str, Any]) -> bool:
    # A question keeps its own route even mid-draft. A policy question that happens to
    # mention a date is not an answer to the slot the assistant last asked for, so this
    # guard runs before the date and weekday markers rather than after them.
    if _is_informational_message(message):
        return False
    normalized = _normalize_intent_text(message)
    if _LEAVE_DATE_PATTERN.search(message):
        return True
    if any(marker in normalized for marker in (
        "hom nay",
        "ngay mai",
        "ngay kia",
        "ngay mot",
        "tuan sau",
        "thang sau",
        "thu hai",
        "thu ba",
        "thu tu",
        "thu nam",
        "thu sau",
        "thu bay",
        "chu nhat",
    )):
        return True
    if re.search(r"(?:vì|lý\s*do|ly\s*do)\s*[:\-]?", message, re.IGNORECASE):
        return True
    # The informational guard above already ran, so a plain reply while the draft is
    # still missing its reason is that reason.
    return "reason" in (draft.get("missing_fields") or [])


def _leave_draft_card(
    slots: dict[str, Any],
    missing_fields: list[str],
    *,
    validation_error: str | None = None,
    status: str = "COLLECTING",
) -> dict[str, Any]:
    return {
        "type": "LEAVE_REQUEST_DRAFT",
        "status": status,
        "start_date": slots.get("start_date"),
        "end_date": slots.get("end_date"),
        "reason": slots.get("reason"),
        "missing_fields": missing_fields,
        "validation_error": validation_error,
    }


def _leave_follow_up_reply(slots: dict[str, Any], missing_fields: list[str]) -> str:
    labels = {
        "start_date": "ngày bắt đầu nghỉ (YYYY-MM-DD, DD/MM/YYYY hoặc DD/MM)",
        "end_date": "ngày kết thúc nghỉ (YYYY-MM-DD, DD/MM/YYYY hoặc DD/MM)",
        "reason": "lý do nghỉ",
    }
    missing_text = ", ".join(f"**{labels[field]}**" for field in missing_fields)
    known = []
    if slots.get("start_date"):
        known.append(f"Bắt đầu: **{slots['start_date']}**")
    if slots.get("end_date"):
        known.append(f"Kết thúc: **{slots['end_date']}**")
    if slots.get("reason"):
        known.append(f"Lý do: **{slots['reason']}**")
    known_text = "\n".join(f"- {item}" for item in known)
    prefix = f"Tôi đã ghi nhận:\n{known_text}\n\n" if known else ""
    return (
        f"{prefix}Để tạo đơn nghỉ phép, bạn vui lòng bổ sung {missing_text}. "
        "Tôi chỉ gửi đơn cho cấp trên sau khi đủ cả 3 thông tin."
    )


LEAVE_CANCEL_MARKERS = (
    "huy don",
    "huy yeu cau",
    "khong xin nua",
    "khong nghi nua",
)


# "hủy" on its own is a cancellation, but as a substring it also matches the name Huy and
# any reason containing it, so only the whole message counts.
LEAVE_CANCEL_MESSAGES = frozenset({"huy", "huy bo", "thoi huy", "huy nhe"})


def _is_leave_cancel_message(normalized_message: str) -> bool:
    return (
        normalized_message.strip(" .!?") in LEAVE_CANCEL_MESSAGES
        or any(marker in normalized_message for marker in LEAVE_CANCEL_MARKERS)
    )
