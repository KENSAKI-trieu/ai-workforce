from types import SimpleNamespace

import pytest

from app.core.llm.base import LLMProvider, LLMResult
from app.core.llm.fallback import generate_with_fallback
from app.core.llm.openai_provider import OpenAIProvider
from app.core.llm.router import LLMRouter
from app.core.llm.factory import configured_chat_models, create_chat_model


class _RecordingProvider(LLMProvider):
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.fail = fail
        self.models: list[str | None] = []

    def generate(self, messages, *, model=None):
        self.models.append(model)
        if self.fail:
            raise TimeoutError("provider timed out")
        return LLMResult("ok", model or "fallback-default", self.name)


def test_model_factory_passes_retry_and_timeout_to_openai(monkeypatch) -> None:
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.core.llm.factory.ChatOpenAI", fake_openai)
    result = create_chat_model(
        "openai",
        api_key="test-key",
        model="test-model",
        timeout=12.5,
        max_retries=4,
    )
    assert result is not None
    assert captured["model"] == "test-model"
    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 4


def test_langchain_adapter_preserves_llm_result_contract() -> None:
    response = SimpleNamespace(
        text="validated response",
        content="validated response",
        response_metadata={"model_name": "resolved-model"},
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4,
            "input_token_details": {"cache_read": 3},
        },
    )
    model = SimpleNamespace(invoke=lambda messages: response)
    provider = OpenAIProvider(
        "test-key",
        model_factory=lambda *args, **kwargs: model,
    )
    result = provider.generate([{"role": "user", "content": "hello"}])
    assert result.content == "validated response"
    assert result.provider == "openai"
    assert result.model == "resolved-model"
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "cached_prompt_tokens": 3,
    }


def test_provider_fallback_uses_each_provider_default_model() -> None:
    primary = _RecordingProvider("openai", fail=True)
    secondary = _RecordingProvider("gemini")
    result = generate_with_fallback(
        [primary, secondary],
        [{"role": "user", "content": "hello"}],
        model="openai-model",
    )
    assert result.provider == "gemini"
    assert primary.models == ["openai-model"]
    assert secondary.models == [None]


def test_router_honors_configured_default_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.core.llm.router.settings.LLM_DEFAULT_PROVIDER", "gemini")
    monkeypatch.setattr("app.core.llm.router.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.core.llm.router.settings.GOOGLE_AI_API_KEY", "gemini-key")

    # Bedrock is on for the suite, so it sits in the chain wherever its own order
    # puts it; what this test is about is the chosen default coming first.
    names = [provider.name for provider in LLMRouter().providers()]
    assert names[0] == "gemini"
    assert names[-1] == "local"
    assert set(names) == {"gemini", "openai", "bedrock", "local"}


@pytest.mark.bedrock_flag
def test_chat_models_follow_the_configured_default_provider(monkeypatch) -> None:
    """The graph, routing and extraction build their models here with no provider.

    Ignoring the default put OpenAI first on a Gemini deployment, so every turn spent
    a failing call before reaching the provider it was configured for.
    """
    monkeypatch.setattr("app.core.llm.factory.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.core.llm.factory.settings.GOOGLE_AI_API_KEY", "gemini-key")
    monkeypatch.setattr("app.core.llm.factory.settings.BEDROCK_ENABLED", False)

    monkeypatch.setattr("app.core.llm.factory.settings.LLM_DEFAULT_PROVIDER", "gemini")
    assert [type(model).__name__ for model in configured_chat_models()] == [
        "ChatGoogleGenerativeAI", "ChatOpenAI",
    ]
    # An explicit provider still wins over the configured default.
    assert type(configured_chat_models("openai")[0]).__name__ == "ChatOpenAI"

    monkeypatch.setattr("app.core.llm.factory.settings.LLM_DEFAULT_PROVIDER", "auto")
    assert type(configured_chat_models()[0]).__name__ == "ChatOpenAI"

    monkeypatch.setattr("app.core.llm.factory.settings.LLM_DEFAULT_PROVIDER", "local")
    assert configured_chat_models() == []


@pytest.mark.bedrock_flag
def test_router_leaves_bedrock_out_while_the_flag_is_off(monkeypatch) -> None:
    """The flag is the only thing gating it: there is no key to be missing."""
    monkeypatch.setattr("app.core.llm.router.settings.BEDROCK_ENABLED", False)
    monkeypatch.setattr("app.core.llm.router.settings.LLM_DEFAULT_PROVIDER", "auto")
    monkeypatch.setattr("app.core.llm.router.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.core.llm.router.settings.GOOGLE_AI_API_KEY", "gemini-key")

    assert "bedrock" not in [provider.name for provider in LLMRouter().providers()]
