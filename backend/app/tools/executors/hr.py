"""HR tools."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from app.models.models import AIAgent, ChatConversation, ChatMessage, User
from app.core.hr_capabilities import HR_PRIVATE_REPLY_TOOLS
from app.domains.hr.hr_employee_tools import get_employee_sections
from app.domains.hr.hr_service import query_leave_balance
from app.agents.access import ToolNotPermitted, refusal_reply
from app.agents.hr.flow import HRToolArguments, run_hr_turn
from app.agents.hr.intent import _departments_for_names
from app.agents.hr.leave import _load_leave_draft
from app.agents.hr.llm_flow import LookupArguments
from app.agents.tool_outputs import store_tool_output
from app.tools.registry import ToolContext
from app.tools.schemas import (
    EmployeeLookupInput,
    HRCancelLeaveInput,
    HRDirectoryInput,
    HREmployeeProfileInput,
    HRExportInput,
    HRLeaveBalanceInput,
    HRLeaveRequestsInput,
    HRNoArgumentsInput,
    HROnboardingInput,
    HRRequestLeaveInput,
    LeaveLookupInput,
    TenantToolInput,
)


def lookup_employee(context: ToolContext, request: EmployeeLookupInput) -> dict[str, Any]:
    return get_employee_sections(
        context.db,
        actor=context.actor,
        employee_id=request.employee_id,
        requested_sections=request.sections,
        purpose=request.purpose,
        tool_name="employee_lookup",
    )


def lookup_leave(context: ToolContext, request: LeaveLookupInput) -> dict[str, Any]:
    db = context.db
    actor = context.actor
    employee_id = request.employee_id or actor.id
    access = get_employee_sections(
        db,
        actor=actor,
        employee_id=employee_id,
        requested_sections=["LEAVE"],
        purpose="SELF_SERVICE" if employee_id == actor.id else "LEAVE_MANAGEMENT",
        tool_name="leave_lookup",
    )
    employee = db.query(User).filter(
        User.id == employee_id,
        User.tenant_id == actor.tenant_id,
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "employee_id": str(employee.id),
        "request_id": access["request_id"],
        "scope": access["scope"],
        "balance": query_leave_balance(db, employee, request.year),
    }


# --------------------------------------------------------------------------- HR agent
# The LangGraph HR agent's tools. Each runs the deterministic HR chat's own branch for its
# capability -- grant check, scope, purpose limitation, audit -- with the arguments the
# model chose, so the two engines cannot drift apart. Every one is terminal: its reply is
# the answer, written by the backend. A reply carrying personal data is kept here and only
# a reference travels back through the graph (see AgentToolOutput).

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
# How many of the user's messages an email is looked for in, newest first: the address
# often comes a turn before the purpose that unlocks it.
_EMAIL_LOOKBACK = 3

# Said to the AI service in place of a private reply. It is never shown while the stored
# reply exists; it is what the graph keeps and what a lost reference would show.
PRIVATE_REPLY_PLACEHOLDER = "Kết quả tra cứu nhân sự đã được hiển thị cho người dùng."


def _user_messages(context: ToolContext, request: TenantToolInput, limit: int) -> list[str]:
    """The user's own latest messages in their own conversation, newest first."""
    conversation_id = request.audit.conversation_id
    if conversation_id is None:
        return []
    actor = context.actor
    # The id is caller trace data, so ownership is checked against the actor.
    conversation = context.db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.tenant_id == actor.tenant_id,
        ChatConversation.user_id == actor.id,
    ).first()
    if conversation is None:
        return []
    rows = context.db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender == "USER",
    ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
    return [row.content or "" for row in rows]


def _message_with_email(messages: list[str]) -> str:
    """The newest of the user's messages naming an email, else their latest message."""
    return next((text for text in messages if _EMAIL.search(text)), messages[0] if messages else "")


def _empty_response(agent: AIAgent) -> dict[str, Any]:
    return {
        "agent_name": agent.name,
        "agent_role": agent.role_code,
        "avatar_emoji": agent.avatar_emoji,
        "reply": "",
        "citations": [],
        "tools_executed": [],
        "approval_card": None,
        "hr_card": None,
    }


def _run_hr_capability(
    context: ToolContext,
    request: TenantToolInput,
    *,
    tool_name: str,
    intent: str,
    arguments: HRToolArguments,
    message: str | None = None,
    abandon_leave_draft: bool = False,
) -> dict[str, Any]:
    agent = context.agent
    if agent is None:
        raise HTTPException(status_code=403, detail="HR tools run for an AI Employee")
    if message is None:
        message = next(iter(_user_messages(context, request, 1)), "")
    response = _empty_response(agent)
    thread_id = str(request.audit.conversation_id) if request.audit.conversation_id else None
    leave_draft = None
    if abandon_leave_draft:
        # The open draft, so the card it left reads CANCELLED and the deterministic chat
        # never resumes it; a draft only the conversation held is cancelled all the same.
        leave_draft = _load_leave_draft(context.db, context.actor, thread_id) or {
            "type": "LEAVE_REQUEST_DRAFT", "status": "COLLECTING",
        }
    try:
        response = run_hr_turn(
            db=context.db,
            user=context.actor,
            agent=agent,
            role_code_upper=agent.role_code.upper(),
            message=message,
            thread_id=thread_id,
            response_data=response,
            hr_intent_override=intent,
            leave_draft=leave_draft,
            leave_cancel_request=abandon_leave_draft,
            on_llm_usage=None,
            tool_arguments=arguments,
        )
    except ToolNotPermitted as exc:
        response["reply"] = refusal_reply(exc)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        # A role check inside the branch (onboarding is HR's own work), said in words.
        response["reply"] = "Tài khoản của bạn không có quyền thực hiện thao tác này."
    output_id = store_tool_output(
        context.db,
        actor=context.actor,
        conversation_id=request.audit.conversation_id,
        tool_name=tool_name,
        response=response,
    )
    private = tool_name in HR_PRIVATE_REPLY_TOOLS
    return {
        "output_id": str(output_id),
        "reply": PRIVATE_REPLY_PLACEHOLDER if private else response["reply"],
        "created": bool(response.get("approval_card")),
    }


def _lookup(
    context: ToolContext,
    *,
    departments: list[str],
    person: str | None = None,
    start_date: Any = None,
    end_date: Any = None,
    whose: str | None = None,
) -> LookupArguments:
    codes, unmatched = _departments_for_names(context.db, context.actor, departments)
    return LookupArguments(
        person=person,
        departments=codes,
        unmatched_department=unmatched,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        whose=whose,
    )


def hr_directory(context: ToolContext, request: HRDirectoryInput) -> dict[str, Any]:
    person = (request.person or "").strip()
    if "REDACTED" in person.upper():
        person = ""
    messages = _user_messages(context, request, 1)
    if not person:
        # A masked email in the request: the address is in the user's own message.
        email = _EMAIL.search(messages[0]) if messages else None
        person = email.group(0) if email else ""
    if person:
        intent = "EMPLOYEE_SEARCH"
    else:
        intent = "MANAGER_DIRECTORY" if request.managers_only else "EMPLOYEE_DIRECTORY"
    return _run_hr_capability(
        context, request,
        tool_name="query_company_users_sql",
        intent=intent,
        arguments=HRToolArguments(
            lookup=_lookup(context, departments=request.departments, person=person or None),
        ),
        message=messages[0] if messages else "",
    )


def hr_employee_profile(context: ToolContext, request: HREmployeeProfileInput) -> dict[str, Any]:
    if request.employee == "SELF":
        return _run_hr_capability(
            context, request,
            tool_name="get_employee_full_profile",
            intent="SELF_PROFILE",
            arguments=HRToolArguments(),
        )
    return _run_hr_capability(
        context, request,
        tool_name="get_employee_full_profile",
        intent="FULL_PROFILE",
        arguments=HRToolArguments(purpose=request.purpose),
        message=_message_with_email(_user_messages(context, request, _EMAIL_LOOKBACK)),
    )


def _self_service(tool_name: str, intent: str):
    def run(context: ToolContext, request: HRNoArgumentsInput) -> dict[str, Any]:
        return _run_hr_capability(
            context, request, tool_name=tool_name, intent=intent, arguments=HRToolArguments(),
        )

    run.__name__ = f"hr_{tool_name}"
    return run


hr_compensation = _self_service("get_employee_compensation_summary", "SELF_COMPENSATION")
hr_private_profile = _self_service("get_employee_private_profile", "SELF_PRIVATE_PROFILE")
hr_contract = _self_service("get_employee_contract_summary", "SELF_CONTRACT")
hr_contract_expiry = _self_service("get_contract_expiry", "CONTRACT_EXPIRY")
hr_pending_approvals = _self_service("list_pending_hr_approvals", "PENDING_APPROVALS")


def hr_leave_balance(context: ToolContext, request: HRLeaveBalanceInput) -> dict[str, Any]:
    return _run_hr_capability(
        context, request,
        tool_name="query_leave_balance",
        intent="QUERY_LEAVE_BALANCE",
        arguments=HRToolArguments(about_someone_else=bool((request.person or "").strip())),
    )


def hr_request_leave(context: ToolContext, request: HRRequestLeaveInput) -> dict[str, Any]:
    if request.abandon:
        return _run_hr_capability(
            context, request,
            tool_name="request_leave",
            intent="ACTION_LEAVE_REQUEST",
            arguments=HRToolArguments(),
            abandon_leave_draft=True,
        )
    return _run_hr_capability(
        context, request,
        tool_name="request_leave",
        intent="ACTION_LEAVE_REQUEST",
        arguments=HRToolArguments(leave_slots={
            "start_date": request.start_date.isoformat() if request.start_date else None,
            "end_date": request.end_date.isoformat() if request.end_date else None,
            "reason": (request.reason or "").strip() or None,
        }),
    )


def hr_cancel_leave(context: ToolContext, request: HRCancelLeaveInput) -> dict[str, Any]:
    return _run_hr_capability(
        context, request,
        tool_name="cancel_leave_request",
        intent="ACTION_LEAVE_CANCEL",
        arguments=HRToolArguments(lookup=_lookup(
            context, departments=[], start_date=request.start_date, end_date=request.end_date,
        )),
    )


def hr_leave_requests(context: ToolContext, request: HRLeaveRequestsInput) -> dict[str, Any]:
    return _run_hr_capability(
        context, request,
        tool_name="list_leave_requests",
        intent="EMPLOYEE_LEAVE_STATUS_COUNT" if request.view == "WHO_IS_OFF" else "LEAVE_REQUEST_STATUS",
        arguments=HRToolArguments(lookup=_lookup(
            context,
            departments=request.departments,
            start_date=request.start_date,
            end_date=request.end_date,
            whose=request.whose,
        )),
    )


def hr_export(context: ToolContext, request: HRExportInput) -> dict[str, Any]:
    return _run_hr_capability(
        context, request,
        tool_name="export_hr_directory",
        intent="ACTION_EXPORT",
        arguments=HRToolArguments(export_format=request.format, directory_type=request.directory),
    )


def hr_onboarding(context: ToolContext, request: HROnboardingInput) -> dict[str, Any]:
    return _run_hr_capability(
        context, request,
        tool_name="create_onboarding_workflow",
        intent="ACTION_ONBOARDING",
        arguments=HRToolArguments(),
        message=_message_with_email(_user_messages(context, request, _EMAIL_LOOKBACK)),
    )
