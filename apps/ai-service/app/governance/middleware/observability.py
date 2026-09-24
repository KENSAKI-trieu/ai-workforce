"""Model usage, cost and latency telemetry middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from app.governance.middleware.context import AgentRuntimeContext


@dataclass(frozen=True)
class ModelUsageRecord:
    tenant_id: str
    correlation_id: str
    agent_role: str
    provider: str
    model: str
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str = "SUCCESS"


class TelemetrySink(Protocol):
    def record(self, record: ModelUsageRecord) -> None: ...

    async def arecord(self, record: ModelUsageRecord) -> None: ...


class InMemoryTelemetrySink:
    def __init__(self) -> None:
        self.records: list[ModelUsageRecord] = []

    def record(self, record: ModelUsageRecord) -> None:
        self.records.append(record)

    async def arecord(self, record: ModelUsageRecord) -> None:
        self.record(record)


class GatewayTelemetrySink:
    def __init__(self, gateway: object) -> None:
        self.gateway = gateway

    def record(self, record: ModelUsageRecord) -> None:
        self.gateway.record_model_usage(asdict(record))

    async def arecord(self, record: ModelUsageRecord) -> None:
        await self.gateway.arecord_model_usage(asdict(record))


def _provider_and_model(request: ModelRequest, message: AIMessage) -> tuple[str, str]:
    response_metadata = message.response_metadata or {}
    model = str(
        response_metadata.get("model_name")
        or response_metadata.get("model")
        or getattr(request.model, "model_name", None)
        or getattr(request.model, "model", None)
        or request.model.__class__.__name__
    )
    provider = str(
        response_metadata.get("model_provider")
        or getattr(request.model, "_llm_type", "unknown")
    )
    return provider, model


def _usage_record(
    request: ModelRequest,
    response: ModelResponse,
    latency_ms: int,
) -> ModelUsageRecord | None:
    context = getattr(request.runtime, "context", None)
    if not isinstance(context, AgentRuntimeContext):
        return None
    message = next((item for item in reversed(response.result) if isinstance(item, AIMessage)), None)
    if message is None:
        return None
    usage = message.usage_metadata or {}
    details = usage.get("input_token_details") or {}
    provider, model = _provider_and_model(request, message)
    return ModelUsageRecord(
        tenant_id=str(context.tenant_id),
        correlation_id=str(context.correlation_id),
        agent_role=context.agent_role,
        provider=provider,
        model=model,
        prompt_tokens=int(usage.get("input_tokens", 0) or 0),
        cached_prompt_tokens=int(details.get("cache_read", details.get("cached_tokens", 0)) or 0),
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
        latency_ms=latency_ms,
    )


def _failure_record(request: ModelRequest, latency_ms: int) -> ModelUsageRecord | None:
    context = getattr(request.runtime, "context", None)
    if not isinstance(context, AgentRuntimeContext):
        return None
    model = str(
        getattr(request.model, "model_name", None)
        or getattr(request.model, "model", None)
        or request.model.__class__.__name__
    )
    return ModelUsageRecord(
        tenant_id=str(context.tenant_id),
        correlation_id=str(context.correlation_id),
        agent_role=context.agent_role,
        provider=str(getattr(request.model, "_llm_type", "unknown")),
        model=model,
        prompt_tokens=0,
        cached_prompt_tokens=0,
        completion_tokens=0,
        latency_ms=latency_ms,
        status="FAILED",
    )


class ModelTelemetryMiddleware(AgentMiddleware):
    """Record each physical provider call, including calls later rejected and retried."""

    def __init__(self, sink: TelemetrySink) -> None:
        super().__init__()
        self.sink = sink

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception:
            record = _failure_record(request, int((time.perf_counter() - started) * 1000))
            if record is not None:
                try:
                    self.sink.record(record)
                except Exception:
                    pass
            raise
        record = _usage_record(request, response, int((time.perf_counter() - started) * 1000))
        if record is not None:
            try:
                self.sink.record(record)
            except Exception:
                # Telemetry must not turn a valid model response into an outage.
                pass
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        started = time.perf_counter()
        try:
            response = await handler(request)
        except Exception:
            record = _failure_record(request, int((time.perf_counter() - started) * 1000))
            if record is not None:
                try:
                    await self.sink.arecord(record)
                except Exception:
                    pass
            raise
        record = _usage_record(request, response, int((time.perf_counter() - started) * 1000))
        if record is not None:
            try:
                await self.sink.arecord(record)
            except Exception:
                pass
        return response
