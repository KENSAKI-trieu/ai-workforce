"""The AI service builds its tools from the backend's contracts; it keeps no copy of them."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from langchain_core.tools import StructuredTool

from app.agents.base.runtime import model_facing_schema
from app.tools.gateway import ToolGatewayClient, ToolGatewayError
from app.tools.registry import build_langchain_tools

RAG_CONTRACT = {
    "name": "rag_search",
    "description": "Search governed tenant knowledge.",
    "action": "READ_ONLY",
    "allowed_roles": ["*"],
    "allowed_departments": ["*"],
    "acl_match": "ROLE_AND_DEPARTMENT",
    "timeout_seconds": 20,
    "retry_policy": {"max_attempts": 3, "backoff_seconds": 0.25, "retryable_status_codes": [429, 503]},
    "audit_action": "tool.rag.search",
    "terminal": False,
    "input_schema": {
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "audit": {"type": "object"},
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["tenant_id", "audit", "query"],
    },
}
REVIEW_CONTRACT = {
    **RAG_CONTRACT,
    "name": "audit_contract_risk",
    "description": "Review contract text the user sent.",
    "timeout_seconds": 60,
    "retry_policy": {"max_attempts": 3, "backoff_seconds": 0.25, "retryable_status_codes": [429]},
    "terminal": True,
}


class RecordingGateway:
    def __init__(self, contracts: list[dict[str, Any]]) -> None:
        self.contracts = contracts
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return self.contracts

    def invoke(self, name: str, payload: dict[str, Any], **policy: Any) -> Any:
        self.calls.append((name, payload, policy))
        return {"ok": True}

    async def ainvoke(self, name: str, payload: dict[str, Any], **policy: Any) -> Any:
        self.calls.append((name, payload, policy))
        return {"ok": True}


def test_tools_are_built_from_the_contracts_the_backend_returns() -> None:
    gateway = RecordingGateway([RAG_CONTRACT, REVIEW_CONTRACT])
    tools = {tool.name: tool for tool in build_langchain_tools(gateway)}  # type: ignore[arg-type]
    assert set(tools) == {"rag_search", "audit_contract_risk"}
    rag = tools["rag_search"]
    assert isinstance(rag, StructuredTool)
    assert rag.metadata["gateway_only"] is True
    assert rag.metadata["action"] == "READ_ONLY"
    assert rag.metadata["terminal"] is False
    assert tools["audit_contract_risk"].metadata["terminal"] is True

    invocation = {
        "tenant_id": str(uuid.uuid4()),
        "audit": {"correlation_id": str(uuid.uuid4())},
        "query": "leave policy",
    }
    assert rag.invoke(invocation) == {"ok": True}
    name, payload, policy = gateway.calls[0]
    assert name == "rag_search"
    # Injected fields travel through untouched; the backend validates the whole input.
    assert payload == invocation
    assert policy["max_attempts"] == 3
    assert policy["timeout_seconds"] == 20
    assert policy["retryable_status_codes"] == (429, 503)


def test_the_model_never_sees_the_fields_orchestration_injects() -> None:
    tool = build_langchain_tools(RecordingGateway([RAG_CONTRACT]))[0]  # type: ignore[arg-type]
    schema = model_facing_schema(tool)
    assert set(schema["properties"]) == {"query", "top_k"}
    assert schema["required"] == ["query"]


@pytest.mark.tool_contracts
def test_the_contract_list_is_read_from_the_backend(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url, headers, timeout):
        seen["url"], seen["headers"] = url, headers
        return httpx.Response(200, json=[RAG_CONTRACT], request=httpx.Request("GET", url))

    monkeypatch.setattr("app.tools.gateway.httpx.get", fake_get)
    contracts = ToolGatewayClient("http://backend:8000/", "tool-jwt").list_tools()
    assert contracts == [RAG_CONTRACT]
    assert seen["url"] == "http://backend:8000/api/v1/internal/tools"
    assert seen["headers"] == {"Authorization": "Bearer tool-jwt"}


@pytest.mark.tool_contracts
@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"detail": "no"}),
    httpx.Response(200, json={"not": "a list"}),
])
def test_unreadable_contracts_are_an_error_not_an_empty_toolset(monkeypatch, response) -> None:
    def fake_get(url, headers, timeout):
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr("app.tools.gateway.httpx.get", fake_get)
    with pytest.raises(ToolGatewayError):
        ToolGatewayClient("http://backend:8000", "tool-jwt").list_tools()


def test_the_legal_domain_can_review_contracts() -> None:
    """Without it, routing Legal through the graph silently drops contract review."""
    from app.agents.legal.agent import POLICY

    assert "audit_contract_risk" in POLICY.tools


def test_tenant_conventions_follow_the_rules_and_are_fenced() -> None:
    from app.agents.base.decision import decision_system_prompt

    plain = decision_system_prompt([RAG_CONTRACT])
    assert "tenant_conventions" not in plain
    prompt = decision_system_prompt([RAG_CONTRACT], "  Xưng hô anh/chị.  ")
    rules_end = prompt.index("Available tool contracts")
    conventions = prompt.index("<tenant_conventions>\nXưng hô anh/chị.\n</tenant_conventions>")
    assert conventions > rules_end
    assert "never override the rules" in prompt


@pytest.mark.tool_contracts
def test_a_turn_without_tool_contracts_is_refused_with_503(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    def unavailable(self):
        raise ToolGatewayError("backend down")

    monkeypatch.setattr("app.tools.gateway.ToolGatewayClient.list_tools", unavailable)
    monkeypatch.setattr("app.agents.base.runtime.configured_chat_models", lambda **_: [])
    response = TestClient(app).post(
        "/v1/orchestration/run",
        json={
            "tenant_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "role": "Employee",
            "department": "ALL",
            "conversation_id": str(uuid.uuid4()),
            "workflow_id": str(uuid.uuid4()),
            "message": "Xin chào",
            "requested_agent": "KNOWLEDGE",
        },
        headers={"X-Internal-Tool-Authorization": "Bearer internal-tool-jwt"},
    )
    # A 5xx, so the backend falls back to its deterministic flow.
    assert response.status_code == 503
