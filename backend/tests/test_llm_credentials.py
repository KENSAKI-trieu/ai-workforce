"""A company's own model API keys: stored sealed, never shown, sent with its model calls."""

from __future__ import annotations

import base64
import json

import pytest

from app.clients.ai_service_client import AIServiceClient, AIServiceError
from app.core import llm_credentials
from app.core.config import settings
from app.core.llm_credentials import (
    HEADER,
    _tenant_from_authorization,
    key_hint,
    open_key,
    request_headers,
    seal_key,
    using_tenant,
)
from app.core.security import create_access_token, create_internal_tool_token
from app.models.models import AuditLog, TenantLLMCredential, User

KEY = "sk-company-test-0123456789abcd"


class _FakeVerifier:
    def __init__(self, ok: bool = True, fail: bool = False) -> None:
        self.ok = ok
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def verify_llm_key(self, provider, api_key):
        self.calls.append((provider, api_key))
        if self.fail:
            raise AIServiceError("AI service request failed: /v1/llm/verify-key")
        return {"ok": self.ok, "error": None if self.ok else "AuthenticationError", "models": 12}


@pytest.fixture
def verifier(monkeypatch):
    fake = _FakeVerifier()
    monkeypatch.setattr("app.api.v1.workspace.get_ai_service_client", lambda: fake)
    return fake


@pytest.fixture
def acme(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "admin@company.com").one()


@pytest.fixture
def keys_from_test_session(monkeypatch, transactional_db_session):
    """The lookup opens its own session, which cannot see this module's open transaction."""

    def load(tenant_id):
        rows = transactional_db_session.query(TenantLLMCredential).filter(
            TenantLLMCredential.tenant_id == tenant_id
        )
        return {row.provider: [open_key(row.sealed_key)] for row in rows}

    monkeypatch.setattr(llm_credentials, "_load_keys", load)
    llm_credentials._cache.clear()
    yield
    llm_credentials._cache.clear()


def _decoded(value: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


def test_a_key_is_sealed_and_shown_by_its_last_characters_only() -> None:
    sealed = seal_key(KEY)
    assert KEY not in sealed
    assert open_key(sealed) == KEY
    assert key_hint(KEY) == "…abcd"
    assert key_hint("short") == "…"


def test_the_tenant_comes_from_a_verified_token_only(acme) -> None:
    access = create_access_token(acme.id, acme.role, acme.tenant_id)
    internal = create_internal_tool_token(acme, agent_role="LEGAL")
    assert _tenant_from_authorization(f"Bearer {access}") == str(acme.tenant_id)
    assert _tenant_from_authorization(f"Bearer {internal}") == str(acme.tenant_id)
    assert _tenant_from_authorization("Bearer forged.token.value") is None
    assert _tenant_from_authorization(None) is None


def test_no_header_without_a_tenant_or_without_keys(monkeypatch) -> None:
    monkeypatch.setattr(llm_credentials, "_load_keys", lambda tenant_id: {})
    llm_credentials._cache.clear()
    assert request_headers() == {}
    with using_tenant("00000000-0000-0000-0000-000000000001"):
        assert request_headers() == {}
    llm_credentials._cache.clear()


def test_saving_checks_the_key_then_stores_it_sealed(
    client, ceo_token_headers, verifier, acme, transactional_db_session
) -> None:
    response = client.put(
        "/api/v1/workspace/llm-keys/openai", json={"api_key": f"  {KEY} "}, headers=ceo_token_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["hint"] == "…abcd"
    assert verifier.calls == [("openai", KEY)]

    row = transactional_db_session.query(TenantLLMCredential).filter(
        TenantLLMCredential.tenant_id == acme.tenant_id, TenantLLMCredential.provider == "openai"
    ).one()
    assert row.sealed_key != KEY and open_key(row.sealed_key) == KEY

    workspace = client.get("/api/v1/workspace", headers=ceo_token_headers)
    assert KEY not in workspace.text
    openai = next(item for item in workspace.json()["llm_keys"] if item["provider"] == "openai")
    assert openai["company_key"]["hint"] == "…abcd"
    assert openai["company_key"]["readable"] is True

    audit = transactional_db_session.query(AuditLog).filter(
        AuditLog.tenant_id == acme.tenant_id, AuditLog.action == "workspace.llm_key_saved"
    ).all()
    assert audit and all(KEY not in json.dumps(item.after_data or {}) for item in audit)


def test_a_refused_key_is_not_stored(client, ceo_token_headers, monkeypatch, acme, transactional_db_session) -> None:
    monkeypatch.setattr("app.api.v1.workspace.get_ai_service_client", lambda: _FakeVerifier(ok=False))
    response = client.put(
        "/api/v1/workspace/llm-keys/gemini", json={"api_key": "AIza-refused-key-value"}, headers=ceo_token_headers
    )
    assert response.status_code == 422
    assert not transactional_db_session.query(TenantLLMCredential).filter(
        TenantLLMCredential.tenant_id == acme.tenant_id, TenantLLMCredential.provider == "gemini"
    ).count()

    monkeypatch.setattr("app.api.v1.workspace.get_ai_service_client", lambda: _FakeVerifier(fail=True))
    response = client.put(
        "/api/v1/workspace/llm-keys/gemini", json={"api_key": "AIza-refused-key-value"}, headers=ceo_token_headers
    )
    assert response.status_code == 503


def test_only_administrators_manage_keys(client, employee_token_headers, verifier) -> None:
    response = client.put(
        "/api/v1/workspace/llm-keys/openai", json={"api_key": KEY}, headers=employee_token_headers
    )
    assert response.status_code == 403
    assert client.delete("/api/v1/workspace/llm-keys/openai", headers=employee_token_headers).status_code == 403
    assert verifier.calls == []


def test_an_unknown_vendor_is_refused(client, ceo_token_headers, verifier) -> None:
    response = client.put(
        "/api/v1/workspace/llm-keys/anthropic", json={"api_key": KEY}, headers=ceo_token_headers
    )
    assert response.status_code == 404


def test_the_company_key_travels_with_its_model_calls(
    client, ceo_token_headers, verifier, keys_from_test_session, monkeypatch
) -> None:
    assert client.put(
        "/api/v1/workspace/llm-keys/openai", json={"api_key": KEY}, headers=ceo_token_headers
    ).status_code == 200

    sent: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"default": None, "models": [], "errors": {}}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        sent.append(headers or {})
        return _Response()

    monkeypatch.setattr(settings, "AI_SERVICE_INTERNAL_TOKEN", "internal-test-token")
    ai_client = AIServiceClient()
    ai_client.base_url = "http://ai.test"
    monkeypatch.setattr("app.api.v1.agents.get_ai_service_client", lambda: ai_client)
    monkeypatch.setattr("app.clients.ai_service_client.httpx.get", fake_get)

    response = client.get("/api/v1/agents/model-options", headers=ceo_token_headers)
    assert response.status_code == 200, response.text
    assert _decoded(sent[-1][HEADER]) == {"openai": [KEY]}

    # Removed, the vendor goes back to the deployment's keys: no header at all.
    assert client.delete("/api/v1/workspace/llm-keys/openai", headers=ceo_token_headers).status_code == 200
    client.get("/api/v1/agents/model-options", headers=ceo_token_headers)
    assert HEADER not in sent[-1]
