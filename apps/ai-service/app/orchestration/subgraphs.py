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
    "LEGAL": DomainPolicy(
        "LEGAL",
        ("rag_search", "generate_legal_document", "submit_approval_request"),
        "Apply legal policy context. Generated documents and external actions require approval.",
    ),
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
