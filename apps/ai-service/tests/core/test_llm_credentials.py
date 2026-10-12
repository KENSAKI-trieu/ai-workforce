"""A company's own API keys, carried per request in X-LLM-Credentials."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.llm.catalog import CatalogModel, ModelCatalog, verify_key
from app.core.llm.credentials import (
    CredentialsMiddleware,
    encode_header,
    overridden_keys,
    parse_header,
    using_credentials,
)
from app.core.llm.factory import configured_chat_models, provider_keys
from app.core.llm.router import LLMRouter


@pytest.fixture
def platform_keys(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DEFAULT_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "platform-gemini")
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY_2", None)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "platform-openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY_2", None)
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, "OPENAI_CHAT_MODEL", "gpt-4o-mini")


def test_the_header_round_trips_and_drops_what_it_cannot_use() -> None:
    value = encode_header({"openai": ["sk-company", " ", "sk-company"], "anthropic": ["x"]})
    assert parse_header(value) == {"openai": ("sk-company",)}
    assert parse_header(None) is None
    assert parse_header("not base64 json!") is None
    assert parse_header(encode_header({"gemini": []})) is None


def test_a_company_key_replaces_only_its_own_vendor(platform_keys) -> None:
    assert provider_keys("gemini") == ("platform-gemini",)
    with using_credentials({"gemini": ("company-gemini",)}):
        assert provider_keys("gemini") == ("company-gemini",)
        assert provider_keys("openai") == ("platform-openai",)
    assert overridden_keys("gemini") is None


def test_the_graph_and_the_text_path_build_on_the_company_key(platform_keys, monkeypatch) -> None:
    built = []
    monkeypatch.setattr(
        "app.core.llm.factory.create_chat_model",
        lambda provider, *, api_key, model=None, **_: built.append((provider, api_key, model)) or object(),
    )
    with using_credentials({"openai": ("company-openai",)}):
        configured_chat_models(provider="openai", model="gpt-4.1")
        providers = LLMRouter().providers("openai")
    assert built[0] == ("openai", "company-openai", "gpt-4.1")
    assert ("gemini", "platform-gemini", None) in built
    assert providers[0].api_keys == ("company-openai",)


def test_the_catalog_lists_with_the_company_key_and_caches_it_apart(platform_keys) -> None:
    seen = []

    def lister(key):
        seen.append(key)
        return [CatalogModel(f"gemini-for-{key}", "gemini", key)]

    catalog = ModelCatalog(listers={"gemini": lister, "openai": lambda key: []})
    platform = catalog.snapshot()
    with using_credentials({"gemini": ("company-gemini",)}):
        company = catalog.snapshot()
        catalog.snapshot()
    catalog.snapshot()

    assert seen == ["platform-gemini", "company-gemini"]
    assert "gemini-for-company-gemini" in {item["id"] for item in company["models"]}
    assert "gemini-for-company-gemini" not in {item["id"] for item in platform["models"]}


def test_verify_key_reports_the_vendor_failure_without_its_message() -> None:
    def refusing(key):
        raise PermissionError(f"bad key {key}")

    assert verify_key("openai", "sk-good", {"openai": lambda key: [CatalogModel("gpt-4o", "openai", "gpt-4o")]}) == {
        "ok": True, "error": None, "models": 1,
    }
    result = verify_key("openai", "sk-bad", {"openai": refusing})
    assert result == {"ok": False, "error": "PermissionError", "models": 0}
    assert verify_key("anthropic", "x", {})["ok"] is False


def test_the_middleware_binds_the_header_for_the_request_only(platform_keys) -> None:
    app = FastAPI()
    app.add_middleware(CredentialsMiddleware)

    @app.get("/keys")
    def keys() -> dict:
        return {"gemini": list(provider_keys("gemini"))}

    client = TestClient(app)
    header = encode_header({"gemini": ["company-gemini"]})
    assert client.get("/keys", headers={"X-LLM-Credentials": header}).json() == {"gemini": ["company-gemini"]}
    assert client.get("/keys").json() == {"gemini": ["platform-gemini"]}
