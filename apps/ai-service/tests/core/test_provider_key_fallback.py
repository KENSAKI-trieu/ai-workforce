"""A vendor with two credentials must survive one of them running out.

Free and low tiers are rate limited per project, so an exhausted key used to take the
whole vendor down: `generate_with_fallback` moved on to the next vendor and, failing
that, to the deterministic echo provider -- which answers, so nothing looked broken
while every classifier in the platform quietly fell back to its keyword rules.

The retry lives inside the provider rather than as a second entry in the vendor chain
because that chain deliberately drops the requested model when it switches vendor.
"""

from types import SimpleNamespace

import pytest

from app.core.config import Settings, _configured_keys, settings
from app.core.llm.providers import BedrockProvider
from app.core.llm.fallback import generate_with_fallback
from app.core.llm.providers import OpenAIProvider
from app.core.llm.router import LLMRouter


class _Quota(Exception):
    """Stands in for a vendor's rate-limit error, whatever its class."""


def _response(text: str = "ok"):
    return SimpleNamespace(
        text=text,
        content=text,
        response_metadata={"model_name": "resolved-model"},
        usage_metadata={"input_tokens": 3, "output_tokens": 1, "input_token_details": {}},
    )


class _KeyedFactory:
    """A model factory whose behaviour is chosen per credential."""

    def __init__(self, **by_key):
        self.by_key = by_key
        self.calls: list[tuple[str, str]] = []

    def __call__(self, _provider, *, api_key, model):
        behaviour = self.by_key[api_key]

        def invoke(_messages):
            self.calls.append((api_key, model))
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour

        return SimpleNamespace(invoke=invoke)


def test_a_spare_key_answers_when_the_first_is_exhausted():
    factory = _KeyedFactory(key1=_Quota("429 RESOURCE_EXHAUSTED"), key2=_response())
    provider = OpenAIProvider(("key1", "key2"), model_factory=factory)

    result = provider.generate([{"role": "user", "content": "hello"}])

    assert result.content == "ok"
    assert result.provider == "openai"
    assert [key for key, _ in factory.calls] == ["key1", "key2"]


def test_the_requested_model_survives_the_switch_between_keys():
    """The whole reason this is not a second entry in the vendor chain."""
    factory = _KeyedFactory(key1=_Quota("429"), key2=_response())
    provider = OpenAIProvider(("key1", "key2"), model_factory=factory)

    provider.generate([{"role": "user", "content": "hello"}], model="gpt-4o")

    assert factory.calls == [("key1", "gpt-4o"), ("key2", "gpt-4o")]


def test_the_spare_key_is_not_touched_while_the_first_one_works():
    factory = _KeyedFactory(key1=_response(), key2=_response("second"))
    provider = OpenAIProvider(("key1", "key2"), model_factory=factory)

    result = provider.generate([{"role": "user", "content": "hello"}])

    assert result.content == "ok"
    assert [key for key, _ in factory.calls] == ["key1"]


def test_the_last_error_is_raised_when_every_key_fails():
    """It must still raise, so the vendor chain moves on to the next provider."""
    factory = _KeyedFactory(key1=_Quota("first"), key2=_Quota("second"))
    provider = OpenAIProvider(("key1", "key2"), model_factory=factory)

    with pytest.raises(_Quota, match="second"):
        provider.generate([{"role": "user", "content": "hello"}])


def test_an_exhausted_vendor_still_hands_over_to_the_next_one():
    factory = _KeyedFactory(key1=_Quota("first"), key2=_Quota("second"))
    exhausted = OpenAIProvider(("key1", "key2"), model_factory=factory)
    answering = SimpleNamespace(
        name="gemini",
        generate=lambda messages, *, model=None: SimpleNamespace(provider="gemini"),
    )

    result = generate_with_fallback(
        [exhausted, answering], [{"role": "user", "content": "hello"}]
    )

    assert result.provider == "gemini"
    assert len(factory.calls) == 2


def test_a_single_key_behaves_exactly_as_before():
    factory = _KeyedFactory(key1=_response())
    provider = OpenAIProvider("key1", model_factory=factory)

    assert provider.api_keys == ("key1",)
    assert provider.api_key == "key1"
    assert provider.generate([{"role": "user", "content": "hello"}]).content == "ok"


def test_blank_and_duplicate_credentials_are_dropped():
    assert _configured_keys("key1", None) == ("key1",)
    assert _configured_keys("key1", "   ") == ("key1",)
    # The same key twice would be retried after it failed, which wastes a call and
    # makes the log read as though a spare existed.
    assert _configured_keys("key1", "key1") == ("key1",)
    assert _configured_keys(" key1 ", "key2") == ("key1", "key2")
    assert _configured_keys(None, "key2") == ("key2",)


def test_a_vendor_without_any_credential_still_gets_one_slot():
    """Bedrock authenticates its boto3 client, not the request, so it has no key.

    Rejecting the empty case made enabling Bedrock raise while the router was still
    being assembled, which took every other vendor down with it.
    """
    provider = BedrockProvider(model_factory=_KeyedFactory())

    assert provider.api_keys == ("",)
    assert provider.api_key == ""


def test_a_keyless_vendor_calls_its_factory_exactly_once():
    factory = _KeyedFactory(**{"": _response()})
    provider = BedrockProvider(model_factory=factory)

    result = provider.generate([{"role": "user", "content": "hello"}])

    assert result.provider == "bedrock"
    assert factory.calls == [("", settings.BEDROCK_CHAT_MODEL)]


def test_the_router_builds_one_vendor_entry_carrying_both_keys(monkeypatch):
    monkeypatch.setattr("app.core.llm.router.settings.LLM_DEFAULT_PROVIDER", "openai")
    monkeypatch.setattr("app.core.llm.router.settings.OPENAI_API_KEY", "key1")
    monkeypatch.setattr("app.core.llm.router.settings.OPENAI_API_KEY_2", "key2")
    monkeypatch.setattr("app.core.llm.router.settings.GOOGLE_AI_API_KEY", None)
    monkeypatch.setattr("app.core.llm.router.settings.GOOGLE_AI_API_KEY_2", None)

    providers = LLMRouter().providers()

    assert [provider.name for provider in providers] == ["openai", "bedrock", "local"]
    assert providers[0].api_keys == ("key1", "key2")


def test_the_second_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key1")
    monkeypatch.setenv("OPENAI_API_KEY_2", "env-key2")
    monkeypatch.setenv("GOOGLE_AI_API_KEY_2", "env-google2")

    loaded = Settings(_env_file=None)

    assert loaded.openai_api_keys == ("env-key1", "env-key2")
    assert loaded.google_api_keys[-1] == "env-google2"
