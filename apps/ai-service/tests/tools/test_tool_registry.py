from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.tools.registry import ToolAction, build_langchain_tools, tool_registry
from app.tools.schemas import CreateTaskInput, RAGSearchInput


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def invoke(self, name: str, payload: dict[str, Any], **policy: Any) -> Any:
        self.calls.append((name, payload, policy))
        return {"ok": True}

    async def ainvoke(self, name: str, payload: dict[str, Any], **policy: Any) -> Any:
        self.calls.append((name, payload, policy))
        return {"ok": True}


def _rag_payload() -> dict[str, Any]:
    return {
        "tenant_id": str(uuid.uuid4()),
        "audit": {"correlation_id": str(uuid.uuid4())},
        "query": "leave policy",
    }


def test_registry_contains_governed_phase3_tools() -> None:
    descriptors = {item.name: item for item in tool_registry.all()}
    assert set(descriptors) == {
        "rag_search",
        "employee_lookup",
        "leave_lookup",
        "create_task",
        "expense_lookup",
        "generate_legal_document",
        "submit_approval_request",
        "audit_contract_risk",
    }
    assert descriptors["rag_search"].action == ToolAction.READ_ONLY
    # Reviews run without a prior human approval; a CRITICAL result raises its own.
    assert descriptors["audit_contract_risk"].action == ToolAction.READ_ONLY
    assert descriptors["create_task"].action == ToolAction.WRITE
    assert descriptors["submit_approval_request"].action == ToolAction.EXTERNAL_ACTION
    assert descriptors["rag_search"].retry.max_attempts == 3
    assert descriptors["create_task"].retry.max_attempts == 1
    assert all(item.timeout_seconds > 0 and item.audit_action for item in descriptors.values())
    assert all(item.allowed_roles and item.allowed_departments for item in descriptors.values())


def test_tenant_and_audit_are_mandatory() -> None:
    with pytest.raises(ValidationError):
        RAGSearchInput.model_validate({"query": "leave policy"})
    with pytest.raises(ValidationError):
        CreateTaskInput.model_validate({
            "tenant_id": str(uuid.uuid4()),
            "audit": {"correlation_id": str(uuid.uuid4())},
            "title": "Missing idempotency key",
        })


def test_langchain_tools_delegate_to_gateway_with_policy() -> None:
    gateway = RecordingGateway()
    tools = build_langchain_tools(gateway)  # type: ignore[arg-type]
    assert len(tools) == 8
    rag = next(tool for tool in tools if tool.name == "rag_search")
    assert isinstance(rag, StructuredTool)
    assert rag.metadata["gateway_only"] is True
    invocation = _rag_payload()
    assert rag.invoke(invocation) == {"ok": True}
    name, payload, policy = gateway.calls[0]
    assert name == "rag_search"
    assert payload["tenant_id"] == invocation["tenant_id"]
    assert policy["max_attempts"] == 3
    assert policy["timeout_seconds"] == 20


def test_the_legal_domain_can_review_contracts() -> None:
    """Without it, routing Legal through the graph silently drops contract review."""
    from app.agents.legal.agent import POLICY
    from app.tools.schemas import ContractRiskReviewInput

    assert "audit_contract_risk" in POLICY.tools
    schema = ContractRiskReviewInput.model_json_schema()["properties"]
    # The contract and the user's side are read from the user's messages by the backend;
    # neither is the model's to supply.
    assert "contract_text" not in schema and "represented_party" not in schema
    assert set(schema) >= {"from_user_message", "document_scope"}
    base = {"tenant_id": str(uuid.uuid4()), "audit": {"correlation_id": str(uuid.uuid4())}}
    assert ContractRiskReviewInput.model_validate(base).from_user_message == 0
    with pytest.raises(ValidationError):
        ContractRiskReviewInput.model_validate({**base, "from_user_message": 9})
