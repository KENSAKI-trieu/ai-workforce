"""One graph per agent, chosen by the trusted role -- no parent graph guessing between them.

The parent graph's intent router and agent selector only ever echoed the agent the
backend named, and for a role they did not know they fell back to keyword guessing, which
ran the turn under another agent's prompt.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.agents.base.decision import DeterministicDecisionProvider
from app.agents.base.nodes import OrchestrationRuntimeContext
from app.agents.registry import AGENTS, LangGraphEngine, agent_graph_builder, resolve_agent
from app.governance.middleware.context import AgentRuntimeContext
from app.main import app


def _context(agent_role: str) -> OrchestrationRuntimeContext:
    security = AgentRuntimeContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="Employee",
        department="ALL",
        agent_role=agent_role,
        correlation_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
    )
    return OrchestrationRuntimeContext(
        security=security, decision_provider=DeterministicDecisionProvider(), tools={}
    )


def _state(context: OrchestrationRuntimeContext, requested_agent: str | None) -> dict:
    security = context.security
    return {
        "tenant_id": str(security.tenant_id),
        "user_id": str(security.user_id),
        "role": security.role,
        "department": security.department,
        "conversation_id": str(security.conversation_id),
        "workflow_id": None,
        "messages": [{"role": "user", "content": "Xin chào"}],
        "intent": "",
        "selected_agent": "",
        "retrieved_context": [],
        "tool_calls": [],
        "citations": [],
        "approval_id": None,
        "final_answer": None,
        "errors": [],
        "requested_agent": requested_agent,
    }


def test_every_agent_has_its_own_graph_without_routing_nodes() -> None:
    for agent in AGENTS:
        nodes = set(agent_graph_builder(agent).nodes)
        assert {"intent_router", "agent_selector"}.isdisjoint(nodes)
        assert {f"{agent.lower()}_policy", f"{agent.lower()}_tool_scope", "model_decision"} <= nodes


def test_roles_resolve_to_graphs_and_unknown_roles_are_refused() -> None:
    assert resolve_agent("legal") == "LEGAL"
    # IT and Sales are served by the customer-support graph.
    assert resolve_agent("IT") == "CUSTOMER_SUPPORT"
    assert resolve_agent("SALES") == "CUSTOMER_SUPPORT"
    for unknown in ("MARKETING", "", None):
        with pytest.raises(ValueError):
            resolve_agent(unknown)


def test_the_turn_runs_on_the_graph_of_the_trusted_role() -> None:
    context = _context("LEGAL")
    result = LangGraphEngine().invoke(_state(context, "LEGAL"), context=context, thread_id=str(uuid.uuid4()))
    assert result["selected_agent"] == "LEGAL"
    nodes = [item["node"] for item in result["execution_trace"]]
    assert "legal_policy" in nodes and "intent_router" not in nodes


def test_a_requested_agent_that_differs_from_the_trusted_role_is_refused() -> None:
    context = _context("KNOWLEDGE")
    with pytest.raises(PermissionError):
        LangGraphEngine().invoke(_state(context, "LEGAL"), context=context, thread_id=str(uuid.uuid4()))


def test_an_alias_role_runs_on_the_shared_graph() -> None:
    context = _context("IT")
    result = LangGraphEngine().invoke(_state(context, "IT"), context=context, thread_id=str(uuid.uuid4()))
    assert result["selected_agent"] == "CUSTOMER_SUPPORT"


def _payload(**overrides) -> dict:
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "role": "Employee",
        "department": "ALL",
        "conversation_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "message": "Xin chào",
        "requested_agent": "KNOWLEDGE",
        "allowed_tools": [],
        "denied_tools": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("path", ["/v1/orchestration/run", "/v1/orchestration/run/stream"])
def test_the_api_refuses_an_unknown_or_missing_agent(path, monkeypatch) -> None:
    monkeypatch.setattr("app.agents.base.runtime.configured_chat_models", lambda **_: [])
    client = TestClient(app)
    headers = {"X-Internal-Tool-Authorization": "Bearer internal-tool-jwt"}
    unknown = client.post(path, json=_payload(requested_agent="MARKETING"), headers=headers)
    assert unknown.status_code == 422
    missing = _payload()
    del missing["requested_agent"]
    assert client.post(path, json=missing, headers=headers).status_code == 422
