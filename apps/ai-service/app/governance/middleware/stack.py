"""Factory for the ordered production middleware stack."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.model_selection import ComplexityModelSelectionMiddleware
from app.governance.middleware.observability import ModelTelemetryMiddleware, TelemetrySink
from app.governance.middleware.output_validation import OutputCitationValidationMiddleware
from app.governance.middleware.redaction import PHONE_PATTERN
from app.governance.middleware.tenant_acl import TenantACLContextMiddleware
from app.core.config import settings


@dataclass(frozen=True)
class GovernedAgentConfig:
    max_model_calls: int = 8
    max_tool_calls: int = 12
    model_retries: int = 2
    retry_initial_delay: float = 0.25
    retry_max_delay: float = 2.0
    complex_threshold: int = 4


# Statuses a retry a few seconds later cannot change: an exhausted or rate-limited
# quota (the provider asks for tens of seconds, the backoff here is at most two),
# a rejected key, an unknown model. Retrying them only spends more of the quota;
# the fallback middleware moves on to the next provider instead.
NON_RETRYABLE_MODEL_STATUSES = frozenset({401, 403, 404, 429})


def is_retryable_model_error(exc: BaseException) -> bool:
    """Whether a failed model call is worth repeating against the same provider.

    Provider SDKs wrap the HTTP error (LangChain's Gemini error wraps the
    google-genai `ClientError`), so the status is read along the cause chain.
    Everything else, including a response that failed output validation, is
    retried as before.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ("status_code", "code"):
            status = getattr(current, attribute, None)
            if isinstance(status, int) and status in NON_RETRYABLE_MODEL_STATUSES:
                return False
        current = current.__cause__ or current.__context__
    return True


def governed_middleware(
    *,
    simple_model: BaseChatModel,
    complex_model: BaseChatModel,
    fallback_models: Sequence[BaseChatModel] = (),
    telemetry_sink: TelemetrySink,
    config: GovernedAgentConfig | None = None,
) -> list[AgentMiddleware]:
    policy = config or GovernedAgentConfig(
        max_model_calls=settings.AGENT_MAX_MODEL_CALLS,
        max_tool_calls=settings.AGENT_MAX_TOOL_CALLS,
        model_retries=settings.AGENT_MIDDLEWARE_MODEL_RETRIES,
        complex_threshold=settings.AGENT_COMPLEXITY_THRESHOLD,
    )
    middleware: list[AgentMiddleware] = [
        TenantACLContextMiddleware(),
        PIIMiddleware("email", strategy="redact", apply_to_input=True, apply_to_output=True, apply_to_tool_results=True),
        PIIMiddleware("credit_card", strategy="redact", apply_to_input=True, apply_to_output=True, apply_to_tool_results=True),
        PIIMiddleware("ip", strategy="redact", apply_to_input=True, apply_to_output=True, apply_to_tool_results=True),
        PIIMiddleware("phone_number", detector=PHONE_PATTERN, strategy="redact", apply_to_input=True, apply_to_output=True, apply_to_tool_results=True),
        ModelCallLimitMiddleware(run_limit=policy.max_model_calls, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=policy.max_tool_calls, exit_behavior="error"),
        ComplexityModelSelectionMiddleware(
            simple_model=simple_model,
            complex_model=complex_model,
            complex_threshold=policy.complex_threshold,
        ),
    ]
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*fallback_models))
    middleware.extend([
        ModelRetryMiddleware(
            max_retries=policy.model_retries,
            retry_on=is_retryable_model_error,
            on_failure="error",
            initial_delay=policy.retry_initial_delay,
            max_delay=policy.retry_max_delay,
            jitter=True,
        ),
        OutputCitationValidationMiddleware(),
        ModelTelemetryMiddleware(telemetry_sink),
    ])
    return middleware


def create_governed_agent(
    *,
    model: BaseChatModel,
    simple_model: BaseChatModel,
    complex_model: BaseChatModel,
    fallback_models: Sequence[BaseChatModel],
    tools: Sequence[BaseTool],
    telemetry_sink: TelemetrySink,
    system_prompt: str,
    response_format: Any | None = None,
    config: GovernedAgentConfig | None = None,
):
    """Create an agent whose cross-cutting controls live entirely in middleware."""
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=governed_middleware(
            simple_model=simple_model,
            complex_model=complex_model,
            fallback_models=fallback_models,
            telemetry_sink=telemetry_sink,
            config=config,
        ),
        response_format=response_format,
        context_schema=AgentRuntimeContext,
    )
