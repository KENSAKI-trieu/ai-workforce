"""The standard graph of one agent.

    input_guard → <agent>_policy → <agent>_tool_scope → retrieve_context → model_decision
        model_decision → execute_read_tool → model_decision   (read tools loop back)
        model_decision → approval_interrupt                     (actions wait for a human)
        → output_validation → citation_verification → response

There is no parent graph routing between agents: the user always talks to one agent and
the backend names it, so ``agents/registry.py`` picks this graph by role instead.
An agent that needs a step of its own builds this graph and adds to it in its graph.py.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.base.nodes import (
    OrchestrationRuntimeContext,
    after_decision,
    after_read_tool,
    approval_interrupt,
    citation_verification,
    execute_read_tool,
    input_guard,
    model_decision,
    output_validation,
    response,
    retrieve_context,
)
from app.agents.base.policy import DomainPolicy, policy_node, tool_scope_node
from app.agents.base.state import WorkforceAgentState

# Public progress phase for each node; anything unlisted is not reported to the client.
_FIXED_PHASES = {
    "input_guard": "ANALYZING",
    "retrieve_context": "SEARCHING",
    "model_decision": "ANALYZING",
    "execute_read_tool": "TOOL_CALLING",
    "approval_interrupt": "TOOL_CALLING",
    "output_validation": "ANALYZING",
    "citation_verification": "ANALYZING",
}


def public_phase(node_name: str) -> str | None:
    if node_name.endswith(("_policy", "_tool_scope")):
        return "ANALYZING"
    return _FIXED_PHASES.get(node_name)


def build_agent_graph(policy: DomainPolicy) -> StateGraph:
    policy_step = f"{policy.node_prefix}_policy"
    scope_step = f"{policy.node_prefix}_tool_scope"

    builder = StateGraph(WorkforceAgentState, context_schema=OrchestrationRuntimeContext)
    builder.add_node("input_guard", input_guard)
    builder.add_node(policy_step, policy_node(policy))
    builder.add_node(scope_step, tool_scope_node(policy))
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("model_decision", model_decision)
    builder.add_node("execute_read_tool", execute_read_tool)
    builder.add_node("approval_interrupt", approval_interrupt)
    builder.add_node("output_validation", output_validation)
    builder.add_node("citation_verification", citation_verification)
    builder.add_node("response", response)

    builder.add_edge(START, "input_guard")
    builder.add_edge("input_guard", policy_step)
    builder.add_edge(policy_step, scope_step)
    builder.add_edge(scope_step, "retrieve_context")
    builder.add_edge("retrieve_context", "model_decision")
    builder.add_conditional_edges(
        "model_decision",
        after_decision,
        {
            "execute_read_tool": "execute_read_tool",
            "approval_interrupt": "approval_interrupt",
            "output_validation": "output_validation",
        },
    )
    builder.add_conditional_edges(
        "execute_read_tool",
        after_read_tool,
        {"model_decision": "model_decision", "output_validation": "output_validation"},
    )
    builder.add_edge("approval_interrupt", "output_validation")
    builder.add_edge("output_validation", "citation_verification")
    builder.add_edge("citation_verification", "response")
    builder.add_edge("response", END)
    return builder
