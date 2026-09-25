"""Deterministic complexity scoring and model selection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel

from app.governance.middleware.context import AgentRuntimeContext


COMPLEX_MARKERS = {
    "analyze", "audit", "compare", "contract", "legal", "architecture",
    "root cause", "multi-step", "reconcile", "risk", "strategy",
}


def complexity_score(request: ModelRequest) -> int:
    text = " ".join(str(getattr(message, "content", "")) for message in request.messages).lower()
    score = min(len(text) // 1500, 4)
    score += min(len(request.messages) // 6, 3)
    score += 2 if len(request.tools) >= 4 else 0
    score += sum(1 for marker in COMPLEX_MARKERS if marker in text)
    return score


class ComplexityModelSelectionMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        simple_model: BaseChatModel,
        complex_model: BaseChatModel,
        complex_threshold: int = 4,
    ) -> None:
        super().__init__()
        self.simple_model = simple_model
        self.complex_model = complex_model
        self.complex_threshold = complex_threshold

    def select(self, request: ModelRequest) -> BaseChatModel:
        context = getattr(request.runtime, "context", None)
        hint = context.complexity_hint if isinstance(context, AgentRuntimeContext) else None
        if hint == "complex":
            return self.complex_model
        if hint == "simple":
            return self.simple_model
        if hint == "standard":
            return request.model
        return self.complex_model if complexity_score(request) >= self.complex_threshold else self.simple_model

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(request.override(model=self.select(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(request.override(model=self.select(request)))
