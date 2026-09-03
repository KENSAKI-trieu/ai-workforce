"""Single source of truth for the HR agent's capability names.

The HR executor does not use function calling: a "tool" here is a capability name in a
flat allow-list, checked by ``_require_tool`` before a hard-coded intent branch runs. The
names therefore have to agree across four places that used to keep their own copies --
the executor, registration seeding, database seeding, and the configuration API. They
live in ``app.core`` because that is the only package all four already depend on;
importing them from ``app.services`` would close an import cycle through
``app/services/__init__.py``.
"""

from __future__ import annotations

# Every capability the HR executor can actually dispatch. Each name is reachable from a
# `_require_tool` call in `agent_executor._execute_agent_chat_core`, except
# `get_employee_leave_summary`, which is deliberately optional -- see below.
HR_CORE_TOOLS: frozenset[str] = frozenset({
    "hybrid_rag_search",
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

HR_CONFIGURATION_VERSION = 7


def default_hr_tools() -> list[str]:
    """Grants for a freshly seeded HR agent, sorted so seeded rows compare cleanly."""
    return sorted(HR_CORE_TOOLS)
