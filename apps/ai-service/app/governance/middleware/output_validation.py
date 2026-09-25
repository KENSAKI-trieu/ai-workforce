"""Validate final model output and grounded citation markers."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from app.governance.middleware.context import AgentRuntimeContext


CITATION_PATTERN = re.compile(r"\[Citation:\s*([^\]]+)\]", re.IGNORECASE)


def _response_message(response: ModelResponse) -> AIMessage | None:
    return next((message for message in reversed(response.result) if isinstance(message, AIMessage)), None)


def validate_model_response(request: ModelRequest, response: ModelResponse) -> None:
    message = _response_message(response)
    if message is None:
        raise ValueError("Model response does not contain an AI message")
    if message.tool_calls:
        return
    content = message.text.strip()
    if not content and response.structured_response is None:
        raise ValueError("Final model output is empty")

    context = getattr(request.runtime, "context", None)
    if not isinstance(context, AgentRuntimeContext) or not context.citation_required:
        return
    structured = response.structured_response
    structured_citations = getattr(structured, "citations", None)
    if structured_citations is not None:
        if getattr(structured, "grounded", False) and not structured_citations:
            raise ValueError("Grounded structured output requires citations")
        if context.allowed_citation_sources:
            allowed = {source.casefold() for source in context.allowed_citation_sources}
            for citation in structured_citations:
                identifiers = {
                    str(value).casefold()
                    for value in (
                        getattr(citation, "document_title", None),
                        getattr(citation, "document_id", None),
                        getattr(citation, "chunk_id", None),
                    )
                    if value
                }
                if not identifiers & allowed:
                    raise ValueError("Structured output contains a citation outside the supplied source context")
        return
    citations = [value.strip() for value in CITATION_PATTERN.findall(content)]
    if not citations:
        raise ValueError("Grounded output requires at least one [Citation: source] marker")
    if context.allowed_citation_sources:
        allowed = {source.casefold() for source in context.allowed_citation_sources}
        invalid = [citation for citation in citations if citation.casefold() not in allowed]
        if invalid:
            raise ValueError("Output contains a citation outside the supplied source context")


class OutputCitationValidationMiddleware(AgentMiddleware):
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        response = handler(request)
        validate_model_response(request, response)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        validate_model_response(request, response)
        return response
