"""Per-agent model choice, and grants of agent rows seeded before their tools existed."""

from __future__ import annotations

import contextvars
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.agents.langgraph.engine import LangGraphEngine
from app.clients.ai_service_client import AIServiceClient, AIServiceError
from app.core.agent_models import (
    agent_model_dependency,
    bind_while_iterating,
    selected_model,
    using_model,
)
from app.core.database import get_db
from app.core.hr_capabilities import HR_CONFIGURATION_VERSION
from app.core.security import get_current_active_user
from app.domains.platform.auth_service import DEFAULT_AGENT_TOOLS, upgrade_agent_grants
from app.models.models import AIAgent, User

CATALOG = {
    "default": {"provider": "gemini", "id": "gemini-3.5-flash-lite"},
    "models": [
        {"id": "gpt-4o-mini", "provider": "openai", "label": "gpt-4o-mini"},
        {"id": "gemini-2.5-flash", "provider": "gemini", "label": "Gemini 2.5 Flash"},
        {"id": "gemini-3.8-flash", "provider": "gemini", "label": "Gemini 3.8 Flash"},
    ],
    "errors": {},
}


class _FakeCatalogClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def list_models(self, *, refresh: bool = False):
        if self.fail:
            raise AIServiceError("AI service request failed: /v1/llm/models")
        return CATALOG


@pytest.fixture
def catalog(monkeypatch):
    fake = _FakeCatalogClient()
    monkeypatch.setattr("app.api.v1.agents.get_ai_service_client", lambda: fake)
    return fake


@pytest.fixture
def acme(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "admin@company.com").one()


def _agent(db, tenant_id, role):
    return db.query(AIAgent).filter(AIAgent.tenant_id == tenant_id, AIAgent.role_code == role).one()


def test_model_options_list_the_vendors_models_with_pricing(client, ceo_token_headers, catalog):
    response = client.get("/api/v1/agents/model-options", headers=ceo_token_headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["default"] == CATALOG["default"]
    priced = {item["id"]: item["priced"] for item in data["models"]}
    assert priced == {"gpt-4o-mini": True, "gemini-2.5-flash": True, "gemini-3.8-flash": False}


def test_model_options_need_the_configure_permission(client, employee_token_headers, catalog):
    response = client.get("/api/v1/agents/model-options", headers=employee_token_headers)
    assert response.status_code == 403


def test_model_options_say_when_the_ai_service_is_down(client, ceo_token_headers, catalog):
    catalog.fail = True
    response = client.get("/api/v1/agents/model-options", headers=ceo_token_headers)
    assert response.status_code == 503
    assert "danh sách model" in response.json()["detail"]


def test_a_listed_model_is_saved_and_blank_returns_to_the_default(
    client, ceo_token_headers, catalog, transactional_db_session, acme
):
    agent = _agent(transactional_db_session, acme.tenant_id, "KNOWLEDGE")
    try:
        saved = client.patch(
            "/api/v1/agents/KNOWLEDGE", headers=ceo_token_headers, json={"model_name": "gpt-4o-mini"}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["model_name"] == "gpt-4o-mini"

        refused = client.patch(
            "/api/v1/agents/KNOWLEDGE", headers=ceo_token_headers, json={"model_name": "gpt-made-up"}
        )
        assert refused.status_code == 422
        assert "gpt-made-up" in refused.json()["detail"]

        reset = client.patch("/api/v1/agents/KNOWLEDGE", headers=ceo_token_headers, json={"model_name": ""})
        assert reset.status_code == 200
        assert reset.json()["model_name"] is None
    finally:
        agent.model_name = None
        transactional_db_session.commit()


def test_resaving_a_withdrawn_model_does_not_block_the_rest(
    client, ceo_token_headers, catalog, transactional_db_session, acme
):
    agent = _agent(transactional_db_session, acme.tenant_id, "KNOWLEDGE")
    agent.model_name = "gemini-retired"
    transactional_db_session.commit()
    try:
        catalog.fail = True  # not even consulted: the value did not change
        response = client.patch(
            "/api/v1/agents/KNOWLEDGE",
            headers=ceo_token_headers,
            json={"model_name": "gemini-retired", "prompt_overlay": "Xưng hô anh/chị."},
        )
        assert response.status_code == 200, response.text
    finally:
        agent.model_name = None
        agent.prompt_overlay = None
        transactional_db_session.commit()


def test_generate_text_sends_the_bound_model_unless_the_caller_names_one(monkeypatch):
    sent = []
    monkeypatch.setattr(
        AIServiceClient, "_post", lambda self, path, payload, **kwargs: sent.append(payload) or {}
    )
    client = AIServiceClient()

    client.generate_text([{"role": "user", "content": "a"}])
    with using_model("gemini-2.5-flash"):
        client.generate_text([{"role": "user", "content": "b"}])
        client.generate_text([{"role": "user", "content": "c"}], model="gpt-4o-mini")
    client.generate_text([{"role": "user", "content": "d"}])

    assert [item["model"] for item in sent] == [None, "gemini-2.5-flash", "gpt-4o-mini", None]


def test_a_streamed_body_keeps_the_model_across_steps():
    """Starlette runs each step of a sync stream in a fresh copy of the request context."""

    def body():
        yield selected_model()
        yield selected_model()

    stream = bind_while_iterating(body(), "gemini-2.5-flash")
    steps = [contextvars.copy_context().run(next, stream) for _ in range(2)]
    assert steps == ["gemini-2.5-flash", "gemini-2.5-flash"]
    assert selected_model() is None


def test_the_router_dependency_reaches_the_endpoint_and_its_stream(transactional_db_session, acme):
    agent = _agent(transactional_db_session, acme.tenant_id, "FINANCE")
    agent.model_name = "gpt-4o-mini"
    transactional_db_session.commit()
    app = FastAPI(dependencies=[Depends(agent_model_dependency("FINANCE"))])

    @app.get("/plain")
    def plain():
        return {"model": selected_model()}

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter([str(selected_model())]))

    def override_db():
        yield transactional_db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = lambda: acme
    try:
        with TestClient(app) as test_client:
            assert test_client.get("/plain").json() == {"model": "gpt-4o-mini"}
            assert test_client.get("/stream").text == "gpt-4o-mini"
    finally:
        agent.model_name = None
        transactional_db_session.commit()


def test_the_graph_is_told_the_agents_model(transactional_db_session, acme):
    agent = _agent(transactional_db_session, acme.tenant_id, "LEGAL")
    agent.model_name = "gemini-2.5-flash"
    try:
        payload = LangGraphEngine._payload(
            db=transactional_db_session, user=acme, agent=agent,
            conversation_id=str(uuid.uuid4()), workflow_id=str(uuid.uuid4()),
        )
        assert payload["model"] == "gemini-2.5-flash"
        agent.model_name = "  "
        payload = LangGraphEngine._payload(
            db=transactional_db_session, user=acme, agent=agent,
            conversation_id=str(uuid.uuid4()), workflow_id=str(uuid.uuid4()),
        )
        assert payload["model"] is None
    finally:
        agent.model_name = None


def _row(role: str, *, tools, allowed, denied=(), version=1) -> AIAgent:
    return AIAgent(
        role_code=role,
        tools_access=list(tools),
        allowed_actions=list(allowed),
        disallowed_actions=list(denied),
        configuration_version=version,
    )


def test_an_agent_seeded_with_no_tools_gets_its_roles_defaults():
    row = _row("LEGAL", tools=[], allowed=[])
    assert upgrade_agent_grants(row) is True
    assert row.tools_access == sorted(DEFAULT_AGENT_TOOLS["LEGAL"])
    # Empty means "everything granted"; filling it would only repeat tools_access.
    assert row.allowed_actions == []
    assert row.configuration_version == HR_CONFIGURATION_VERSION


def test_the_one_size_legacy_seed_is_replaced_not_extended():
    row = _row(
        "KNOWLEDGE",
        tools=["query_leave_balance", "request_leave", "hybrid_rag_search"],
        allowed=[],
    )
    upgrade_agent_grants(row)
    assert row.tools_access == sorted(DEFAULT_AGENT_TOOLS["KNOWLEDGE"])
    assert "request_leave" not in row.allowed_actions


def test_an_upgrade_keeps_denials_and_drops_names_the_role_cannot_use():
    row = _row(
        "LEGAL",
        tools=["audit_contract_risk", "rag_search", "request_leave"],
        allowed=["audit_contract_risk", "rag_search", "request_leave"],
        denied=["generate_legal_document"],
        version=5,
    )
    upgrade_agent_grants(row)
    assert "request_leave" not in row.tools_access
    assert "generate_legal_document" not in row.tools_access
    assert row.disallowed_actions == ["generate_legal_document"]
    assert "compare_contract_versions" in row.allowed_actions


def test_a_configured_row_is_left_alone():
    row = _row("LEGAL", tools=["rag_search"], allowed=["rag_search"], version=HR_CONFIGURATION_VERSION)
    assert upgrade_agent_grants(row) is False
    assert row.tools_access == ["rag_search"]


def test_the_configuration_page_shows_an_upgraded_agent(
    client, ceo_token_headers, transactional_db_session, acme
):
    agent = _agent(transactional_db_session, acme.tenant_id, "IT")
    before = (list(agent.tools_access), list(agent.allowed_actions), agent.configuration_version)
    agent.tools_access, agent.allowed_actions, agent.configuration_version = [], [], 1
    transactional_db_session.commit()
    try:
        response = client.get("/api/v1/agents/IT", headers=ceo_token_headers)
        assert response.status_code == 200
        assert set(response.json()["tools_access"]) == set(DEFAULT_AGENT_TOOLS["IT"])
    finally:
        agent.tools_access, agent.allowed_actions, agent.configuration_version = before
        transactional_db_session.commit()
