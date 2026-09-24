"""Whether an AI Employee may use a capability, as the chat executor checks it."""

from __future__ import annotations

from fastapi import HTTPException
from app.models.models import AIAgent
from app.core.hr_capabilities import HR_CONFIGURATION_VERSION, HR_CORE_TOOLS, HR_RETIRED_TOOLS
from app.core.tool_permissions import grant_decision
from app.plugins.resolver import EMPTY_RESTRICTION, SkillRestriction


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


# Where a tenant's plugin narrowing is parked on the loaded agent row. It is a plain
# instance attribute and deliberately not a mapped column: writing the narrowed lists
# back to `ai_agents` would survive uninstalling the package, permanently stripping the
# tenant of tools the package had only meant to hide while it was installed.
_PLUGIN_RESTRICTION_ATTR = "_plugin_skill_restriction"


def _attach_plugin_restriction(agent: AIAgent, restriction: SkillRestriction) -> None:
    setattr(agent, _PLUGIN_RESTRICTION_ATTR, restriction)


def _plugin_restriction(agent: AIAgent) -> SkillRestriction:
    """The narrowing in force for this row; unrestricted when nothing was attached."""
    return getattr(agent, _PLUGIN_RESTRICTION_ATTR, EMPTY_RESTRICTION)


def _grant_decision(agent: AIAgent, tool_name: str) -> str | None:
    _repair_hr_agent_capabilities(agent)
    return grant_decision(
        tool_name,
        tools_access=agent.tools_access,
        allowed_actions=agent.allowed_actions,
        disallowed_actions=agent.disallowed_actions,
        restriction=_plugin_restriction(agent),
    )


def _require_tool(agent: AIAgent, tool_name: str) -> None:
    decision = _grant_decision(agent, tool_name)
    if decision == "DENIED":
        raise HTTPException(
            status_code=403,
            detail=f"AI Employee is explicitly forbidden from action '{tool_name}'",
        )
    if decision == "NOT_GRANTED":
        raise HTTPException(
            status_code=403,
            detail=f"AI Employee is not allowed to use tool '{tool_name}'",
        )
    # Phrased differently so an operator can tell a plugin withdrawal apart from a
    # permission the agent never had.
    if decision == "PLUGIN":
        raise HTTPException(
            status_code=403,
            detail=(
                f"An installed plugin withdraws tool '{tool_name}' "
                "from this AI Employee"
            ),
        )


def _can_use_tool(agent: AIAgent, tool_name: str) -> bool:
    """Return whether an optional tool is enabled by the agent configuration."""
    return _grant_decision(agent, tool_name) is None
