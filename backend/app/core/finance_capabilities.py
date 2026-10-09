"""The Finance (and Marketing) agents' tool grants, and how an existing row catches up.

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
    106: ("get_account_trend", "get_expense_breakdown"),
    107: ("list_spreadsheets", "analyze_spreadsheet"),
    108: ("get_income_statement", "list_finance_drafts"),
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


# The Marketing agent's grants, versioned the same way from 201. It has no deterministic
# chat either: the campaign pipeline runs on its own page and the chat on the graph.
MARKETING_TOOL_INTRODUCTIONS: dict[int, tuple[str, ...]] = {
    201: ("rag_search",),
    202: ("start_marketing_campaign",),
}

# role -> (version -> tools that version added, tools retired before the first version)
VERSIONED_ROLE_TOOLS: dict[str, tuple[dict[int, tuple[str, ...]], frozenset[str]]] = {
    "FINANCE": (FINANCE_TOOL_INTRODUCTIONS, FINANCE_RETIRED_TOOLS),
    "MARKETING": (MARKETING_TOOL_INTRODUCTIONS, frozenset()),
}


def versioned_default_tools(role_code: str) -> tuple[str, ...]:
    introductions, _ = VERSIONED_ROLE_TOOLS[role_code.upper()]
    return tuple(sorted({name for names in introductions.values() for name in names}))


def configuration_version_for(role_code: str, hr_version: int) -> int:
    """The version to stamp on a row an administrator just configured."""
    versioned = VERSIONED_ROLE_TOOLS.get(role_code.upper())
    return max(versioned[0]) if versioned else hr_version


def upgrade_versioned_grants(agent: Any) -> bool:
    """Bring a Finance or Marketing row's grants up to its role's current version.

    Returns whether it changed. Only the tools of versions newer than the row's are added,
    so a tool an administrator removed stays removed.
    """
    versioned = VERSIONED_ROLE_TOOLS.get(str(agent.role_code or "").upper())
    if versioned is None:
        return False
    introductions, retired = versioned
    current = max(introductions)
    version = agent.configuration_version or 1
    if version >= current:
        return False
    denied = set(agent.disallowed_actions or [])
    tools = set(agent.tools_access or [])
    allowed = set(agent.allowed_actions or [])
    if version < min(introductions):
        tools -= retired
        allowed -= retired
        denied -= retired
    for introduced_in, names in introductions.items():
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
    agent.configuration_version = current
    return True


def upgrade_finance_grants(agent: Any) -> bool:
    """Bring a Finance row's grants up to the current version. Returns whether it changed."""
    if str(agent.role_code or "").upper() != "FINANCE":
        return False
    return upgrade_versioned_grants(agent)
