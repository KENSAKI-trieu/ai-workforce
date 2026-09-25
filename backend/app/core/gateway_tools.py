"""Single source of truth for the internal tool gateway's grant names.

Two different name spaces share the ``ai_agents.tools_access`` column:

* capability names dispatched by the deterministic executors -- see
  ``app.core.hr_capabilities`` for the HR set, and ``DEFAULT_AGENT_TOOLS`` for the rest;
* the gateway tool names registered in ``app.tools.registry``, used when a role is routed
  through LangGraph (``LANGGRAPH_ENABLED=true``).

Both enforcement points test plain membership in that one column -- the AI service filters
its LangChain tools by it, and ``_enforce_agent_configuration`` re-checks it server-side --
so a role whose grants hold only capability names ends up with an empty gateway toolset and
answers with no retrieval and no error. Keeping the gateway grants here, and composing them
into every default map, is what stops that from happening per-role by accident.

This module is deliberately import-free data. ``app.tools.registry`` executes
``build_tool_registry()`` at import time, which imports the domain services and the chat
flows; importing it from ``app.domains.platform.auth_service`` would close a cycle.
``tests/test_tool_gateway.py::test_gateway_grant_names_exist_in_registry`` asserts these
names against the registry instead, so drift fails a test rather than a production request.
"""

from __future__ import annotations

# Every tool name registered in app/tools/registry.py, with the operator-facing description
# the configuration UI shows. Names missing from here are rejected as "Unknown tools" by
# PATCH /agents/{role_code}, which used to make seeded agents unsaveable.
GATEWAY_TOOL_DESCRIPTIONS: dict[str, str] = {
    "rag_search": "Tìm kiếm kho tri thức qua tool gateway, có kiểm tra ACL phòng ban và vai trò.",
    "employee_lookup": "Đọc hồ sơ nhân viên qua gateway, lọc theo chính sách HR và purpose.",
    "leave_lookup": "Tra cứu quỹ phép qua gateway, giới hạn theo phạm vi quản lý.",
    "create_task": "Tạo task trong tenant qua gateway, có khóa idempotency.",
    "expense_lookup": "Đọc chi phí và mức sử dụng AI qua gateway.",
    "generate_legal_document": "Tạo bản nháp văn bản pháp lý DOCX/PDF chờ người duyệt.",
    "submit_approval_request": "Tạo cổng phê duyệt cho hành động cần con người xác nhận.",
    # Shares its name with the Legal capability on purpose: one grant switches contract
    # review on or off in the deterministic chat and through LangGraph alike.
    "audit_contract_risk": "Rà soát rủi ro hợp đồng.",
}

GATEWAY_TOOLS: frozenset[str] = frozenset(GATEWAY_TOOL_DESCRIPTIONS)

# What a user is told a tool is when the graph refuses a request because the organisation
# turned it off. Short, because it is read inside a sentence; the descriptions above are
# written for administrators choosing grants.
GATEWAY_TOOL_LABELS: dict[str, str] = {
    "rag_search": "tra cứu kho tri thức",
    "employee_lookup": "tra cứu hồ sơ nhân viên",
    "leave_lookup": "tra cứu quỹ phép",
    "create_task": "tạo task",
    "expense_lookup": "tra cứu chi phí",
    "generate_legal_document": "soạn văn bản pháp lý",
    "submit_approval_request": "gửi yêu cầu phê duyệt",
    "audit_contract_risk": "rà soát rủi ro hợp đồng",
}
assert set(GATEWAY_TOOL_LABELS) == GATEWAY_TOOLS, "every gateway tool needs a user-facing label"

# Default gateway grants per agent role, kept at least privilege: a tool absent here can
# still be enabled per tenant through the configuration API, and the gateway's own
# role/department ACL applies on top of whatever is granted.
#
# `employee_lookup` and `leave_lookup` are withheld from CEO on purpose even though
# DOMAIN_POLICIES["CEO"] lists them: those tools take a `purpose` argument that widens the
# sections HR policy will release, and on this path the argument is chosen by the model.
# Granting them is an explicit tenant decision, not a default.
#
# HR gets knowledge search only. Its other capabilities are HR-specific names checked by
# its own deterministic executor; search became the same `rag_search` everywhere when the
# tool names were unified. See apps/ai-service/app/agents/hr/tools.py before granting HR
# any other gateway tool.
GATEWAY_TOOL_GRANTS: dict[str, tuple[str, ...]] = {
    "CEO": (
        "rag_search",
        "create_task",
        "expense_lookup",
        "generate_legal_document",
        "submit_approval_request",
    ),
    "HR": ("rag_search",),
    "LEGAL": (
        "rag_search",
        "audit_contract_risk",
        "generate_legal_document",
        "submit_approval_request",
    ),
    "IT": ("rag_search", "create_task", "submit_approval_request"),
    "FINANCE": ("rag_search", "expense_lookup", "create_task", "submit_approval_request"),
    "SALES": ("rag_search", "create_task", "submit_approval_request"),
    "KNOWLEDGE": ("rag_search",),
}


def gateway_grants(role_code: str) -> tuple[str, ...]:
    """Gateway tool names granted to a role by default."""
    return GATEWAY_TOOL_GRANTS.get(role_code.upper(), ())


def disabled_gateway_tools(
    role_code: str, tools_access: list[str] | None, allowed: list[str]
) -> list[dict[str, str]]:
    """Gateway tools this agent would have but its organisation turned off.

    "Would have" is the role's default grants plus whatever `tools_access` lists, so a tool
    dropped from `tools_access` altogether still counts as switched off rather than as a
    capability the agent never had. `allowed` is the effective grant after every narrowing:
    `allowed_actions`, `disallowed_actions` and the tenant's plugins.
    """
    from app.core.tool_permissions import canonical_tool_names

    candidates = set(gateway_grants(role_code)) | set(canonical_tool_names(tools_access))
    return [
        {"name": name, "label": GATEWAY_TOOL_LABELS[name]}
        for name in sorted((candidates & GATEWAY_TOOLS) - set(allowed))
    ]


def with_gateway_grants(role_code: str, capability_names: list[str]) -> list[str]:
    """Compose a role's capability grants with its gateway grants, sorted and deduplicated."""
    return sorted(set(capability_names) | set(gateway_grants(role_code)))


def effective_tool_grants(
    tools_access: list[str] | None,
    allowed_actions: list[str] | None,
    disallowed_actions: list[str] | None,
) -> list[str]:
    """The tools an AI Employee may actually call, from its three configuration columns.

    `allowed_actions` narrows `tools_access` when it is set, and `disallowed_actions`
    overrides both. The AI service used to be told only about the first and the third, so a
    tool removed from `allowed_actions` was still bound and still offered to the model,
    which then spent a turn choosing a call the gateway answered with 403. Both sides now
    compute the grant the same way, from the authoritative side.
    """
    from app.core.tool_permissions import canonical_tool_names

    granted = set(canonical_tool_names(tools_access))
    permitted = set(canonical_tool_names(allowed_actions))
    if permitted:
        granted &= permitted
    return sorted(granted - set(canonical_tool_names(disallowed_actions)))
