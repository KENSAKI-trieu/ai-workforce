from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
)
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime

from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.model_selection import ComplexityModelSelectionMiddleware
from app.governance.middleware.observability import InMemoryTelemetrySink, ModelTelemetryMiddleware
from app.governance.middleware.output_validation import OutputCitationValidationMiddleware
from app.governance.middleware.stack import GovernedAgentConfig, governed_middleware, is_retryable_model_error
from app.governance.middleware.tenant_acl import TenantACLContextMiddleware
from app.schemas.citations import Citation, RAGAnswer


def _context(**overrides):
    values = {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "role": "Manager",
        "department": "HR",
        "agent_role": "HR",
        "correlation_id": uuid.uuid4(),
        "allowed_tools": frozenset({"create_task", "rag_search"}),
    }
    values.update(overrides)
    return AgentRuntimeContext(**values)


def _model_request(context, *, text="hello", tools=None, model=None):
    return ModelRequest(
        model=model or FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(text)],
        tools=tools or [],
        runtime=Runtime(context=context),
    )


def _tool(name: str, action: str = "READ_ONLY"):
    def execute(**kwargs):
        return kwargs

    return StructuredTool.from_function(
        func=execute,
        name=name,
        description=f"Test {name}",
        metadata={
            "action": action,
            "allowed_roles": ["*"],
            "allowed_departments": ["*"],
            "acl_match": "ROLE_AND_DEPARTMENT",
        },
    )


def test_tenant_acl_filters_tools_and_injects_system_context() -> None:
    allowed = _tool("rag_search")
    denied = _tool("expense_lookup")
    request = _model_request(_context(), tools=[allowed, denied])
    captured = {}

    def handler(updated):
        captured["request"] = updated
        return ModelResponse(result=[AIMessage("ok")])

    TenantACLContextMiddleware().wrap_model_call(request, handler)
    updated = captured["request"]
    assert [tool.name for tool in updated.tools] == ["rag_search"]
    assert str(request.runtime.context.tenant_id) in updated.system_message.text
    assert "department=HR" in updated.system_message.text


def test_tenant_acl_overwrites_model_tool_context_and_denies_unlisted_tool() -> None:
    context = _context()
    task_tool = _tool("create_task", "WRITE")
    request = ToolCallRequest(
        tool_call={
            "name": "create_task",
            "args": {"tenant_id": str(uuid.uuid4()), "title": "A task"},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=task_tool,
        state={},
        runtime=SimpleNamespace(context=context),
    )

    def handler(updated):
        arguments = updated.tool_call["args"]
        assert arguments["tenant_id"] == str(context.tenant_id)
        assert arguments["audit"]["correlation_id"] == str(context.correlation_id)
        assert arguments["audit"]["idempotency_key"].endswith(":call-1")
        return ToolMessage("ok", tool_call_id="call-1")

    result = TenantACLContextMiddleware().wrap_tool_call(request, handler)
    assert result.content == "ok"

    blocked = request.override(tool_call={**request.tool_call, "name": "expense_lookup"})
    with pytest.raises(PermissionError):
        TenantACLContextMiddleware().wrap_tool_call(blocked, handler)


def test_complexity_model_selection_uses_score_and_trusted_hint() -> None:
    simple = FakeListChatModel(responses=["simple"])
    complex_model = FakeListChatModel(responses=["complex"])
    middleware = ComplexityModelSelectionMiddleware(
        simple_model=simple,
        complex_model=complex_model,
        complex_threshold=2,
    )
    simple_request = _model_request(_context(complexity_hint="simple"))
    complex_request = _model_request(
        _context(),
        text="Analyze legal contract risk and compare architecture strategy",
        tools=[_tool("rag_search"), _tool("create_task"), _tool("one"), _tool("two")],
    )
    assert middleware.select(simple_request) is simple
    assert middleware.select(complex_request) is complex_model


def test_output_validation_retries_bad_citation_and_records_each_attempt() -> None:
    context = _context(
        citation_required=True,
        allowed_citation_sources=frozenset({"Leave Policy"}),
    )
    request = _model_request(context)
    sink = InMemoryTelemetrySink()
    telemetry = ModelTelemetryMiddleware(sink)
    validator = OutputCitationValidationMiddleware()
    retry = ModelRetryMiddleware(
        max_retries=1,
        on_failure="error",
        initial_delay=0,
        max_delay=0,
        jitter=False,
    )
    responses = iter([
        AIMessage(
            "Unsupported answer [Citation: Unknown]",
            usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            response_metadata={"model_name": "gpt-4o"},
        ),
        AIMessage(
            "Supported answer [Citation: Leave Policy]",
            usage_metadata={"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
            response_metadata={"model_name": "gpt-4o"},
        ),
    ])

    def physical_call(current_request):
        return ModelResponse(result=[next(responses)])

    def validated_call(current_request):
        return validator.wrap_model_call(
            current_request,
            lambda req: telemetry.wrap_model_call(req, physical_call),
        )

    response = retry.wrap_model_call(request, validated_call)
    assert "Leave Policy" in response.result[0].content
    assert len(sink.records) == 2
    assert sum(record.prompt_tokens for record in sink.records) == 21


def test_output_validation_checks_structured_citation_source() -> None:
    request = _model_request(_context(
        citation_required=True,
        allowed_citation_sources=frozenset({"policy-1"}),
    ))
    valid = RAGAnswer(
        answer="Twelve days.",
        grounded=True,
        citations=[Citation(
            document_id="policy-1",
            document_title="Leave Policy",
            quote="12 days",
        )],
    )
    middleware = OutputCitationValidationMiddleware()
    response = middleware.wrap_model_call(
        request,
        lambda _: ModelResponse(result=[AIMessage("Twelve days.")], structured_response=valid),
    )
    assert response.structured_response is valid

    invalid = valid.model_copy(update={
        "citations": [Citation(document_id="other", document_title="Other", quote="x")]
    })
    with pytest.raises(ValueError, match="outside"):
        middleware.wrap_model_call(
            request,
            lambda _: ModelResponse(result=[AIMessage("Unsupported")], structured_response=invalid),
        )


def test_governed_stack_contains_pii_limits_retry_and_fallback() -> None:
    base = FakeListChatModel(responses=["base"])
    fallback = FakeListChatModel(responses=["fallback"])
    sink = InMemoryTelemetrySink()
    middleware = governed_middleware(
        simple_model=base,
        complex_model=base,
        fallback_models=[fallback],
        telemetry_sink=sink,
    )
    assert sum(isinstance(item, PIIMiddleware) for item in middleware) == 4
    assert any(isinstance(item, ModelCallLimitMiddleware) for item in middleware)
    assert any(isinstance(item, ToolCallLimitMiddleware) for item in middleware)
    assert any(isinstance(item, ModelRetryMiddleware) for item in middleware)
    assert any(isinstance(item, ModelFallbackMiddleware) for item in middleware)
    selector_index = next(i for i, item in enumerate(middleware) if isinstance(item, ComplexityModelSelectionMiddleware))
    fallback_index = next(i for i, item in enumerate(middleware) if isinstance(item, ModelFallbackMiddleware))
    retry_index = next(i for i, item in enumerate(middleware) if isinstance(item, ModelRetryMiddleware))
    validator_index = next(i for i, item in enumerate(middleware) if isinstance(item, OutputCitationValidationMiddleware))
    telemetry_index = next(i for i, item in enumerate(middleware) if isinstance(item, ModelTelemetryMiddleware))
    assert selector_index < fallback_index < retry_index < validator_index < telemetry_index

    email_guard = next(item for item in middleware if isinstance(item, PIIMiddleware) and item.pii_type == "email")
    update = email_guard.before_model(
        {"messages": [HumanMessage("Contact employee@example.com")]},
        Runtime(),
    )
    assert update["messages"][0].content == "Contact [REDACTED_EMAIL]"


def _provider_errors() -> dict[str, Exception]:
    import httpx
    import openai
    from google.genai.errors import ClientError
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

    def gemini(status: int, reason: str) -> Exception:
        # LangChain re-raises the google-genai error as its own, keeping it as the cause.
        wrapped = ChatGoogleGenerativeAIError(f"Error calling model ({reason})")
        wrapped.__cause__ = ClientError(status, {"error": {"code": status, "message": reason, "status": reason}})
        return wrapped

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return {
        "gemini_quota": gemini(429, "RESOURCE_EXHAUSTED"),
        "gemini_unknown_model": gemini(404, "NOT_FOUND"),
        "openai_no_credit": openai.RateLimitError(
            "You have no credits remaining", response=httpx.Response(429, request=request), body=None
        ),
        "openai_bad_key": openai.AuthenticationError(
            "Incorrect API key", response=httpx.Response(401, request=request), body=None
        ),
    }


def _governed_retry() -> ModelRetryMiddleware:
    base = FakeListChatModel(responses=["base"])
    middleware = governed_middleware(
        simple_model=base,
        complex_model=base,
        telemetry_sink=InMemoryTelemetrySink(),
        config=GovernedAgentConfig(model_retries=2, retry_initial_delay=0, retry_max_delay=0),
    )
    return next(item for item in middleware if isinstance(item, ModelRetryMiddleware))


@pytest.mark.parametrize("name", ["gemini_quota", "gemini_unknown_model", "openai_no_credit", "openai_bad_key"])
def test_a_call_no_retry_can_fix_is_not_repeated(name: str) -> None:
    """An exhausted quota answered the same way three times over, spending three
    requests of a 20-a-day free tier on every failed turn."""
    error = _provider_errors()[name]
    assert is_retryable_model_error(error) is False
    calls = []

    def failing(_request):
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        _governed_retry().wrap_model_call(_model_request(_context()), failing)
    assert len(calls) == 1


def test_a_transient_or_rejected_response_is_still_retried() -> None:
    calls = []

    def flaky(_request):
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("Grounded output requires at least one [Citation: source] marker")
        return ModelResponse(result=[AIMessage("ok")])

    response = _governed_retry().wrap_model_call(_model_request(_context()), flaky)
    assert response.result[0].content == "ok"
    assert len(calls) == 3
    assert is_retryable_model_error(TimeoutError("read timed out")) is True
