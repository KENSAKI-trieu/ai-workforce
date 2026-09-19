"""Business subgraphs sharing the workforce state schema."""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from app.orchestration.state import WorkforceAgentState


@dataclass(frozen=True)
class DomainPolicy:
    agent: str
    tools: tuple[str, ...]
    prompt: str
    citation_required: bool = True


DOMAIN_POLICIES = {
    # These three tools are the whole of the LEGAL agent once the backend routes this
    # role here (LANGGRAPH_ENABLED=true). None of them reviews contract risk, so the
    # deterministic engine behind `audit_contract_risk` -- clause splitting, the
    # per-contract-type checklist, the perspective-aware severity rules and internal
    # conflict detection -- becomes unreachable from chat, silently: the backend's LEGAL
    # branch is skipped entirely rather than failing. Anyone enabling the flag for LEGAL
    # must add a review tool here first; backend/app/main.py logs this at startup.
    "LEGAL": DomainPolicy(
        "LEGAL",
        ("rag_search", "generate_legal_document", "submit_approval_request"),
        "Apply legal policy context. Generated documents and external actions require approval.",
    ),
    # HR does not reach this graph: the backend routes that role to its own deterministic
    # executor instead. The names below are gateway tool names, while an HR agent's
    # tools_access holds capability names from app.core.hr_capabilities, and the two sets
    # are disjoint -- scope_tools intersects them, so enabling HR here would hand the model
    # an empty toolset rather than these five. Every other role now gets its gateway grants
    # from backend/app/core/gateway_tools.py, where HR is empty on purpose; grant HR there
    # first if this role is ever routed through the graph.
    "HR": DomainPolicy(
        "HR",
        ("rag_search", "employee_lookup", "leave_lookup", "create_task", "submit_approval_request"),
        "Apply purpose limitation and employee-scope policy before using HR data.",
    ),
    "FINANCE": DomainPolicy(
        "FINANCE",
        ("rag_search", "expense_lookup", "create_task", "submit_approval_request"),
        "Use tenant cost data and finance policy; do not infer missing amounts.",
    ),
    "CUSTOMER_SUPPORT": DomainPolicy(
        "CUSTOMER_SUPPORT",
        ("rag_search", "create_task", "submit_approval_request"),
        "Ground customer replies in approved support material and gate outbound actions.",
    ),
    "KNOWLEDGE": DomainPolicy(
        "KNOWLEDGE",
        ("rag_search",),
        "Answer only from governed tenant knowledge and provide verifiable citations.",
    ),
    "CEO": DomainPolicy(
        "CEO",
        (
            "rag_search", "employee_lookup", "leave_lookup", "expense_lookup",
            "create_task", "generate_legal_document", "submit_approval_request",
        ),
        "Coordinate cross-domain work, preserve domain ACLs, and gate every action.",
    ),
}


def build_domain_subgraph(policy: DomainPolicy):
    def apply_policy(state: WorkforceAgentState) -> dict:
        return {
            "selected_agent": policy.agent,
            "domain_prompt": policy.prompt,
            "citation_required": policy.citation_required,
            "execution_trace": [
                *(state.get("execution_trace") or []),
                {"node": f"{policy.agent.lower()}_policy", "status": "COMPLETED"},
            ],
        }

    def scope_tools(state: WorkforceAgentState) -> dict:
        requested = set(state.get("available_tools") or [])
        domain_tools = [tool for tool in policy.tools if tool in requested]
        return {
            "available_tools": domain_tools,
            "execution_trace": [
                *(state.get("execution_trace") or []),
                {"node": f"{policy.agent.lower()}_tool_scope", "status": "COMPLETED"},
            ],
        }

    builder = StateGraph(WorkforceAgentState)
    builder.add_node("policy", apply_policy)
    builder.add_node("tool_scope", scope_tools)
    builder.add_edge(START, "policy")
    builder.add_edge("policy", "tool_scope")
    builder.add_edge("tool_scope", END)
    return builder.compile(checkpointer=False)


def build_business_subgraphs() -> dict[str, object]:
    return {name: build_domain_subgraph(policy) for name, policy in DOMAIN_POLICIES.items()}
