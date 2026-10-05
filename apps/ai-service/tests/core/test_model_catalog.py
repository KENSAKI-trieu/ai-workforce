"""The agent model picker: vendor listings, filtering, and routing a chosen model."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.llm.base import LLMProvider, LLMResult
from app.core.llm.catalog import CatalogModel, ModelCatalog, _is_chat_model, provider_for_model
from app.services.generation import generate_chat


@pytest.fixture
def two_vendors(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DEFAULT_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "g-key")
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY_2", None)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "o-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY_2", None)
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, "OPENAI_CHAT_MODEL", "gpt-4o-mini")


@pytest.mark.parametrize(
    ("provider", "name", "offered"),
    [
        ("gemini", "gemini-2.5-flash", True),
        ("gemini", "gemini-flash-latest", True),
        ("gemini", "gemini-2.5-flash-preview-tts", False),
        ("gemini", "gemini-2.5-flash-image", False),
        ("gemini", "gemini-embedding-001", False),
        ("gemini", "gemini-3.8-live", False),
        ("gemini", "gemma-4-31b-it", False),
        ("openai", "gpt-4o-mini", True),
        ("openai", "o4-mini", True),
        ("openai", "gpt-4o-2024-08-06", False),
        ("openai", "gpt-4o-mini-transcribe", False),
        ("openai", "gpt-5-pro", False),
        ("openai", "gpt-realtime", False),
        ("openai", "text-embedding-3-small", False),
        ("openai", "whisper-1", False),
    ],
)
def test_only_plain_chat_models_are_offered(provider, name, offered) -> None:
    assert _is_chat_model(provider, name) is offered


def test_a_model_name_names_its_vendor() -> None:
    assert provider_for_model("gemini-2.5-flash") == "gemini"
    assert provider_for_model("gpt-4o-mini") == "openai"
    assert provider_for_model("o3-mini") == "openai"
    assert provider_for_model("jp.anthropic.claude-haiku-4-5-20251001-v1:0") == "bedrock"
    assert provider_for_model("mystery-model") is None
    assert provider_for_model(None) is None


def test_catalog_merges_vendors_and_keeps_a_failed_vendors_default(two_vendors) -> None:
    def failing(api_key):
        raise TimeoutError("vendor down")

    catalog = ModelCatalog(listers={
        "gemini": lambda key: [CatalogModel("gemini-2.5-flash", "gemini", "Gemini 2.5 Flash")],
        "openai": failing,
    })
    snapshot = catalog.snapshot()

    ids = [item["id"] for item in snapshot["models"]]
    assert ids == ["gpt-4o-mini", "gemini-2.5-flash", "gemini-3.5-flash-lite"]
    assert snapshot["errors"] == {"openai": "TimeoutError"}
    assert snapshot["default"] == {"provider": "gemini", "id": "gemini-3.5-flash-lite"}


def test_catalog_is_cached_until_refreshed(two_vendors) -> None:
    calls = []

    def lister(key):
        calls.append(key)
        return []

    catalog = ModelCatalog(listers={"gemini": lister, "openai": lister})
    catalog.snapshot()
    catalog.snapshot()
    assert len(calls) == 2
    catalog.snapshot(refresh=True)
    assert len(calls) == 4


class _Recording(LLMProvider):
    def __init__(self, name):
        self.name = name
        self.models = []

    def generate(self, messages, *, model=None):
        self.models.append(model)
        return LLMResult("ok", model or "default", self.name)


def test_a_chosen_model_is_sent_to_its_own_vendor() -> None:
    gemini, openai = _Recording("gemini"), _Recording("openai")
    requested = []

    def providers(name=None):
        requested.append(name)
        return [openai, gemini] if name == "openai" else [gemini, openai]

    result = generate_chat(
        [{"role": "user", "content": "hi"}],
        model="gpt-4o-mini",
        router=SimpleNamespace(providers=providers),
    )
    assert requested == ["openai"]
    assert result.provider == "openai" and openai.models == ["gpt-4o-mini"]
    assert gemini.models == []


def test_the_graph_leads_with_the_agents_model(monkeypatch) -> None:
    from app.agents.base.runtime import build_runtime_context

    captured = {}

    def models(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.agents.base.runtime.build_langchain_tools", lambda gateway: [])
    monkeypatch.setattr("app.agents.base.runtime.configured_chat_models", models)
    common = dict(
        tenant_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), role="Employee", department="ALL",
        conversation_id=str(uuid.uuid4()), workflow_id=str(uuid.uuid4()), agent_role="KNOWLEDGE",
        allowed_tools=[], denied_tools=[], tool_jwt="tool-jwt",
    )
    build_runtime_context(**common, model="gpt-4o-mini")
    assert captured == {"provider": "openai", "model": "gpt-4o-mini", "max_retries": 0}

    build_runtime_context(**common, model=None)
    assert captured == {"provider": None, "model": None, "max_retries": 0}

    # A name no vendor claims is dropped rather than handed to the default vendor.
    build_runtime_context(**common, model="mystery-model")
    assert captured == {"provider": None, "model": None, "max_retries": 0}
