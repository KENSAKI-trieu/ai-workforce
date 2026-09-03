"""
Unified Agent Execution Engine for AI Workforce.
Processes incoming chat messages for HR, Knowledge, Legal, IT, Finance, Sales, and CEO agents.
These deterministic tool flows emit audit logs but do not claim provider token usage.
"""

import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Dict, Any, Iterator, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.models import (
    AIAgent,
    AgentWorkflow,
    ChatConversation,
    ChatMessage,
    Tenant,
    User,
    WorkflowApproval,
)
from app.core.hr_capabilities import (
    HR_CONFIGURATION_VERSION,
    HR_CORE_TOOLS,
    HR_RETIRED_TOOLS,
)
from app.services.hr_service import (
    can_manage_hr,
    can_approve_hr_request,
    create_onboarding_case,
    hr_scope_label,
    query_leave_balance,
    request_leave,
)
from app.services.hr_employee_tools import (
    get_employee_sections,
    list_contract_status_summaries,
    query_company_users_sql,
)
from app.services.rag_service import hybrid_search_documents
from app.services.legal_service import audit_contract_text
from app.services.it_service import handle_it_request
from app.services.finance_service import audit_invoice_and_reconcile
from app.services.sales_service import handle_sales_request
from app.services.ceo_service import generate_and_execute_ceo_dag
from app.services.audit_service import log_audit_action
from app.core.config import settings
from app.services.agents.langgraph_engine import LangGraphEngine
from app.services.ai_service_client import AIServiceError
from app.services.agents.hr_llm_flow import (
    ACTION_INTENTS,
    SYNTHESIZABLE_INTENTS,
    classify_hr_request,
    extract_leave_request_slots,
    generate_grounded_hr_answer,
)

logger = logging.getLogger(__name__)

# The approval visibility rule runs in Python, so the queue is walked in bounded batches
# instead of being loaded whole.
PENDING_APPROVAL_BATCH = 100
PENDING_APPROVAL_SCAN_LIMIT = 1000
PENDING_APPROVAL_CARD_SIZE = 20


def _repair_hr_agent_capabilities(agent: AIAgent) -> None:
    """Upgrade legacy HR configuration once; later Admin/Owner choices remain authoritative."""
    if agent.role_code != "HR":
        return
    version = agent.configuration_version or 1
    if version >= HR_CONFIGURATION_VERSION:
        # Every _require_tool call lands here. Re-sorting the three JSON columns when no
        # migration has to run would mark the agent row dirty on every single chat turn.
        return
    denied = set(agent.disallowed_actions or [])
    tools = set(agent.tools_access or [])
    allowed = set(agent.allowed_actions or [])
    if version < 2:
        required = HR_CORE_TOOLS - denied
        tools |= required
        allowed |= required
    if version < 3:
        profile_tools = {
            "get_employee_basic_profile",
            "get_employee_private_profile",
            "get_employee_contract_summary",
            "get_employee_compensation_summary",
            "get_employee_leave_summary",
            "get_employee_full_profile",
        }
        legacy_enabled = "get_employee_profile" in tools and "get_employee_profile" not in denied
        legacy_denied = "get_employee_profile" in denied
        tools.discard("get_employee_profile")
        allowed.discard("get_employee_profile")
        denied.discard("get_employee_profile")
        if legacy_enabled:
            tools |= profile_tools
            allowed |= profile_tools
        elif legacy_denied:
            denied |= profile_tools
    if version < 4:
        # Directory access follows profile access. This asked about
        # `get_employee_basic_profile` until version 7 retired that name; the full-profile
        # grant is set and cleared by exactly the same paths above, so the outcome of this
        # historical step is unchanged for every starting version.
        if "get_employee_full_profile" in tools and "get_employee_full_profile" not in denied:
            tools.add("query_company_users_sql")
            allowed.add("query_company_users_sql")
        else:
            denied.add("query_company_users_sql")
    if version < 5:
        if "export_hr_directory" not in denied:
            tools.add("export_hr_directory")
            allowed.add("export_hr_directory")
    if version < 7:
        # Version 6 stripped the first two retired names; version 7 adds
        # `get_employee_basic_profile` to that set. Agents already stamped 6 still need the
        # sweep, so the whole set is subtracted here rather than per version.
        tools -= HR_RETIRED_TOOLS
        allowed -= HR_RETIRED_TOOLS
        denied -= HR_RETIRED_TOOLS
    agent.configuration_version = HR_CONFIGURATION_VERSION
    agent.tools_access = sorted(tools)
    agent.allowed_actions = sorted(allowed)
    agent.disallowed_actions = sorted(denied)


def _require_tool(agent: AIAgent, tool_name: str) -> None:
    _repair_hr_agent_capabilities(agent)
    tools = set(agent.tools_access or [])
    allowed = set(agent.allowed_actions or [])
    denied = set(agent.disallowed_actions or [])
    if tool_name in denied:
        raise HTTPException(
            status_code=403,
            detail=f"AI Employee is explicitly forbidden from action '{tool_name}'",
        )
    if tool_name not in tools or (allowed and tool_name not in allowed):
        raise HTTPException(
            status_code=403,
            detail=f"AI Employee is not allowed to use tool '{tool_name}'",
        )


def _can_use_tool(agent: AIAgent, tool_name: str) -> bool:
    """Return whether an optional tool is enabled by the agent configuration."""
    _repair_hr_agent_capabilities(agent)
    tools = set(agent.tools_access or [])
    allowed = set(agent.allowed_actions or [])
    denied = set(agent.disallowed_actions or [])
    return (
        tool_name not in denied
        and tool_name in tools
        and (not allowed or tool_name in allowed)
    )


def _employee_profile_payload(
    db: Session,
    actor: User,
    employee: User,
    *,
    requested_sections: list[str],
    purpose: str,
    tool_name: str,
) -> dict[str, Any]:
    access = get_employee_sections(
        db,
        actor=actor,
        employee_id=employee.id,
        requested_sections=requested_sections,
        purpose=purpose,
        tool_name=tool_name,
    )
    basic = access["data"].get("basic", {})
    return {
        "type": "EMPLOYEE_PROFILE",
        "employee": basic,
        "leave_balance": access["data"].get("leave"),
        "private": access["data"].get("private"),
        "contracts": access["data"].get("contract"),
        "compensation": access["data"].get("compensation"),
        "access": {
            "request_id": access["request_id"],
            "purpose": access["purpose"],
            "scope": access["scope"],
            "allowed_sections": access["allowed_sections"],
            "denied_sections": access["denied_sections"],
            "masked_fields": access["masked_fields"],
        },
    }


def _access_result(profile_payload: dict[str, Any]) -> dict[str, Any]:
    """What the policy engine actually released, for the tools_executed trace.

    Every profile branch reports this instead of a per-branch constant string, so the
    trace records which sections were allowed, denied and masked rather than restating
    the tool name.
    """
    access = profile_payload["access"]
    return {
        "allowed_sections": access["allowed_sections"],
        "denied_sections": access["denied_sections"],
        "masked_fields": access["masked_fields"],
    }


def _sql_directory_item(employee: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "type": "EMPLOYEE_PROFILE",
        "employee": employee,
        "leave_balance": None,
        "access": {
            "scope": scope,
            "purpose": "DIRECTORY_LOOKUP",
            "allowed_sections": ["BASIC"],
        },
    }


def _employee_profile_reply(payload: dict[str, Any]) -> str:
    employee = payload["employee"]
    balance = payload.get("leave_balance")
    reply = (
        f"Hồ sơ nhân sự của **{employee['name']}**:\n"
        f"- Email: **{employee['email']}**\n"
        f"- Vai trò: **{employee['role']}**\n"
        f"- Phòng ban: **{employee['department']}**\n"
        f"- Chức danh: **{employee['job_title'] or 'Chưa cập nhật'}**\n"
        f"- Trạng thái: **{employee['employment_status']}**\n"
        f"- Quản lý trực tiếp: **{employee['manager_name'] or 'Chưa thiết lập'}**"
    )
    if balance:
        reply += f"\n- Phép còn lại: **{balance['remaining_days']} ngày**"
    private = payload.get("private") or {}
    if private:
        reply += (
            f"\n- Điện thoại: **{private.get('phone') or 'Chưa cập nhật'}**"
            f"\n- Khu vực: **{private.get('city') or 'Chưa cập nhật'}"
            f"{', ' + private['country'] if private.get('country') else ''}**"
        )
    compensation = payload.get("compensation") or {}
    if compensation:
        salary = compensation.get("monthly_salary")
        salary_text = f"{salary:,.0f}" if isinstance(salary, (int, float)) else "Chưa cập nhật"
        reply += (
            f"\n- Lương tháng: **{salary_text} {compensation.get('salary_currency') or 'VND'}**"
        )
    contracts = payload.get("contracts")
    if contracts is not None:
        reply += f"\n- Hợp đồng được phép xem: **{len(contracts)}**"
    access = payload.get("access") or {}
    if access:
        reply += (
            f"\n\nPhạm vi dữ liệu: **{', '.join(access.get('allowed_sections') or [])}** · "
            f"Mục đích: **{access.get('purpose')}**."
        )
    return reply


def _normalize_intent_text(message: str) -> str:
    normalized = unicodedata.normalize("NFD", message.lower().replace("đ", "d"))
    return " ".join(
        "".join(char for char in normalized if unicodedata.category(char) != "Mn").split()
    )


# Phrases that make a turn a question about how something works, wherever it appears.
# Shared by the intent classifier and by the leave-draft guard so the two cannot drift:
# a phrasing the classifier treats as a policy question must never be recorded as the
# reason on an open leave draft.
_INFORMATIONAL_MARKERS = (
    "bao nhieu",
    "cach ",
    "can gi",
    "can nhung",
    "chinh sach",
    "co can",
    "co duoc",
    "dieu kien",
    "huong dan",
    "la gi",
    "lam sao",
    "may ngay",
    "nhu the nao",
    "quy dinh",
    "quy trinh",
    "ra sao",
    "the nao",
    "thu tuc",
    "yeu cau gi",
)


def _is_informational_message(message: str) -> bool:
    """Return whether the turn reads as a question rather than a slot-filling answer."""
    normalized = _normalize_intent_text(message)
    return "?" in message or any(
        marker in normalized for marker in _INFORMATIONAL_MARKERS
    )


def _classify_hr_intent(message: str) -> str:
    """Classify HR intent from normalized action and entity markers."""
    normalized = _normalize_intent_text(message)
    if any(term in normalized for term in (
        "con bao nhieu ngay phep",
        "so ngay phep",
        "phep con lai",
        "quy phep",
    )):
        return "QUERY_LEAVE_BALANCE"
    if any(term in normalized for term in (
        "xuat file",
        "xuat danh sach",
        "xuat du lieu",
        "trich xuat",
        "export file",
        "export ",
        "tai file",
        "tai xuong",
        "xuat bao cao",
    )):
        return "ACTION_EXPORT"

    if any(marker in normalized for marker in (
        "tao onboarding",
        "khoi tao onboarding",
        "onboard ",
    )):
        return "ACTION_ONBOARDING"

    if any(marker in normalized for marker in (
        "ho so day du",
        "toan bo ho so",
        "full profile",
    )):
        return "FULL_PROFILE"
    if any(marker in normalized for marker in (
        "luong cua toi",
        "muc luong cua toi",
        "thu nhap cua toi",
    )):
        return "SELF_COMPENSATION"
    if any(marker in normalized for marker in (
        "thong tin ca nhan cua toi",
        "ho so rieng tu cua toi",
    )):
        return "SELF_PRIVATE_PROFILE"
    if any(marker in normalized for marker in (
        "ho so cua toi",
        "thong tin nhan su cua toi",
    )):
        return "SELF_PROFILE"
    if any(marker in normalized for marker in (
        "hop dong cua toi",
        "thu viec cua toi",
    )):
        return "SELF_CONTRACT"
    if any(marker in normalized for marker in (
        "hop dong sap het han",
        "hop dong gan het han",
    )):
        return "CONTRACT_EXPIRY"
    if any(marker in normalized for marker in (
        "don cho duyet",
        "yeu cau cho duyet",
        "phe duyet dang cho",
    )):
        return "PENDING_APPROVALS"

    count_markers = (
        "bao nhieu",
        "co may",
        "so luong",
        "tong so",
    )
    directory_markers = count_markers + (
        "danh sach",
        "liet ke",
        "tat ca",
        "tim cac",
        "tim nhung",
        "tim tat ca",
        "xem cac",
    )
    employee_entity = any(marker in normalized for marker in (
        "nhan vien",
        "nhan su",
        "employee",
    ))
    manager_entity = any(marker in normalized for marker in (
        "quan ly",
        "manager",
    ))
    leave_context = "nghi" in normalized and "phep" in normalized

    if employee_entity and leave_context and any(marker in normalized for marker in count_markers):
        return "EMPLOYEE_LEAVE_STATUS_COUNT"
    if manager_entity and (
        any(marker in normalized for marker in directory_markers)
        or normalized in {"tim quan ly", "xem quan ly", "quan ly"}
    ):
        return "MANAGER_DIRECTORY"
    if employee_entity and any(marker in normalized for marker in directory_markers):
        return "EMPLOYEE_DIRECTORY"
    if any(marker in normalized for marker in (
        "tim nhan vien",
        "tim ho so",
        "tra cuu nhan vien",
        "xem ho so cua",
        "ho so nhan vien",
        "ho so cua ",
    )):
        return "EMPLOYEE_SEARCH"

    if leave_context and any(marker in normalized for marker in _INFORMATIONAL_MARKERS):
        return "POLICY_QUERY"

    leave_action_markers = (
        "toi muon xin nghi",
        "toi muon nghi phep",
        "toi xin nghi",
        "cho toi xin nghi",
        "xin nghi phep",
        "xin phep nghi",
        "tao don nghi",
        "gui don nghi",
        "nop don nghi",
        "dang ky nghi",
        "cho toi nghi",
    )
    if any(marker in normalized for marker in leave_action_markers):
        return "ACTION_LEAVE_REQUEST"
    if any(marker in normalized for marker in _INFORMATIONAL_MARKERS):
        return "POLICY_QUERY"
    return "UNKNOWN"


_LEAVE_BALANCE_MARKERS = (
    "con bao nhieu ngay phep",
    "so ngay phep",
    "phep con lai",
    "quy phep",
)

# Words that can surround a leave-balance question without naming anyone: polite lead-ins,
# first-person pronouns, role words that only qualify a following name, and question
# tails. Whatever is left after removing them is a subject.
_SUBJECT_NOISE = frozenset({
    # lead-ins
    "a", "ah", "biet", "cho", "hay", "hoi", "kiem", "lam", "long", "oi", "on",
    "tra", "vui", "xem",
    # first person
    "em", "minh", "t", "toi", "tui",
    # role words that qualify a name rather than being one
    "anh", "ba", "bac", "ban", "chi", "chu", "co", "nhan", "ong", "su", "vien",
    # question tails
    "bao", "con", "gi", "ha", "khong", "la", "lai", "nao", "nhieu", "roi", "the", "va",
})


def _has_named_subject(segment: str) -> bool:
    return any(token not in _SUBJECT_NOISE for token in segment.split())


def _leave_balance_names_another_person(message: str) -> bool:
    """Return whether a leave-balance question is aimed at somebody other than the asker.

    ``query_leave_balance`` only ever reads the requester's own quota, so a question
    about a colleague must be refused rather than answered with the requester's figures
    under the colleague's name. Vietnamese puts the subject on either side of the
    phrase — "An còn bao nhiêu ngày phép" and "quỹ phép của An" — so both are checked.
    """
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message):
        return True
    normalized = _normalize_intent_text(message)
    for marker in _LEAVE_BALANCE_MARKERS:
        head, separator, tail = normalized.partition(marker)
        if not separator:
            continue
        if _has_named_subject(head):
            return True
        # Only a possessive tail names an owner; "... là bao nhiêu" names nobody.
        _before, possessive, owner = tail.partition("cua ")
        return bool(possessive) and _has_named_subject(owner)
    return False


def _parse_hr_export_request(message: str) -> tuple[str | None, str | None]:
    normalized = _normalize_intent_text(message)
    export_format: str | None = None
    if "pdf" in normalized:
        export_format = "pdf"
    elif any(marker in normalized for marker in ("excel", "xlsx")):
        export_format = "xlsx"
    elif "json" in normalized:
        export_format = "json"

    directory_type: str | None = None
    if any(marker in normalized for marker in ("quan ly", "manager")):
        directory_type = "managers"
    elif any(marker in normalized for marker in ("nhan vien", "nhan su", "employee")):
        directory_type = "employees"
    return export_format, directory_type


def _extract_employee_search_term(message: str) -> str:
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message)
    if email_match:
        return email_match.group(0)

    normalized = _normalize_intent_text(message)
    original_words = message.strip().split()
    prefixes = (
        "tra cuu nhan vien",
        "xem ho so cua",
        "ho so nhan vien",
        "tim nhan vien",
        "tim ho so",
        "ho so cua",
    )
    for prefix in prefixes:
        if normalized == prefix:
            return ""
        if normalized.startswith(f"{prefix} "):
            return " ".join(original_words[len(prefix.split()):]).strip(" .?!")
    return ""


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
) -> dict[str, Any]:

    """Merge semantic LLM extraction over the safe deterministic parser fallback."""
    slots = _extract_leave_slots(message, existing)
    extracted = extract_leave_request_slots(
        message,
        existing=existing,
        reference_date=reference_date,
        timezone_name=timezone_name,
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


def execute_agent_chat(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    """Run the HR LLM-first gate, then dispatch to retrieval or governed tools."""
    if role_code.upper() != "HR":
        return _execute_agent_chat_core(db, user, role_code, message, thread_id)

    response: Dict[str, Any] | None = None
    for event in stream_hr_chat_events(db, user, role_code, message, thread_id):
        if event["event"] == "complete":
            response = event["response"]
    if response is None:
        raise RuntimeError("HR chat flow ended without a response")
    return response


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


def stream_hr_chat_events(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
) -> Iterator[Dict[str, Any]]:
    """Run the HR gate, yielding a phase before each blocking step.

    Every step here is slow enough to be worth announcing: intent routing and answer
    synthesis each call the LLM, and dispatch may hit retrieval. Callers that only want
    the answer drain the generator; the SSE endpoint forwards the phases as they arrive.
    """
    yield {"event": "status", "phase": "ANALYZING"}

    detailed_intent = _classify_hr_intent(message)
    leave_draft = _load_leave_draft(db, user, thread_id)
    normalized_message = _normalize_intent_text(message)
    leave_cancel_request = bool(
        leave_draft and _is_leave_cancel_message(normalized_message)
    )
    leave_continuation = bool(
        leave_draft
        and not leave_cancel_request
        and _is_leave_draft_continuation(message, leave_draft)
    )
    stateful_leave_action = leave_cancel_request or leave_continuation
    if stateful_leave_action:
        detailed_intent = "ACTION_LEAVE_REQUEST"

    classification = classify_hr_request(
        message,
        detailed_intent=detailed_intent,
    )

    # A slot-filling turn stays an action, but the router still gets to say that this
    # particular turn is a question. Cancelling a draft is never ambiguous, so only a
    # continuation may be reinterpreted this way.
    if (
        leave_continuation
        and classification.source == "llm"
        and classification.kind == "QUESTION"
        and classification.intent is not None
        and classification.intent not in ACTION_INTENTS
    ):
        stateful_leave_action = False

    request_kind = "ACTION" if stateful_leave_action else classification.kind

    routed_intent = detailed_intent
    if classification.source == "llm" and not stateful_leave_action:
        # The router understands paraphrases the keyword rules cannot cover. Its label
        # is already restricted to HR_INTENT_LABELS, and the branch it selects still
        # enforces tool permissions and purpose limitation.
        if classification.intent:
            routed_intent = classification.intent
        if request_kind == "QUESTION" and routed_intent in ACTION_INTENTS:
            # A question about an operation must not execute that operation.
            routed_intent = "POLICY_QUERY"
        elif request_kind == "QUESTION" and routed_intent == "UNKNOWN":
            # The HR agent was explicitly selected, so retrieve governed HR context.
            routed_intent = "POLICY_QUERY"
        elif request_kind == "ACTION" and routed_intent not in ACTION_INTENTS:
            # The model cannot invent a tool name or arguments. Unknown actions fail closed.
            routed_intent = "UNKNOWN"

    yield {"event": "status", "phase": "SEARCHING"}
    response = _execute_agent_chat_core(
        db,
        user,
        role_code,
        message,
        thread_id,
        hr_intent_override=routed_intent,
        leave_draft=leave_draft,
        leave_cancel_request=leave_cancel_request,
    )
    if response.get("tools_executed"):
        yield {"event": "status", "phase": "TOOL_CALLING"}

    if request_kind == "QUESTION" and routed_intent in SYNTHESIZABLE_INTENTS:
        # Self-service profile, compensation and leave-balance answers are already exact
        # and already contain personal data; they are neither improved nor safely
        # rewritten by a generative pass.
        response = generate_grounded_hr_answer(message, response)

    yield {"event": "status", "phase": "COMPLETED"}
    yield {"event": "complete", "response": response}


def _execute_agent_chat_core(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
    *,
    hr_intent_override: str | None = None,
    leave_draft: dict[str, Any] | None = None,
    leave_cancel_request: bool = False,
) -> Dict[str, Any]:
    """
    Main dispatch entry point for processing agent queries.
    Returns structured response containing answer text, citations, tool calls, and specialized card payloads.
    """
    role_code_upper = role_code.upper()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id,
        AIAgent.role_code == role_code_upper
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{role_code_upper}' not found")
    if not agent.is_active:
        raise HTTPException(status_code=409, detail=f"Agent '{role_code_upper}' is inactive")

    agent_name = agent.name if agent else f"{role_code_upper} Agent"
    agent_emoji = agent.avatar_emoji if agent else "🤖"

    response_data: Dict[str, Any] = {
        "agent_name": agent_name,
        "agent_role": role_code_upper,
        "avatar_emoji": agent_emoji,
        "reply": "",
        "citations": [],
        "tools_executed": [],
        "approval_card": None,
        "hr_card": None,
        "jira_card": None,
        "legal_risk_card": None,
        "invoice_card": None,
        "quote_card": None,
        "dag_plan_card": None,
    }

    # HR has its own LLM-first question/action gate below. Other agents may use
    # the generic LangGraph orchestration path.
    if settings.LANGGRAPH_ENABLED and role_code_upper != "HR":
        try:
            return LangGraphEngine().execute(
                db=db,
                user=user,
                agent=agent,
                message=message,
                conversation_id=thread_id,
            )
        except AIServiceError as exc:
            if (
                not settings.LANGGRAPH_LEGACY_FALLBACK
                or (exc.status_code is not None and exc.status_code < 500)
            ):
                raise
            logger.exception("LangGraph runtime failed; using the legacy deterministic executor")

    # -----------------------------------------------------------------------
    # 1. HR Agent Processing
    # -----------------------------------------------------------------------
    if role_code_upper == "HR":
        # stream_hr_chat_events already loaded the draft and resolved both the intent and
        # the cancel decision. Re-deriving them here would cost four extra queries a turn
        # and let the two copies of the rule drift apart.
        hr_intent = hr_intent_override or _classify_hr_intent(message)
        if leave_draft is None and hr_intent_override is None:
            leave_draft = _load_leave_draft(db, user, thread_id)
        normalized_message = _normalize_intent_text(message)

        if leave_draft and leave_cancel_request:
            cancelled_slots = _extract_leave_slots("", leave_draft)
            response_data["reply"] = (
                "Tôi đã hủy bản nháp xin nghỉ. Chưa có đơn nào được tạo hoặc gửi cho cấp trên."
            )
            response_data["hr_card"] = _leave_draft_card(
                cancelled_slots,
                _leave_missing_fields(cancelled_slots),
                status="CANCELLED",
            )
            return response_data

        if (
            hr_intent_override is None
            and leave_draft
            and _is_leave_draft_continuation(message, leave_draft)
        ):
            hr_intent = "ACTION_LEAVE_REQUEST"

        if hr_intent == "ACTION_ONBOARDING":
            _require_tool(agent, "create_onboarding_workflow")
            if not can_manage_hr(user):
                raise HTTPException(status_code=403, detail="Only HR can create onboarding workflows")
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message)
            if not email_match:
                response_data["reply"] = (
                    "Để tạo onboarding an toàn, vui lòng cung cấp email nhân viên, "
                    "ví dụ: **Tạo onboarding cho new.hire@company.com**."
                )
                return response_data
            employee = db.query(User).filter(
                User.tenant_id == user.tenant_id,
                User.email == email_match.group(0).lower(),
            ).first()
            if not employee:
                response_data["reply"] = "Không tìm thấy nhân viên có email này trong workspace."
                return response_data
            case = create_onboarding_case(
                db,
                employee=employee,
                creator=user,
                start_date=date.today(),
                probation_end_date=None,
                mentor_id=None,
            )
            response_data["tools_executed"].append({
                "tool_name": "create_onboarding_workflow",
                "input": {"employee_id": str(employee.id)},
                "result": {"onboarding_id": str(case.id), "tasks": len(case.steps)},
            })
            response_data["reply"] = (
                f"Đã tạo workflow onboarding cho **{employee.full_name}** với "
                f"**{len(case.steps)} nhiệm vụ** cho HR, IT, quản lý, Finance và nhân viên."
            )
            response_data["hr_card"] = {
                "type": "ONBOARDING",
                "id": str(case.id),
                "employee_name": employee.full_name,
                "status": case.status,
                "start_date": case.start_date.isoformat(),
                "task_count": len(case.steps),
            }
            return response_data

        if hr_intent == "FULL_PROFILE":
            _require_tool(agent, "get_employee_full_profile")
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message)
            if not email_match:
                response_data["reply"] = (
                    "Để kiểm soát đúng người và tránh lộ dữ liệu nhạy cảm, vui lòng cung cấp "
                    "**email công ty** của nhân viên và **mục đích nghiệp vụ**."
                )
                return response_data
            purpose_sections: tuple[str, list[str]] | None = None
            if any(marker in normalized_message for marker in ("gia han hop dong", "contract renewal")):
                purpose_sections = "CONTRACT_RENEWAL", ["BASIC", "CONTRACT"]
            elif any(marker in normalized_message for marker in ("danh gia cuoi nam", "danh gia hieu suat")):
                purpose_sections = "PERFORMANCE_REVIEW", ["BASIC", "PERFORMANCE"]
            elif "onboarding" in normalized_message:
                purpose_sections = "ONBOARDING", ["BASIC", "PRIVATE", "CONTRACT", "DOCUMENTS"]
            elif any(marker in normalized_message for marker in ("xu ly bang luong", "payroll")):
                purpose_sections = "PAYROLL_PROCESSING", ["BASIC", "COMPENSATION"]
            if not purpose_sections:
                response_data["reply"] = (
                    "Yêu cầu hồ sơ sâu bắt buộc có mục đích hợp lệ, ví dụ: "
                    "**gia hạn hợp đồng**, **đánh giá hiệu suất**, **onboarding** hoặc "
                    "**xử lý bảng lương**. Tôi chưa truy cập dữ liệu khi chưa có mục đích."
                )
                return response_data
            employee = db.query(User).filter(
                User.tenant_id == user.tenant_id,
                User.email == email_match.group(0).lower(),
            ).first()
            if not employee:
                response_data["reply"] = "Không tìm thấy nhân viên trong workspace hiện tại."
                return response_data
            purpose, requested_sections = purpose_sections
            profile_payload = _employee_profile_payload(
                db,
                user,
                employee,
                requested_sections=requested_sections,
                purpose=purpose,
                tool_name="get_employee_full_profile",
            )
            response_data["tools_executed"].append({
                "tool_name": "get_employee_full_profile",
                "input": {
                    "employee_id": str(employee.id),
                    "requested_sections": requested_sections,
                    "purpose": purpose,
                },
                "result": _access_result(profile_payload),
            })
            response_data["reply"] = _employee_profile_reply(profile_payload)
            response_data["hr_card"] = profile_payload
            return response_data

        if hr_intent == "SELF_COMPENSATION":
            _require_tool(agent, "get_employee_compensation_summary")
            profile_payload = _employee_profile_payload(
                db,
                user,
                user,
                requested_sections=["BASIC", "COMPENSATION"],
                purpose="SELF_SERVICE",
                tool_name="get_employee_compensation_summary",
            )
            response_data["tools_executed"].append({
                "tool_name": "get_employee_compensation_summary",
                "input": {
                    "employee_id": str(user.id),
                    "requested_sections": ["BASIC", "COMPENSATION"],
                    "purpose": "SELF_SERVICE",
                },
                "result": _access_result(profile_payload),
            })
            response_data["reply"] = _employee_profile_reply(profile_payload)
            response_data["hr_card"] = profile_payload
            return response_data

        if hr_intent == "SELF_PRIVATE_PROFILE":
            _require_tool(agent, "get_employee_private_profile")
            profile_payload = _employee_profile_payload(
                db,
                user,
                user,
                requested_sections=["BASIC", "PRIVATE"],
                purpose="SELF_SERVICE",
                tool_name="get_employee_private_profile",
            )
            response_data["tools_executed"].append({
                "tool_name": "get_employee_private_profile",
                "input": {
                    "employee_id": str(user.id),
                    "requested_sections": ["BASIC", "PRIVATE"],
                    "purpose": "SELF_SERVICE",
                },
                "result": _access_result(profile_payload),
            })
            response_data["reply"] = _employee_profile_reply(profile_payload)
            response_data["hr_card"] = profile_payload
            return response_data

        if hr_intent == "SELF_PROFILE":
            _require_tool(agent, "get_employee_full_profile")
            # The leave quota is a separate grant. Drop that one section when Admin has
            # withheld it, rather than failing the whole self-service lookup.
            self_sections = ["BASIC"]
            if _can_use_tool(agent, "get_employee_leave_summary"):
                self_sections.append("LEAVE")
            profile_payload = _employee_profile_payload(
                db,
                user,
                user,
                requested_sections=self_sections,
                purpose="SELF_SERVICE",
                tool_name="get_employee_full_profile",
            )
            response_data["tools_executed"].append({
                "tool_name": "get_employee_full_profile",
                "input": {
                    "employee_id": str(user.id),
                    "requested_sections": self_sections,
                    "purpose": "SELF_SERVICE",
                },
                "result": _access_result(profile_payload),
            })
            response_data["reply"] = _employee_profile_reply(profile_payload)
            response_data["hr_card"] = profile_payload
            return response_data

        if hr_intent == "ACTION_EXPORT":
            # Permission first, conversation second — as in every other branch. Asking for
            # a format before checking the grant both wastes a turn and confirms the
            # feature exists to somebody who may not use it.
            _require_tool(agent, "export_hr_directory")
            export_format, directory_type = _parse_hr_export_request(message)
            missing = []
            if not directory_type:
                missing.append("loại dữ liệu (**danh sách nhân viên** hoặc **danh sách quản lý**)")
            if not export_format:
                missing.append("định dạng (**Excel**, **PDF** hoặc **JSON**)")
            if missing:
                response_data["reply"] = (
                    "Để tạo file an toàn, vui lòng bổ sung "
                    + " và ".join(missing)
                    + ". Ví dụ: **Xuất danh sách nhân viên Excel**."
                )
                return response_data

            directory_label = (
                "danh sách quản lý" if directory_type == "managers" else "danh sách nhân viên"
            )
            format_label = {"xlsx": "Excel", "pdf": "PDF", "json": "JSON"}[export_format]
            download_url = (
                f"/api/v1/hr/employees/export?format={export_format}"
                f"&directory={directory_type}"
            )
            # This branch reads no employee data and writes no audit row: it only hands
            # back a link. The dataset is built, scoped and audited later by
            # GET /hr/employees/export, which re-derives the actor's own scope. Withholding
            # the export_hr_directory grant therefore removes the affordance from chat, not
            # access to the endpoint -- the human's permissions are what gate the download.
            response_data["tools_executed"].append({
                "tool_name": "export_hr_directory",
                "input": {
                    "format": export_format,
                    "directory": directory_type,
                    "purpose": "DIRECTORY_EXPORT",
                },
                "result": {"download_link_issued": True},
            })
            response_data["reply"] = (
                f"File **{format_label}** cho **{directory_label}** đã sẵn sàng. "
                "Dữ liệu sẽ được lấy lại theo quyền hiện tại của bạn khi tải xuống."
            )
            response_data["hr_card"] = {
                "type": "FILE_EXPORT",
                "format": export_format,
                "format_label": format_label,
                "directory_type": directory_type,
                "directory_label": directory_label,
                "download_url": download_url,
                "scope": hr_scope_label(user),
            }
            return response_data

        if hr_intent == "MANAGER_DIRECTORY":
            _require_tool(agent, "query_company_users_sql")
            directory = query_company_users_sql(
                db,
                actor=user,
                roles=["Admin", "Manager"],
                active_only=True,
                limit=100,
            )
            items = [
                _sql_directory_item(employee, scope=directory["scope"])
                for employee in directory["items"]
            ]
            scope = directory["scope"]
            response_data["tools_executed"].append({
                "tool_name": "query_company_users_sql",
                "input": {
                    "directory": "managers",
                    "scope": scope,
                    "requested_sections": ["BASIC"],
                    "purpose": "DIRECTORY_LOOKUP",
                },
                "result_count": len(items),
            })
            total_count = directory.get("total_count", len(items))
            response_data["reply"] = (
                f"Tôi tìm thấy **{total_count} quản lý** trong phạm vi **{scope}** "
                "bạn được phép xem."
            )
            response_data["hr_card"] = {
                "type": "EMPLOYEE_SEARCH",
                "directory_type": "MANAGERS",
                "scope": scope,
                "total_count": total_count,
                "items": items,
            }
            return response_data

        if hr_intent == "EMPLOYEE_DIRECTORY":
            _require_tool(agent, "query_company_users_sql")
            directory = query_company_users_sql(
                db,
                actor=user,
                active_only=True,
                limit=100,
            )
            items = [
                _sql_directory_item(employee, scope=directory["scope"])
                for employee in directory["items"]
            ]
            scope = directory["scope"]
            response_data["tools_executed"].append({
                "tool_name": "query_company_users_sql",
                "input": {
                    "query": "*",
                    "scope": scope,
                    "requested_sections": ["BASIC"],
                    "purpose": "DIRECTORY_LOOKUP",
                },
                "result_count": len(items),
            })
            total_count = directory.get("total_count", len(items))
            response_data["reply"] = (
                f"Tôi tìm thấy **{total_count} nhân viên** trong phạm vi **{scope}** bạn được phép xem."
            )
            response_data["hr_card"] = {
                "type": "EMPLOYEE_SEARCH",
                "scope": scope,
                "total_count": total_count,
                "items": items,
            }
            return response_data

        if hr_intent == "EMPLOYEE_SEARCH":
            _require_tool(agent, "query_company_users_sql")
            search_term = _extract_employee_search_term(message)
            if not search_term:
                response_data["reply"] = "Vui lòng cung cấp tên hoặc email nhân viên cần tra cứu."
                return response_data

            directory = query_company_users_sql(
                db,
                actor=user,
                search=search_term,
                active_only=False,
                limit=10,
            )
            matches = directory["items"]
            scope = directory["scope"]
            response_data["tools_executed"].append({
                "tool_name": "query_company_users_sql",
                "input": {
                    "query": search_term,
                    "scope": scope,
                    "requested_sections": ["BASIC"],
                    "purpose": "DIRECTORY_LOOKUP",
                },
                "result_count": len(matches),
            })
            if not matches:
                response_data["reply"] = (
                    "Không tìm thấy nhân viên phù hợp trong phạm vi bạn được phép xem."
                )
                return response_data
            if len(matches) == 1:
                profile_payload = _sql_directory_item(matches[0], scope=scope)
                response_data["reply"] = _employee_profile_reply(profile_payload)
                response_data["hr_card"] = profile_payload
                return response_data
            response_data["reply"] = (
                f"Tìm thấy **{len(matches)} hồ sơ** trong phạm vi **{scope}**. "
                "Bạn có thể tìm lại bằng email để chọn chính xác một người."
            )
            response_data["hr_card"] = {
                "type": "EMPLOYEE_SEARCH",
                "scope": scope,
                "items": [
                    _sql_directory_item(employee, scope=scope)
                    for employee in matches
                ],
            }
            return response_data

        if hr_intent == "SELF_CONTRACT":
            _require_tool(agent, "get_employee_contract_summary")
            profile_payload = _employee_profile_payload(
                db,
                user,
                user,
                requested_sections=["BASIC", "CONTRACT"],
                purpose="SELF_SERVICE",
                tool_name="get_employee_contract_summary",
            )
            response_data["tools_executed"].append({
                "tool_name": "get_employee_contract_summary",
                "input": {"employee_id": str(user.id), "purpose": "SELF_SERVICE"},
                "result_count": len(profile_payload.get("contracts") or []),
            })
            response_data["reply"] = _employee_profile_reply(profile_payload)
            response_data["hr_card"] = profile_payload
            return response_data

        if hr_intent == "CONTRACT_EXPIRY":
            _require_tool(agent, "get_contract_expiry")
            contract_result = list_contract_status_summaries(
                db,
                actor=user,
                purpose="CONTRACT_STATUS_MONITORING",
                limit=10,
                tool_name="get_contract_expiry",
            )
            contracts = contract_result["items"]
            response_data["tools_executed"].append({
                "tool_name": "get_contract_expiry",
                "input": {
                    "scope": contract_result["scope"],
                    "purpose": contract_result["purpose"],
                },
                "result_count": len(contracts),
            })
            if not contracts:
                response_data["reply"] = "Không tìm thấy hợp đồng đang hiệu lực trong phạm vi bạn được phép xem."
            else:
                lines = [
                    f"- **{item['employee_name']}** · {item['contract_type']} · "
                    f"hết hạn {item['end_date'] or 'không thời hạn'}"
                    for item in contracts
                ]
                response_data["reply"] = "Các hợp đồng đang hiệu lực:\n" + "\n".join(lines)
            response_data["hr_card"] = {
                "type": "CONTRACTS",
                "scope": contract_result["scope"],
                "purpose": contract_result["purpose"],
                "items": contracts,
            }
            return response_data

        if hr_intent == "PENDING_APPROVALS":
            _require_tool(agent, "list_pending_hr_approvals")
            # The approval rule is not expressible as SQL, so filtering stays in Python.
            # Walk the tenant's queue newest-first in bounded batches rather than loading
            # every WAITING approval at once. The scan does not stop at the card size
            # because the reply states a total count, which needs the whole scan window.
            approval_query = db.query(WorkflowApproval).join(AgentWorkflow).options(
                joinedload(WorkflowApproval.workflow)
            ).filter(
                AgentWorkflow.tenant_id == user.tenant_id,
                WorkflowApproval.status == "WAITING",
            ).order_by(WorkflowApproval.updated_at.desc())
            visible: list[WorkflowApproval] = []
            scanned = 0
            while scanned < PENDING_APPROVAL_SCAN_LIMIT:
                batch = approval_query.offset(scanned).limit(PENDING_APPROVAL_BATCH).all()
                visible.extend(
                    item for item in batch if can_approve_hr_request(db, user, item)
                )
                scanned += len(batch)
                if len(batch) < PENDING_APPROVAL_BATCH:
                    break
            # Exhausting the scan window is not the same as leaving rows behind: a queue of
            # exactly PENDING_APPROVAL_SCAN_LIMIT rows is fully counted. Probe for one more
            # row instead of inferring truncation from how the loop ended.
            truncated = (
                scanned >= PENDING_APPROVAL_SCAN_LIMIT
                and approval_query.offset(scanned).limit(1).first() is not None
            )
            count_text = (
                f"ít nhất **{len(visible)} yêu cầu**"
                if truncated else f"**{len(visible)} yêu cầu**"
            )
            response_data["reply"] = (
                f"Bạn có {count_text} đang chờ xử lý."
                if visible else "Hiện không có yêu cầu nào đang chờ bạn phê duyệt."
            )
            response_data["hr_card"] = {
                "type": "PENDING_APPROVALS",
                "truncated": truncated,
                "items": [
                    {
                        "id": str(item.id),
                        "workflow_title": item.workflow.title,
                        "action_type": item.action_type,
                        "risk_level": item.risk_level,
                        "payload": item.payload or {},
                        "status": item.status,
                        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                    }
                    for item in visible[:PENDING_APPROVAL_CARD_SIZE]
                ],
            }
            return response_data

        if hr_intent == "ACTION_LEAVE_REQUEST":
            _require_tool(agent, "request_leave")
            reference_date, timezone_name = _leave_date_context(db, user)
            slots = _extract_leave_slots_with_llm(
                message,
                leave_draft,
                reference_date=reference_date,
                timezone_name=timezone_name,
            )
            missing_fields = _leave_missing_fields(slots)
            if missing_fields:
                response_data["reply"] = _leave_follow_up_reply(slots, missing_fields)
                response_data["hr_card"] = _leave_draft_card(slots, missing_fields)
                return response_data

            parsed_start = date.fromisoformat(str(slots["start_date"]))
            parsed_end = date.fromisoformat(str(slots["end_date"]))
            if parsed_end < parsed_start:
                validation_error = "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu."
                response_data["reply"] = (
                    f"{validation_error} Vui lòng cung cấp lại ngày kết thúc nghỉ."
                )
                response_data["hr_card"] = _leave_draft_card(
                    slots,
                    [],
                    validation_error=validation_error,
                )
                return response_data

            req_result = request_leave(
                db,
                user,
                days=None,
                reason=str(slots["reason"]),
                start_date=str(slots["start_date"]),
                end_date=str(slots["end_date"]),
            )

            response_data["tools_executed"].append({
                "tool_name": "request_leave",
                "input": {
                    "start_date": slots["start_date"],
                    "end_date": slots["end_date"],
                    "reason": slots["reason"],
                },
                "result": {"success": bool(req_result["success"])},
            })

            log_audit_action(
                db,
                user.tenant_id,
                "HR",
                "request_leave",
                {
                    "start_date": slots["start_date"],
                    "end_date": slots["end_date"],
                },
                {"success": req_result["success"]},
            )

            if req_result["success"]:
                response_data["reply"] = (
                    f"Tôi đã tổng hợp và gửi đơn nghỉ phép của **{user.full_name}** tới cấp trên:\n"
                    f"- Ngày bắt đầu: **{slots['start_date']}**\n"
                    f"- Ngày kết thúc: **{slots['end_date']}**\n"
                    f"- Lý do: **{slots['reason']}**\n\n"
                    "Sau khi được phê duyệt, hệ thống sẽ cập nhật quỹ phép và đồng bộ lịch."
                )
                response_data["approval_card"] = req_result["approval_card"]
            else:
                response_data["reply"] = req_result["message"]
                response_data["hr_card"] = _leave_draft_card(
                    slots,
                    [],
                    validation_error=req_result["message"],
                )

            return response_data

        if hr_intent == "QUERY_LEAVE_BALANCE":
            _require_tool(agent, "query_leave_balance")
            # Guard the branch rather than the classifier, so the check also covers the
            # paraphrases the LLM router sends here.
            if _leave_balance_names_another_person(message):
                response_data["reply"] = (
                    "Tôi chỉ tra được quỹ phép của **chính bạn**. Để xem dữ liệu phép của "
                    "nhân viên khác, bạn cần yêu cầu **hồ sơ đầy đủ** kèm **email công ty** "
                    "và **mục đích nghiệp vụ** hợp lệ."
                )
                return response_data
            bal = query_leave_balance(db, user)
            response_data["tools_executed"].append({
                "tool_name": "query_leave_balance",
                "input": {"user_id": str(user.id)},
                "result": bal,
            })
            log_audit_action(db, user.tenant_id, "HR", "query_leave_balance", {"user_id": str(user.id)}, bal)

            response_data["reply"] = (
                f"Thông tin số ngày phép của **{user.full_name}**:\n"
                f"- **Tổng số ngày phép năm**: {bal['total_days']} ngày\n"
                f"- **Đã sử dụng**: {bal['used_days']} ngày\n"
                f"- **Còn lại**: **{bal['remaining_days']} ngày** hưởng nguyên lương."
            )
            response_data["hr_card"] = {"type": "LEAVE_BALANCE", "balance": bal}
            return response_data

        policy_notice = ""
        if hr_intent == "EMPLOYEE_LEAVE_STATUS_COUNT":
            # There is no day-by-day leave calendar tool yet. Say so, then still search the
            # governed HR knowledge base rather than ending the turn with nothing: both the
            # keyword rules and the router can land here, and neither has an alternative.
            policy_notice = (
                "HR Agent chưa có tool lịch nghỉ theo ngày nên chưa thể đếm chính xác số "
                "nhân viên đang nghỉ. Dưới đây là thông tin liên quan trong kho tài liệu HR:"
                "\n\n"
            )
            hr_intent = "POLICY_QUERY"

        if hr_intent == "UNKNOWN":
            response_data["reply"] = (
                "Tôi chưa xác định rõ nghiệp vụ HR cần thực hiện. Bạn có thể yêu cầu, ví dụ: "
                "**tìm nhân viên An**, **liệt kê các quản lý**, **xem ngày phép của tôi**, "
                "**hỏi chính sách nghỉ phép** hoặc **xuất danh sách nhân viên Excel**."
            )
            return response_data

        if hr_intent == "POLICY_QUERY":
            _require_tool(agent, "hybrid_rag_search")
            search_results = hybrid_search_documents(
                db,
                user.tenant_id,
                message,
                department="HR",
                collections=None,
                agent_access=agent.knowledge_access if agent.knowledge_access else None,
                user_role=user.role,
                user_department=user.department,
            )
            response_data["tools_executed"].append({
                "tool_name": "hybrid_rag_search",
                "input": {"query": message},
                "result_count": len(search_results),
            })
            log_audit_action(
                db,
                user.tenant_id,
                "HR",
                "hybrid_rag_search",
                {"query": message},
                {
                    "count": len(search_results),
                    "chunks": [
                        {
                            "chunk_id": item["id"],
                            "document_id": item["document_id"],
                            "version": item["version"],
                            "page": item["page"],
                        }
                        for item in search_results
                    ],
                },
            )

            if search_results:
                top_result = search_results[0]
                response_data["citations"] = search_results
                policy_dates = []
                if top_result.get("effective_date"):
                    policy_dates.append(f"Hiệu lực từ {top_result['effective_date']}")
                if top_result.get("expiration_date"):
                    policy_dates.append(f"hết hiệu lực {top_result['expiration_date']}")
                policy_date_line = (
                    f"\n\n**Thông tin hiệu lực:** {' · '.join(policy_dates)}"
                    if policy_dates else ""
                )
                response_data["reply"] = (
                    f"{policy_notice}"
                    f"Dựa trên quy định HR của công ty:\n\n"
                    f"{top_result['content']}\n\n"
                    f"{top_result['citation_tag']}"
                    f"{policy_date_line}"
                )
            else:
                response_data["reply"] = (
                    f"{policy_notice}"
                    "Tôi chưa tìm thấy chính sách còn hiệu lực và phù hợp trong kho tài liệu HR. "
                    "Tôi sẽ không tự suy diễn quy định; vui lòng liên hệ HR để được xác nhận."
                )
            return response_data

        # Unreachable today: every label in HR_INTENT_LABELS has a branch above, and the
        # router drops anything outside that set. Kept as a net so a label added later
        # without a branch degrades to a refusal instead of falling through to whatever
        # follows. Do not read it as evidence of a missing intent.
        response_data["reply"] = (
            "Tôi chưa thể xử lý yêu cầu HR này. Vui lòng mô tả rõ hành động và đối tượng cần tra cứu."
        )
        return response_data

    # -----------------------------------------------------------------------
    # 2. KNOWLEDGE Agent Processing (Hybrid RAG)
    # -----------------------------------------------------------------------
    elif role_code_upper == "KNOWLEDGE":
        _require_tool(agent, "hybrid_search_documents")
        search_results = hybrid_search_documents(
            db,
            user.tenant_id,
            message,
            department="*" if user.role in {"Owner", "Admin", "CEO"} else user.department,
            collections=None,
            agent_access=agent.knowledge_access if agent.knowledge_access else None,
            user_role=user.role,
            user_department=user.department,
        )

        # Out-of-domain query check: ensure at least some word overlap with knowledge base
        msg_words = set(re.findall(r'\w+', message.lower()))
        filtered_results = []
        for c in search_results:
            c_words = set(re.findall(r'\w+', c["content"].lower()))
            if len(msg_words.intersection(c_words)) > 0:
                filtered_results.append(c)

        search_results = filtered_results

        response_data["tools_executed"].append({
            "tool_name": "hybrid_search_documents",
            "input": {"query": message, "department": user.department},
            "result_count": len(search_results),
        })
        log_audit_action(
            db,
            user.tenant_id,
            "KNOWLEDGE",
            "hybrid_search_documents",
            {"query": message},
            {
                "count": len(search_results),
                "chunks": [
                    {
                        "chunk_id": item["id"],
                        "document_id": item["document_id"],
                        "version": item["version"],
                        "page": item["page"],
                    }
                    for item in search_results
                ],
            },
        )

        if search_results:
            response_data["citations"] = search_results
            best_chunk = search_results[0]
            citations_str = " ".join([c["citation_tag"] for c in search_results[:2]])

            response_data["reply"] = (
                f"Theo tài liệu tri thức doanh nghiệp:\n\n"
                f"{best_chunk['content']}\n\n"
                f"**Nguồn trích dẫn:** {citations_str}"
            )
        else:
            response_data["reply"] = (
                f"Hiện chưa tìm thấy tài liệu phù hợp trong Kho tri thức cho câu hỏi: *'{message}'*.\n"
                f"Bạn có thể tải thêm tài liệu quy định/SOP vào trang **Knowledge Base** để tôi truy xuất!"
            )
        return response_data

    # -----------------------------------------------------------------------
    # 3. LEGAL Agent Processing (Contract Risk Audit & Redline)
    # -----------------------------------------------------------------------
    elif role_code_upper == "LEGAL":
        if not _can_use_tool(agent, "audit_contract_risk"):
            response_data["reply"] = (
                "Tôi là Legal Counsel AI và đã nhận nội dung bạn gửi. "
                "Công cụ rà soát rủi ro hợp đồng `audit_contract_risk` hiện chưa "
                "được bật cho AI Employee này, nên tôi không tự ý thực thi công cụ. "
                "Admin hoặc Owner có thể bật công cụ trong phần Cấu hình nếu cần "
                "phân tích điều khoản và tạo thẻ rủi ro."
            )
            return response_data
        normalized_legal_message = message.lower()
        contract_signals = (
            "hợp đồng", "contract", "agreement", "nda", "msa", "sow", "điều khoản", "clause"
        )
        review_signals = (
            "rà soát", "review", "audit", "risk", "rủi ro", "phạt", "penalty",
            "unlimited liability", "không giới hạn", "đơn phương chấm dứt",
            "customer owns", "khách hàng sở hữu",
        )
        is_contract_review = (
            any(signal in normalized_legal_message for signal in contract_signals)
            and (
                any(signal in normalized_legal_message for signal in review_signals)
                or len(message) >= 180
            )
        )
        if is_contract_review:
            audit_res = audit_contract_text(message)
            response_data["tools_executed"].append({
                "tool_name": "audit_contract_risk",
                "input": {"text_length": len(message)},
                "risks_found": audit_res["total_risks_found"],
                "risk_score": audit_res["risk_score"],
            })
            log_audit_action(
                db,
                user.tenant_id,
                "LEGAL",
                "audit_contract_risk",
                {"text_length": len(message)},
                {"risks": audit_res["total_risks_found"], "risk_score": audit_res["risk_score"]},
            )
            response_data["reply"] = (
                f"Tôi đã rà soát nội dung hợp đồng và phát hiện **{audit_res['total_risks_found']} vấn đề**, "
                f"với điểm rủi ro **{audit_res['risk_score']}/100 ({audit_res['risk_level']})**.\n\n"
                "Mỗi phát hiện bên dưới kèm bằng chứng từ nội dung và hành động đề xuất."
            )
            response_data["legal_risk_card"] = audit_res
            return response_data

        search_results = hybrid_search_documents(
            db,
            user.tenant_id,
            message,
            department="*" if user.role in {"Owner", "Admin", "CEO"} else user.department,
            collections=None,
            agent_access=agent.knowledge_access if agent.knowledge_access else None,
            user_role=user.role,
            user_department=user.department,
        )
        response_data["tools_executed"].append({
            "tool_name": "hybrid_rag_search",
            "input": {"query": message, "department": user.department, "acl_applied": True},
            "result_count": len(search_results),
        })
        log_audit_action(
            db,
            user.tenant_id,
            "LEGAL",
            "hybrid_rag_search",
            {"query": message, "acl_applied": True},
            {"count": len(search_results)},
        )
        if search_results:
            response_data["citations"] = search_results
            excerpts = "\n\n".join(
                f"{item['content']}\n{item['citation_tag']}"
                for item in search_results[:2]
            )
            response_data["reply"] = (
                "Theo văn bản pháp luật và chính sách bạn được phép truy cập:\n\n"
                f"{excerpts}\n\n"
                "Nếu quyết định này tạo nghĩa vụ pháp lý hoặc chia sẻ dữ liệu nhạy cảm, hãy gửi Legal phê duyệt."
            )
            return response_data

        legal_terms = {
            "indemnification": "Indemnification là nghĩa vụ bồi hoàn cho bên kia khi phát sinh tổn thất hoặc khiếu nại thuộc phạm vi đã cam kết. Cần kiểm tra phạm vi, giới hạn tiền, loại khiếu nại và quyền kiểm soát việc bảo vệ.",
            "bồi thường": "Điều khoản bồi thường xác định khi nào một bên phải bù đắp tổn thất cho bên kia. Cần làm rõ nguyên nhân, phạm vi, trần trách nhiệm và thủ tục yêu cầu.",
            "force majeure": "Force majeure (bất khả kháng) là sự kiện ngoài khả năng kiểm soát hợp lý làm cản trở việc thực hiện nghĩa vụ. Điều khoản nên quy định sự kiện, thông báo và hậu quả cụ thể.",
            "intellectual property": "Intellectual property là quyền đối với tài sản trí tuệ như mã nguồn, thiết kế, nhãn hiệu và tài liệu. Hợp đồng cần tách IP có sẵn với deliverable được tạo trong dự án.",
        }
        definition = next(
            (value for term, value in legal_terms.items() if term in normalized_legal_message),
            None,
        )
        response_data["reply"] = definition or (
            "Tôi chưa tìm thấy văn bản còn hiệu lực và phù hợp trong phạm vi ACL của bạn. "
            "Tôi sẽ không tự suy diễn quy định; vui lòng bổ sung tài liệu hoặc gửi Legal Team xác nhận."
        )
        return response_data

    # -----------------------------------------------------------------------
    # 4. IT Agent Processing (Technical Help & Jira Tickets)
    # -----------------------------------------------------------------------
    elif role_code_upper == "IT":
        it_res = handle_it_request(db, user, message)
        _require_tool(
            agent,
            "create_jira_ticket" if it_res.get("ticket_created") else "search_it_kb",
        )
        response_data["tools_executed"].append({
            "tool_name": "create_jira_ticket" if it_res.get("ticket_created") else "search_it_kb",
            "input": {"message": message},
            "result": "Jira Ticket Created" if it_res.get("ticket_created") else "KB Resolved",
        })
        log_audit_action(db, user.tenant_id, "IT", "create_jira_ticket" if it_res.get("ticket_created") else "search_it_kb", {"msg": message}, {"ticket": it_res.get("ticket_created")})

        response_data["reply"] = it_res["reply"]
        if it_res.get("jira_card"):
            response_data["jira_card"] = it_res["jira_card"]
        return response_data

    # -----------------------------------------------------------------------
    # 5. FINANCE Agent Processing (Invoice OCR & PO Reconciliation)
    # -----------------------------------------------------------------------
    elif role_code_upper == "FINANCE":
        _require_tool(agent, "reconcile_po_db")
        fin_res = audit_invoice_and_reconcile(message)
        response_data["tools_executed"].append({
            "tool_name": "reconcile_po_db",
            "input": {"text_length": len(message)},
            "status": fin_res["invoice_card"]["status"],
        })
        log_audit_action(db, user.tenant_id, "FINANCE", "reconcile_po_db", {"msg": message[:30]}, {"status": fin_res["invoice_card"]["status"]})

        response_data["reply"] = fin_res["reply"]
        response_data["invoice_card"] = fin_res["invoice_card"]
        return response_data

    # -----------------------------------------------------------------------
    # 6. SALES Agent Processing (Catalog Lookup & Quotation PDF)
    # -----------------------------------------------------------------------
    elif role_code_upper == "SALES":
        _require_tool(agent, "generate_quotation_pdf")
        sales_res = handle_sales_request(message, customer_name=user.full_name)
        response_data["tools_executed"].append({
            "tool_name": "generate_quotation_pdf",
            "input": {"message": message},
            "total_amount": sales_res["quote_card"]["total_amount"],
        })
        log_audit_action(db, user.tenant_id, "SALES", "generate_quotation_pdf", {"msg": message}, {"total": sales_res["quote_card"]["total_amount"]})

        response_data["reply"] = sales_res["reply"]
        response_data["quote_card"] = sales_res["quote_card"]
        return response_data

    # -----------------------------------------------------------------------
    # 7. CEO Agent (Master Orchestrator DAG)
    # -----------------------------------------------------------------------
    elif role_code_upper == "CEO":
        _require_tool(agent, "generate_and_execute_ceo_dag")
        ceo_res = generate_and_execute_ceo_dag(db, user, message)
        response_data["tools_executed"].append({
            "tool_name": "generate_and_execute_ceo_dag",
            "input": {"prompt": message},
            "subtasks_count": 4,
        })
        log_audit_action(db, user.tenant_id, "CEO", "generate_and_execute_ceo_dag", {"prompt": message}, {"nodes": 4})

        response_data["reply"] = ceo_res["reply"]
        response_data["dag_plan_card"] = ceo_res["dag_plan_card"]
        return response_data

    else:
        response_data["reply"] = f"Agent {role_code_upper} đã tiếp nhận chỉ thị: {message}"
        return response_data
