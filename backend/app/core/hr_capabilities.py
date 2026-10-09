"""Single source of truth for the HR agent's capability names.

The HR executor does not use function calling: a "tool" here is a capability name in a
flat allow-list, checked by ``_require_tool`` before a hard-coded intent branch runs. The
names therefore have to agree across four places that used to keep their own copies --
the executor, registration seeding, database seeding, and the configuration API. They
live in ``app.core`` because that is the only package all four already depend on;
importing them from the domain or agent packages would close an import cycle.
"""

from __future__ import annotations

# Every capability the HR executor can actually dispatch. Each name is reachable from a
# `_require_tool` call in `app/agents/hr/flow.py` (run_hr_turn), except
# `get_employee_leave_summary`, which is deliberately optional -- see below.
HR_CORE_TOOLS: frozenset[str] = frozenset({
    # Knowledge search; `hybrid_rag_search` before the tool names were unified.
    "rag_search",
    "get_employee_private_profile",
    "get_employee_contract_summary",
    "get_employee_compensation_summary",
    # Probed with `_can_use_tool`, never `_require_tool`: withholding this grant narrows
    # the self-profile card to its BASIC section instead of refusing the whole request.
    "get_employee_leave_summary",
    "get_employee_full_profile",
    "query_company_users_sql",
    "query_leave_balance",
    "request_leave",
    "create_onboarding_workflow",
    "get_contract_expiry",
    "list_pending_hr_approvals",
    "export_hr_directory",
    "list_leave_requests",
    "cancel_leave_request",
})

# Capabilities added after version 8. An agent already on version 8 receives them once,
# unless an operator explicitly denied them; nothing else on that row is touched, so a tool
# the operator removed stays removed.
HR_TOOLS_ADDED_IN_VERSION_9: frozenset[str] = frozenset({
    "list_leave_requests",
    "cancel_leave_request",
})

# Granted by older configurations but reachable from nowhere: no branch dispatches them
# and the internal tool gateway's registry does not define them either. Revoked by the
# capability migration rather than left as a grant with no meaning.
HR_RETIRED_TOOLS: frozenset[str] = frozenset({
    "create_hr_task",
    "send_hr_notification",
    # Seeded and advertised since the first release, but no `_require_tool` call ever
    # named it; the self-profile branch asks for `get_employee_full_profile` instead.
    "get_employee_basic_profile",
})

# Also the stamp init_db uses to decide whether a non-HR agent row still needs the gateway
# tool grants composed into DEFAULT_AGENT_TOOLS. Version 8 exists for that repair: rows
# created by the signup path before it carried capability names only. Version 9 adds the
# leave-request tools to HR only.
HR_CONFIGURATION_VERSION = 9

# Rows below this were seeded before every default grant existed, and init_db re-adds the
# defaults to them. Rows at or above it carry an operator's choices, which a later version
# bump must not overwrite: each newer version adds only its own tools, in the executor.
DEFAULT_GRANTS_COMPLETE_VERSION = 8


# What the user is told a capability is when it has been switched off for the agent.
HR_CAPABILITY_LABELS: dict[str, str] = {
    "rag_search": "tra cứu kho tri thức",
    "get_employee_private_profile": "xem thông tin cá nhân nhân viên",
    "get_employee_contract_summary": "xem tóm tắt hợp đồng lao động",
    "get_employee_compensation_summary": "xem thông tin lương",
    "get_employee_leave_summary": "xem tóm tắt nghỉ phép",
    "get_employee_full_profile": "xem hồ sơ nhân viên",
    "query_company_users_sql": "tra cứu danh bạ công ty",
    "query_leave_balance": "tra cứu quỹ phép",
    "request_leave": "tạo đơn nghỉ phép",
    "create_onboarding_workflow": "khởi tạo quy trình onboarding",
    "get_contract_expiry": "theo dõi hạn hợp đồng",
    "list_pending_hr_approvals": "xem danh sách chờ duyệt",
    "export_hr_directory": "xuất danh bạ nhân sự",
    "list_leave_requests": "xem đơn nghỉ và lịch nghỉ",
    "cancel_leave_request": "rút đơn nghỉ phép",
}
assert set(HR_CAPABILITY_LABELS) == HR_CORE_TOOLS, "every HR capability needs a user-facing label"


def default_hr_tools() -> list[str]:
    """Grants for a freshly seeded HR agent, sorted so seeded rows compare cleanly."""
    return sorted(HR_CORE_TOOLS)
