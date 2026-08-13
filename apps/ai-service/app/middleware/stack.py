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

from app.middleware.context import AgentRuntimeContext
from app.middleware.model_selection import ComplexityModelSelectionMiddleware
from app.middleware.observability import ModelTelemetryMiddleware, TelemetrySink
from app.middleware.output_validation import OutputCitationValidationMiddleware
from app.middleware.redaction import PHONE_PATTERN
from app.middleware.tenant_acl import TenantACLContextMiddleware
from app.config import settings


@dataclass(frozen=True)
class GovernedAgentConfig:
    max_model_calls: int = 8
    max_tool_calls: int = 12
    model_retries: int = 2
    retry_initial_delay: float = 0.25
    retry_max_delay: float = 2.0
    complex_threshold: int = 4


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
