"""One HR turn once its intent is known: dispatch to the governed HR capability."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Any

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from app.models.models import AIAgent, AgentWorkflow, User, WorkflowApproval
from app.domains.hr.hr_service import (
    can_manage_hr,
    can_approve_hr_request,
    create_onboarding_case,
    hr_scope_label,
    leave_scope,
    list_leave_requests_in_scope,
    query_leave_balance,
    request_leave,
    withdraw_leave_request,
)
from app.domains.hr.hr_employee_tools import (
    list_contract_status_summaries,
    list_tenant_departments,
    query_company_users_sql,
)
from app.domains.platform.position_service import supervisory_role_names
from app.domains.knowledge.agent_knowledge_scope import agent_scope
from app.domains.knowledge.rag_service import hybrid_search_documents, user_search_scope
from app.domains.platform.audit_service import log_audit_action
from app.plugins.resolver import resolve_prompt_overlay
from app.agents.hr.llm_flow import LookupArguments, UsageReporter, extract_lookup_arguments
from app.agents.access import _can_use_tool, _require_tool
from app.agents.hr.intent import (
    _classify_hr_intent,
    _department_filter_label,
    _extract_employee_search_term,
    _leave_balance_names_another_person,
    _parse_hr_export_request,
    _resolve_requested_departments,
    _unknown_department_reply,
)
from app.agents.hr.leave import (
    _extract_leave_slots,
    _extract_leave_slots_with_llm,
    _is_leave_draft_continuation,
    _leave_date_context,
    _leave_draft_card,
    _leave_follow_up_reply,
    _leave_missing_fields,
    _load_leave_draft,
)
from app.agents.hr.profile import (
    _access_result,
    _employee_profile_payload,
    _employee_profile_reply,
    _sql_directory_item,
)
from app.agents.text import _normalize_intent_text
from app.agents.usage import _hr_llm_usage_recorder


# The approval visibility rule runs in Python, so the queue is walked in bounded batches
# instead of being loaded whole.
PENDING_APPROVAL_BATCH = 100


PENDING_APPROVAL_SCAN_LIMIT = 1000


PENDING_APPROVAL_CARD_SIZE = 20


# A "who is off" question about a longer stretch than this is answered for its first
# LEAVE_CALENDAR_MAX_DAYS days, so one sentence cannot pull a year of everybody's leave.
LEAVE_CALENDAR_MAX_DAYS = 62


LEAVE_STATUS_LABELS = {
    "WAITING": "Chờ duyệt",
    "APPROVED": "Đã duyệt",
    "REJECTED": "Bị từ chối",
    "CANCELLED": "Đã rút",
}


# What each purpose of a deep profile request releases. The policy behind
# `get_employee_sections` still decides whether this asker may see them for that purpose.
PROFILE_PURPOSE_SECTIONS: dict[str, list[str]] = {
    "CONTRACT_RENEWAL": ["BASIC", "CONTRACT"],
    "PERFORMANCE_REVIEW": ["BASIC", "PERFORMANCE"],
    "ONBOARDING": ["BASIC", "PRIVATE", "CONTRACT", "DOCUMENTS"],
    "PAYROLL_PROCESSING": ["BASIC", "COMPENSATION"],
}


@dataclass(frozen=True)
class HRToolArguments:
    """The arguments of an HR gateway tool call, so the branch reads none from the message.

    Under LangGraph the model picks the capability and fills these with function calling;
    the deterministic gate passes None and every branch reads the sentence as before. Who
    is meant is never taken from here on trust: an email still comes from the user's own
    message, and every branch still checks the grant and the asker's scope.
    """

    lookup: LookupArguments | None = None
    leave_slots: dict[str, Any] | None = None
    export_format: str | None = None
    directory_type: str | None = None
    purpose: str | None = None
    # A leave-balance question about a colleague, which this capability refuses.
    about_someone_else: bool = False


def _read_lookup_arguments(
    db: Session,
    user: User,
    *,
    role_code_upper: str,
    message: str,
    intent: str,
    on_llm_usage: UsageReporter | None,
) -> LookupArguments | None:
    """The lookup's arguments as the model read them; None leaves the keyword rules on."""
    reference_date, timezone_name = _leave_date_context(db, user)
    return extract_lookup_arguments(
        message,
        intent=intent,
        departments=list_tenant_departments(db, actor=user),
        reference_date=reference_date,
        timezone_name=timezone_name,
        on_usage=on_llm_usage or _hr_llm_usage_recorder(db, user),
        prompts=resolve_prompt_overlay(db, user.tenant_id, role_code_upper),
    )


def _period_label(start: date, end: date, today: date) -> str:
    if start == end:
        prefix = "hôm nay" if start == today else "ngày"
        return f"{prefix} **{start:%d/%m/%Y}**"
    return f"từ **{start:%d/%m/%Y}** đến **{end:%d/%m/%Y}**"


def _leave_request_line(item: dict[str, Any], *, with_name: bool) -> str:
    start = date.fromisoformat(item["start_date"])
    end = date.fromisoformat(item["end_date"])
    period = f"{start:%d/%m/%Y}" if start == end else f"{start:%d/%m/%Y} – {end:%d/%m/%Y}"
    who = f"**{item['employee']['name']}** · " if with_name else ""
    status = LEAVE_STATUS_LABELS.get(item["status"], item["status"])
    return f"- {who}{period} ({item['requested_days']:g} ngày): **{status}**"


def run_hr_turn(
    *,
    db: Session,
    user: User,
    agent: AIAgent,
    role_code_upper: str,
    message: str,
    thread_id: str | None,
    response_data: Dict[str, Any],
    hr_intent_override: str | None,
    leave_draft: dict[str, Any] | None,
    leave_cancel_request: bool,
    on_llm_usage: UsageReporter | None,
    tool_arguments: HRToolArguments | None = None,
) -> Dict[str, Any]:
    """Dispatch an HR turn to the capability its intent names.

    `tool_arguments` is set when a gateway tool call runs the capability: its arguments
    were already chosen, so no branch spends an LLM call or a keyword rule reading them.
    """
    hr_intent = hr_intent_override or _classify_hr_intent(message)
    if leave_draft is None and hr_intent_override is None:
        leave_draft = _load_leave_draft(db, user, thread_id)
    normalized_message = _normalize_intent_text(message)

    def read_lookup(intent: str) -> LookupArguments | None:
        if tool_arguments is not None:
            return tool_arguments.lookup or LookupArguments()
        return _read_lookup_arguments(
            db, user, role_code_upper=role_code_upper, message=message,
            intent=intent, on_llm_usage=on_llm_usage,
        )

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
        stated_purpose: str | None = None
        if tool_arguments is not None:
            stated_purpose = tool_arguments.purpose
        elif any(marker in normalized_message for marker in ("gia han hop dong", "contract renewal")):
            stated_purpose = "CONTRACT_RENEWAL"
        elif any(marker in normalized_message for marker in ("danh gia cuoi nam", "danh gia hieu suat")):
            stated_purpose = "PERFORMANCE_REVIEW"
        elif "onboarding" in normalized_message:
            stated_purpose = "ONBOARDING"
        elif any(marker in normalized_message for marker in ("xu ly bang luong", "payroll")):
            stated_purpose = "PAYROLL_PROCESSING"
        if stated_purpose not in PROFILE_PURPOSE_SECTIONS:
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
        purpose, requested_sections = stated_purpose, PROFILE_PURPOSE_SECTIONS[stated_purpose]
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
        export_format, directory_type = (
            (tool_arguments.export_format, tool_arguments.directory_type)
            if tool_arguments is not None else _parse_hr_export_request(message)
        )
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

    if hr_intent in {"MANAGER_DIRECTORY", "EMPLOYEE_DIRECTORY"}:
        _require_tool(agent, "query_company_users_sql")
        managers_only = hr_intent == "MANAGER_DIRECTORY"
        entity_label = "quản lý" if managers_only else "nhân viên"
        arguments = read_lookup(hr_intent)
        if arguments is not None:
            departments = arguments.departments
            named_a_department = (
                bool(departments) or arguments.unmatched_department is not None
            )
        else:
            departments, named_a_department = _resolve_requested_departments(
                db, user, message
            )
        # Listing everybody under a heading that says "phòng kế toán" is worse than
        # answering nothing, so an unrecognised department stops here.
        if named_a_department and not departments:
            response_data["reply"] = _unknown_department_reply(db, user)
            return response_data

        directory = query_company_users_sql(
            db,
            actor=user,
            departments=departments or None,
            roles=list(supervisory_role_names(db, user.tenant_id)) if managers_only else None,
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
                **({"directory": "managers"} if managers_only else {"query": "*"}),
                "departments": list(departments),
                "scope": scope,
                "requested_sections": ["BASIC"],
                "purpose": "DIRECTORY_LOOKUP",
            },
            "result_count": len(items),
        })
        total_count = directory.get("total_count", len(items))
        department_clause = (
            f" thuộc phòng {_department_filter_label(db, user, departments)}"
            if departments else ""
        )
        response_data["reply"] = (
            f"Tôi tìm thấy **{total_count} {entity_label}**{department_clause} "
            f"trong phạm vi **{scope}** bạn được phép xem."
        )
        response_data["hr_card"] = {
            "type": "EMPLOYEE_SEARCH",
            **({"directory_type": "MANAGERS"} if managers_only else {}),
            "scope": scope,
            "department_filter": list(departments),
            "total_count": total_count,
            "items": items,
        }
        return response_data

    if hr_intent == "EMPLOYEE_SEARCH":
        _require_tool(agent, "query_company_users_sql")
        arguments = read_lookup(hr_intent)
        # The keyword extractor only finds a name after a fixed opening phrase, so
        # "Phạm Văn Tech là ai?" searched for nobody. It stays as the fallback only.
        search_term = (
            (arguments.person or "") if arguments is not None
            else _extract_employee_search_term(message)
        )
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
        if tool_arguments is not None:
            # The conversation is the draft: the model gathered these over the turns.
            slots = {
                field: (tool_arguments.leave_slots or {}).get(field)
                for field in ("start_date", "end_date", "reason")
            }
        else:
            reference_date, timezone_name = _leave_date_context(db, user)
            slots = _extract_leave_slots_with_llm(
                message,
                leave_draft,
                reference_date=reference_date,
                timezone_name=timezone_name,
                # Falls back to metering here when the caller did not supply a reporter,
                # so a direct call to this function still records what it spends.
                on_usage=on_llm_usage or _hr_llm_usage_recorder(db, user),
                prompts=resolve_prompt_overlay(db, user.tenant_id, role_code_upper),
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
        about_someone_else = (
            tool_arguments.about_someone_else if tool_arguments is not None
            else _leave_balance_names_another_person(message)
        )
        if about_someone_else:
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

    if hr_intent == "EMPLOYEE_LEAVE_STATUS_COUNT":
        _require_tool(agent, "list_leave_requests")
        arguments = read_lookup(hr_intent)
        today, _timezone = _leave_date_context(db, user)
        start = (
            date.fromisoformat(arguments.start_date)
            if arguments and arguments.start_date else today
        )
        end = (
            date.fromisoformat(arguments.end_date)
            if arguments and arguments.end_date else start
        )
        end = min(end, start + timedelta(days=LEAVE_CALENDAR_MAX_DAYS - 1))
        departments = arguments.departments if arguments else ()
        if arguments and arguments.unmatched_department and not departments:
            response_data["reply"] = _unknown_department_reply(db, user)
            return response_data
        visible, scope = leave_scope(db, user)
        result = list_leave_requests_in_scope(
            db,
            user,
            employee_ids=visible,
            statuses={"APPROVED", "WAITING"},
            start_date=start,
            end_date=end,
            departments=departments,
            limit=100,
        )
        items = result["items"]
        on_leave = {item["employee"]["id"] for item in items if item["status"] == "APPROVED"}
        waiting = [item for item in items if item["status"] == "WAITING"]
        tool_input = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "departments": list(departments),
            "scope": scope,
        }
        response_data["tools_executed"].append({
            "tool_name": "list_leave_requests",
            "input": tool_input,
            "result_count": len(items),
        })
        log_audit_action(
            db, user.tenant_id, "HR", "list_leave_requests", tool_input,
            {"count": result["total_count"]},
        )
        department_clause = (
            f" ở phòng {_department_filter_label(db, user, departments)}" if departments else ""
        )
        count_prefix = "ít nhất " if result["total_count"] > len(items) else ""
        period = _period_label(start, end, today)
        reply = (
            f"{period[0].upper()}{period[1:]}{department_clause} có "
            f"{count_prefix}**{len(on_leave)} người nghỉ** (đơn đã duyệt)"
        )
        if waiting:
            reply += f" và **{len(waiting)} đơn** đang chờ duyệt"
        reply += f", trong phạm vi **{scope}** bạn được phép xem."
        if scope == "SELF":
            reply += (
                " Chức vụ của bạn chưa được cấp quyền xem dữ liệu nghỉ phép của người "
                "khác, nên kết quả chỉ gồm đơn của chính bạn."
            )
        response_data["reply"] = reply
        response_data["hr_card"] = {
            "type": "LEAVE_CALENDAR",
            "scope": scope,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "department_filter": list(departments),
            "on_leave_count": len(on_leave),
            "total_count": result["total_count"],
            "items": items,
        }
        return response_data

    if hr_intent == "LEAVE_REQUEST_STATUS":
        _require_tool(agent, "list_leave_requests")
        arguments = read_lookup(hr_intent)
        whose = (arguments.whose if arguments else None) or "SELF"
        team = whose == "TEAM"
        departments = arguments.departments if arguments and team else ()
        if team:
            visible, scope = leave_scope(db, user)
            visible = visible - {user.id}
            if not visible:
                response_data["reply"] = (
                    "Chức vụ của bạn chưa được cấp quyền xem đơn nghỉ của người khác."
                    if scope == "SELF" else
                    "Hiện chưa có nhân viên nào thuộc phạm vi quản lý của bạn."
                )
                return response_data
        else:
            visible, scope = {user.id}, "SELF"
        start = (
            date.fromisoformat(arguments.start_date)
            if arguments and arguments.start_date else None
        )
        end = (
            date.fromisoformat(arguments.end_date)
            if arguments and arguments.end_date else None
        )
        result = list_leave_requests_in_scope(
            db,
            user,
            employee_ids=visible,
            start_date=start,
            end_date=end,
            departments=departments,
            limit=10,
        )
        items = result["items"]
        tool_input = {
            "whose": whose,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "departments": list(departments),
            "scope": scope,
        }
        response_data["tools_executed"].append({
            "tool_name": "list_leave_requests",
            "input": tool_input,
            "result_count": len(items),
        })
        log_audit_action(
            db, user.tenant_id, "HR", "list_leave_requests", tool_input,
            {"count": result["total_count"]},
        )
        if not items:
            response_data["reply"] = (
                "Không có đơn nghỉ nào của nhân viên trong phạm vi bạn quản lý."
                if team else "Bạn chưa có đơn nghỉ nào."
            )
        else:
            shown = (
                f" (hiển thị {len(items)} đơn gần nhất)"
                if result["total_count"] > len(items) else ""
            )
            heading = (
                f"Có **{result['total_count']} đơn nghỉ** của nhân viên trong phạm vi "
                f"**{scope}**{shown}:"
                if team else f"Bạn có **{result['total_count']} đơn nghỉ**{shown}:"
            )
            response_data["reply"] = heading + "\n" + "\n".join(
                _leave_request_line(item, with_name=team) for item in items
            )
        response_data["hr_card"] = {
            "type": "LEAVE_REQUESTS",
            "whose": whose,
            "scope": scope,
            "total_count": result["total_count"],
            "items": items,
        }
        return response_data

    if hr_intent == "ACTION_LEAVE_CANCEL":
        _require_tool(agent, "cancel_leave_request")
        arguments = read_lookup(hr_intent)
        today, _timezone = _leave_date_context(db, user)
        # Without a date, the candidates are the requests that have not ended yet.
        start = (
            date.fromisoformat(arguments.start_date)
            if arguments and arguments.start_date else today
        )
        end = (
            date.fromisoformat(arguments.end_date)
            if arguments and arguments.end_date else None
        )
        own = list_leave_requests_in_scope(
            db,
            user,
            employee_ids={user.id},
            statuses={"WAITING", "APPROVED"},
            start_date=start,
            end_date=end,
            limit=20,
        )["items"]
        waiting = [item for item in own if item["status"] == "WAITING"]
        if not waiting:
            approved = [item for item in own if item["status"] == "APPROVED"]
            response_data["reply"] = (
                "Đơn nghỉ này đã được duyệt nên tôi không tự rút được: ngày phép đã được "
                "trừ và đã lên lịch. Vui lòng liên hệ người duyệt hoặc HR để hủy."
                if approved else
                "Tôi không tìm thấy đơn nghỉ nào của bạn đang chờ duyệt để rút."
            )
            if approved:
                response_data["hr_card"] = {
                    "type": "LEAVE_REQUESTS", "whose": "SELF", "scope": "SELF",
                    "total_count": len(approved), "items": approved,
                }
            return response_data
        if len(waiting) > 1:
            # Withdrawing is not undone by asking again, so an ambiguous turn asks
            # which request it means instead of picking one.
            response_data["reply"] = (
                f"Bạn có **{len(waiting)} đơn** đang chờ duyệt. Bạn muốn rút đơn nào? "
                "Hãy cho tôi biết ngày nghỉ của đơn đó.\n"
                + "\n".join(_leave_request_line(item, with_name=False) for item in waiting)
            )
            response_data["hr_card"] = {
                "type": "LEAVE_REQUESTS", "whose": "SELF", "scope": "SELF",
                "total_count": len(waiting), "items": waiting,
            }
            return response_data

        target = waiting[0]
        try:
            record = withdraw_leave_request(db, user, uuid.UUID(target["id"]))
        except HTTPException as exc:
            response_data["reply"] = (
                "Đơn này vừa được xử lý nên không rút được nữa."
                if exc.status_code == 409 else "Không tìm thấy đơn nghỉ cần rút."
            )
            return response_data
        response_data["tools_executed"].append({
            "tool_name": "cancel_leave_request",
            "input": {"leave_request_id": target["id"]},
            "result": {"status": record.status},
        })
        response_data["reply"] = (
            f"Đã rút đơn nghỉ {_period_label(record.start_date, record.end_date, today)} "
            f"({float(record.requested_days):g} ngày). Số ngày phép giữ chỗ cho đơn này đã "
            "được trả lại và người duyệt đã được báo."
        )
        response_data["hr_card"] = {
            "type": "LEAVE_REQUESTS", "whose": "SELF", "scope": "SELF",
            "total_count": 1, "items": [{**target, "status": record.status}],
        }
        return response_data

    if hr_intent == "UNKNOWN":
        response_data["reply"] = (
            "Tôi chưa xác định rõ nghiệp vụ HR cần thực hiện. Bạn có thể yêu cầu, ví dụ: "
            "**tìm nhân viên An**, **liệt kê các quản lý**, **xem ngày phép của tôi**, "
            "**hỏi chính sách nghỉ phép** hoặc **xuất danh sách nhân viên Excel**."
        )
        return response_data

    if hr_intent == "POLICY_QUERY":
        _require_tool(agent, "rag_search")
        search_results = hybrid_search_documents(
            db,
            user.tenant_id,
            message,
            collections=None,
            agent_access=agent_scope(agent),
            # HR policy questions search the HR shelf whatever the asker's reach.
            **{**user_search_scope(db, user), "department": "HR"},
        )
        response_data["tools_executed"].append({
            "tool_name": "rag_search",
            "input": {"query": message},
            "result_count": len(search_results),
        })
        log_audit_action(
            db,
            user.tenant_id,
            "HR",
            "rag_search",
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
                f"Dựa trên quy định HR của công ty:\n\n"
                f"{top_result['content']}\n\n"
                f"{top_result['citation_tag']}"
                f"{policy_date_line}"
            )
        else:
            response_data["reply"] = (
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
