"""The Finance agent's tool grants, and how an existing agent row catches up with them.

Finance runs only on the graph, so its grants are gateway tool names. They arrive in
stages; each stage is a version, and a row is upgraded once per version by adding only
the tools that version introduced. A tool an administrator later removes therefore stays
removed -- re-adding the whole default set on every start would undo their choice.

Versions start above 100 so they never compare against the HR numbering that the generic
seeding path stamps on every other role.

Import-free data, like ``app.core.hr_capabilities``: seeding, the configuration API and
the chat dispatch all read it.
"""

from __future__ import annotations

from typing import Any

# version -> gateway tools that version added to the Finance agent's default grant.
FINANCE_TOOL_INTRODUCTIONS: dict[int, tuple[str, ...]] = {
    101: ("rag_search",),
    102: ("lookup_invoices",),
    103: ("propose_journal_entry",),
    104: (
        "get_account_balance",
        "get_trial_balance",
        "get_ledger_detail",
        "budget_vs_actual",
        "ar_ap_aging",
        "payment_schedule",
    ),
    105: ("draft_payment_voucher", "draft_payment_reminder"),
}

FINANCE_CONFIGURATION_VERSION: int = max(FINANCE_TOOL_INTRODUCTIONS)

# What the placeholder agent was seeded with. None of it served finance work: the first a
# capability nothing dispatched, the rest generic tools (AI spend, free-form tasks and
# approvals) the Finance agent now covers with typed tools of its own.
FINANCE_RETIRED_TOOLS: frozenset[str] = frozenset({
    "reconcile_po_db",
    "expense_lookup",
    "create_task",
    "submit_approval_request",
})


def finance_default_tools() -> tuple[str, ...]:
    return tuple(sorted({
        name for names in FINANCE_TOOL_INTRODUCTIONS.values() for name in names
    }))


def configuration_version_for(role_code: str, hr_version: int) -> int:
    """The version to stamp on a row an administrator just configured."""
    return FINANCE_CONFIGURATION_VERSION if role_code.upper() == "FINANCE" else hr_version


def upgrade_finance_grants(agent: Any) -> bool:
    """Bring a Finance row's grants up to the current version. Returns whether it changed."""
    if str(agent.role_code or "").upper() != "FINANCE":
        return False
    version = agent.configuration_version or 1
    if version >= FINANCE_CONFIGURATION_VERSION:
        return False
    denied = set(agent.disallowed_actions or [])
    tools = set(agent.tools_access or [])
    allowed = set(agent.allowed_actions or [])
    if version < min(FINANCE_TOOL_INTRODUCTIONS):
        tools -= FINANCE_RETIRED_TOOLS
        allowed -= FINANCE_RETIRED_TOOLS
        denied -= FINANCE_RETIRED_TOOLS
    for introduced_in, names in FINANCE_TOOL_INTRODUCTIONS.items():
        if introduced_in > version:
            additions = set(names) - denied
            tools |= additions
            # An empty allow-list means "everything in tools_access"; filling it here
            # would narrow the agent to just the new names.
            if allowed:
                allowed |= additions
    agent.tools_access = sorted(tools)
    agent.allowed_actions = sorted(allowed)
    agent.disallowed_actions = sorted(denied)
    agent.configuration_version = FINANCE_CONFIGURATION_VERSION
    return True
