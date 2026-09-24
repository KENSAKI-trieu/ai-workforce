from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.chains.rag_answer import generate_rag_answer
from app.chains.structured_extraction import (
    extract_agent_routing,
    extract_contract_findings,
    invoke_structured,
)
from app.llm.base import LLMProvider, LLMResult
from app.llm.fallback import generate_with_fallback
from app.llm.openai_provider import OpenAIProvider
from app.llm.router import LLMRouter
from app.models.factory import configured_chat_models, create_chat_model
from app.schemas.citations import RAGAnswer
from app.schemas.routing import AgentRoutingDecision


class _StructuredRunnable:
    def __init__(self, response):
        self.response = response

    def invoke(self, messages):
        return self.response


class _FakeChatModel:
    def __init__(self, response):
        self.response = response
        self.method = None

    def with_structured_output(self, schema, *, method):
        self.method = method
        return _StructuredRunnable(self.response)


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

    monkeypatch.setattr("app.models.factory.ChatOpenAI", fake_openai)
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
    monkeypatch.setattr("app.llm.router.settings.LLM_DEFAULT_PROVIDER", "gemini")
    monkeypatch.setattr("app.llm.router.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.llm.router.settings.GOOGLE_AI_API_KEY", "gemini-key")

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
    monkeypatch.setattr("app.models.factory.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.models.factory.settings.GOOGLE_AI_API_KEY", "gemini-key")
    monkeypatch.setattr("app.models.factory.settings.BEDROCK_ENABLED", False)

    monkeypatch.setattr("app.models.factory.settings.LLM_DEFAULT_PROVIDER", "gemini")
    assert [type(model).__name__ for model in configured_chat_models()] == [
        "ChatGoogleGenerativeAI", "ChatOpenAI",
    ]
    # An explicit provider still wins over the configured default.
    assert type(configured_chat_models("openai")[0]).__name__ == "ChatOpenAI"

    monkeypatch.setattr("app.models.factory.settings.LLM_DEFAULT_PROVIDER", "auto")
    assert type(configured_chat_models()[0]).__name__ == "ChatOpenAI"

    monkeypatch.setattr("app.models.factory.settings.LLM_DEFAULT_PROVIDER", "local")
    assert configured_chat_models() == []


@pytest.mark.bedrock_flag
def test_router_leaves_bedrock_out_while_the_flag_is_off(monkeypatch) -> None:
    """The flag is the only thing gating it: there is no key to be missing."""
    monkeypatch.setattr("app.llm.router.settings.BEDROCK_ENABLED", False)
    monkeypatch.setattr("app.llm.router.settings.LLM_DEFAULT_PROVIDER", "auto")
    monkeypatch.setattr("app.llm.router.settings.OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("app.llm.router.settings.GOOGLE_AI_API_KEY", "gemini-key")

    assert "bedrock" not in [provider.name for provider in LLMRouter().providers()]


def test_structured_output_returns_validated_pydantic_model() -> None:
    model = _FakeChatModel({
        "role": " finance ",
        "confidence": 0.9,
        "reason": "Budget intent",
    })
    result = invoke_structured(
        model,
        [{"role": "user", "content": "Review budget"}],
        AgentRoutingDecision,
    )
    assert isinstance(result, AgentRoutingDecision)
    assert result.role == "FINANCE"
    assert model.method == "json_schema"


def test_structured_output_rejects_invalid_schema() -> None:
    model = _FakeChatModel({"role": "FINANCE", "confidence": 2.0, "reason": "x"})
    with pytest.raises(ValidationError):
        invoke_structured(model, [], AgentRoutingDecision)


def test_agent_routing_retries_unregistered_role_then_falls_back() -> None:
    result = extract_agent_routing(
        "unknown intent",
        {"FINANCE": "finance operations"},
        models=[_FakeChatModel({
            "role": "UNREGISTERED",
            "confidence": 1.0,
            "reason": "bad route",
        })],
        fallback_role="FINANCE",
    )
    assert result.role == "FINANCE"
    assert result.confidence == 0.0


def test_contract_findings_reject_unsupported_evidence_and_retry() -> None:
    invalid = _FakeChatModel({
        "findings": [{
            "category": "PAYMENT",
            "finding_type": "LEGAL_ISSUE",
            "severity": "HIGH",
            "issue": "Unclear payment term",
            "reason": "The deadline is ambiguous",
            "recommendation": "Specify a fixed deadline",
            "evidence": "Payment is due after 90 days.",
            "confidence": 0.8,
        }],
        "summary": "One issue",
        "requires_legal_approval": True,
    })
    valid = _FakeChatModel({
        "findings": [{
            "category": "PAYMENT",
            "finding_type": "AMBIGUOUS_CLAUSE",
            "severity": "MEDIUM",
            "issue": "Unclear payment term",
            "reason": "The deadline is not measurable",
            "recommendation": "Specify a fixed deadline",
            "evidence": "Payment is due promptly.",
            "confidence": 0.9,
        }],
        "summary": "One supported issue",
        "requires_legal_approval": False,
    })
    result = extract_contract_findings(
        "Payment is due promptly.",
        models=[invalid, valid],
    )
    assert result.findings[0].severity == "MEDIUM"
    assert result.findings[0].evidence == "Payment is due promptly."


def test_rag_answer_retries_hallucinated_citation() -> None:
    chunk = {
        "id": "chunk-1",
        "document_title": "Leave policy",
        "section_title": "Allowance",
        "content": "Employees receive 12 leave days each year.",
    }
    bad = _FakeChatModel({
        "answer": "Twenty days.",
        "grounded": True,
        "citations": [{
            "document_title": "Leave policy",
            "chunk_id": "chunk-1",
            "quote": "Employees receive 20 leave days each year.",
        }],
    })
    good = _FakeChatModel({
        "answer": "Employees receive 12 leave days each year.",
        "grounded": True,
        "citations": [{
            "document_title": "Leave policy",
            "chunk_id": "chunk-1",
            "quote": "Employees receive 12 leave days each year.",
        }],
    })
    result = generate_rag_answer("How many leave days?", [chunk], models=[bad, good])
    assert isinstance(result, RAGAnswer)
    assert result.citations[0].chunk_id == "chunk-1"


def test_rag_answer_uses_existing_custom_fallback() -> None:
    result = generate_rag_answer(
        "Question",
        [{"source_file": "policy.pdf", "content": "Approved policy text."}],
        models=[],
    )
    assert result.grounded is True
    assert result.answer == "Approved policy text."
    assert result.citations[0].document_title == "policy.pdf"
