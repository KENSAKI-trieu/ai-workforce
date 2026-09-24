"""One HR turn once its intent is known: dispatch to the governed HR capability."""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, Any

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from app.models.models import AIAgent, AgentWorkflow, User, WorkflowApproval
from app.domains.hr.hr_service import (
    can_manage_hr,
    can_approve_hr_request,
    create_onboarding_case,
    hr_scope_label,
    query_leave_balance,
    request_leave,
)
from app.domains.hr.hr_employee_tools import (
    list_contract_status_summaries,
    query_company_users_sql,
)
from app.domains.platform.position_service import supervisory_role_names
from app.domains.knowledge.rag_service import hybrid_search_documents
from app.domains.platform.audit_service import log_audit_action
from app.plugins.resolver import resolve_prompt_overlay
from app.agents.hr.llm_flow import UsageReporter
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
) -> Dict[str, Any]:
    """Dispatch an HR turn to the capability its intent names."""
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

    if hr_intent in {"MANAGER_DIRECTORY", "EMPLOYEE_DIRECTORY"}:
        _require_tool(agent, "query_company_users_sql")
        managers_only = hr_intent == "MANAGER_DIRECTORY"
        entity_label = "quản lý" if managers_only else "nhân viên"
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
        _require_tool(agent, "rag_search")
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
