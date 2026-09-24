"""What a tenant customises reaches the agent on either engine, and nothing else does.

- The administrator's text box used to be appended to HR's `answer` slot for every role,
  so text entered for the Legal agent landed where the Legal flow never reads.
- Through LangGraph none of it arrived at all: the graph got no tenant prompt, and was
  offered tools a plugin had withdrawn, which the gateway then refused.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.models import AIAgent, User
from app.plugins.manifest import parse_manifest
from app.plugins.resolver import resolve_prompt_overlay, tenant_graph_instructions
from app.services.agents.langgraph_engine import LangGraphEngine
from app.services.agents.legal_prompts import DEFAULT_LEGAL_PROMPTS
from app.tools.registry import tool_registry

KNOWN_TOOLS = frozenset({"rag_search", "audit_contract_risk", "generate_legal_document", "hybrid_rag_search"})
KNOWN_ROLES = frozenset({"HR", "LEGAL"})


def _legal_manifest(name: str, prompts: dict, **extra):
    return parse_manifest(
        {
            "name": name,
            "version": "1.0.0",
            "display_name": name,
            "target_role": "LEGAL",
            "prompts": prompts,
            **extra,
        },
        known_tools=KNOWN_TOOLS,
        known_roles=KNOWN_ROLES,
    )


@pytest.fixture
def legal_agent(transactional_db_session):
    agent = transactional_db_session.query(AIAgent).filter(AIAgent.role_code == "LEGAL").first()
    original = (agent.prompt_overlay, agent.tools_access, agent.allowed_actions, agent.disallowed_actions)
    yield agent
    agent.prompt_overlay, agent.tools_access, agent.allowed_actions, agent.disallowed_actions = original
    transactional_db_session.flush()


def test_the_text_box_reaches_the_legal_answer_slot(transactional_db_session, legal_agent, monkeypatch):
    monkeypatch.setattr("app.plugins.resolver.installed_manifests", lambda *args: ())
    legal_agent.prompt_overlay = "Luôn xưng hô anh/chị."
    transactional_db_session.flush()

    overlay = resolve_prompt_overlay(transactional_db_session, legal_agent.tenant_id, "LEGAL")
    assert overlay is not None
    assert "answer" not in overlay
    assert overlay["legal_answer"] == DEFAULT_LEGAL_PROMPTS["legal_answer"] + "\n\nLuôn xưng hô anh/chị."


def test_graph_instructions_carry_appends_and_own_text_but_not_replacements(
    transactional_db_session, legal_agent, monkeypatch
):
    manifests = (
        _legal_manifest("tone", {"legal_answer": {"mode": "append", "text": "Gọi hợp đồng là 'khế ước'."}}),
        # A replacement rewrites the deterministic prompt, output format included.
        _legal_manifest("rewrite", {"legal_answer": {"mode": "replace", "text": "Trả JSON khác hẳn."}}),
        # A router slot has no equivalent in the graph.
        _legal_manifest("routing", {"legal_classifier": {"mode": "append", "text": "Gợi ý định tuyến."}}),
    )
    monkeypatch.setattr("app.plugins.resolver.installed_manifests", lambda *args: manifests)
    legal_agent.prompt_overlay = "Luôn xưng hô anh/chị."
    transactional_db_session.flush()

    instructions = tenant_graph_instructions(transactional_db_session, legal_agent.tenant_id, "LEGAL")
    assert instructions == "Gọi hợp đồng là 'khế ước'.\n\nLuôn xưng hô anh/chị."
    # A role with nothing customised sends nothing.
    monkeypatch.setattr("app.plugins.resolver.installed_manifests", lambda *args: ())
    legal_agent.prompt_overlay = None
    transactional_db_session.flush()
    assert tenant_graph_instructions(transactional_db_session, legal_agent.tenant_id, "LEGAL") == ""


def test_the_graph_payload_carries_instructions_and_drops_withdrawn_tools(
    transactional_db_session, legal_agent, monkeypatch
):
    manifests = (
        _legal_manifest(
            "narrow",
            {"legal_answer": {"mode": "append", "text": "Trích dẫn số điều khoản."}},
            skills={"disallowed_actions": ["generate_legal_document"]},
        ),
    )
    monkeypatch.setattr("app.plugins.resolver.installed_manifests", lambda *args: manifests)
    legal_agent.tools_access = ["rag_search", "audit_contract_risk", "generate_legal_document"]
    legal_agent.allowed_actions = []
    legal_agent.disallowed_actions = []
    legal_agent.prompt_overlay = None
    transactional_db_session.flush()
    user = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()

    payload = LangGraphEngine._payload(
        db=transactional_db_session,
        user=user,
        agent=legal_agent,
        conversation_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        message="Rà soát giúp",
    )
    assert payload["tenant_instructions"] == "Trích dẫn số điều khoản."
    assert "generate_legal_document" not in payload["allowed_tools"]
    assert {"rag_search", "audit_contract_risk"} <= set(payload["allowed_tools"])


def test_the_tool_contract_says_which_tools_end_the_turn():
    metadata = {item.name: item.public_metadata() for item in tool_registry.all()}
    assert metadata["audit_contract_risk"]["terminal"] is True
    assert metadata["rag_search"]["terminal"] is False
    # The model-facing description now lives here, since the AI service reads it from us.
    assert "do not paste the contract" in metadata["audit_contract_risk"]["description"]


def test_tool_inputs_bind_tenant_and_audit_and_keep_the_contract_out_of_the_model():
    """Moved from the AI service with the schemas themselves: the backend's are the only copy."""
    from pydantic import ValidationError

    from app.tools.schemas import ContractRiskReviewInput, CreateTaskInput, RAGSearchInput

    with pytest.raises(ValidationError):
        RAGSearchInput.model_validate({"query": "leave policy"})
    with pytest.raises(ValidationError):
        CreateTaskInput.model_validate({
            "tenant_id": str(uuid.uuid4()),
            "audit": {"correlation_id": str(uuid.uuid4())},
            "title": "Missing idempotency key",
        })
    properties = ContractRiskReviewInput.model_json_schema()["properties"]
    # The contract and the user's side are read from the user's own messages.
    assert "contract_text" not in properties and "represented_party" not in properties
    assert set(properties) >= {"from_user_message", "document_scope"}
    base = {"tenant_id": str(uuid.uuid4()), "audit": {"correlation_id": str(uuid.uuid4())}}
    assert ContractRiskReviewInput.model_validate(base).from_user_message == 0
    with pytest.raises(ValidationError):
        ContractRiskReviewInput.model_validate({**base, "from_user_message": 9})
