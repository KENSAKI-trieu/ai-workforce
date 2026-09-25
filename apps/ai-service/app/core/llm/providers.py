"""LLM providers behind /v1/llm/generate: one class per vendor, plus the offline local one.

Each vendor provider wraps LangChain chat models built by ``factory.create_chat_model``,
so the graph (which uses those chat models directly) and this text path share one
construction policy -- timeouts, retries, and the same credentials.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import settings
from app.core.llm.base import LLMProvider, LLMResult
from app.core.llm.factory import LangChainProvider, create_chat_model

logger = logging.getLogger(__name__)


def _message_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if callable(text):
        text = text()
    if isinstance(text, str):
        return text

    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None) or {}
    input_details = metadata.get("input_token_details") or {}
    return {
        "prompt_tokens": int(metadata.get("input_tokens", 0) or 0),
        "completion_tokens": int(metadata.get("output_tokens", 0) or 0),
        "cached_prompt_tokens": int(
            input_details.get("cache_read", input_details.get("cached_tokens", 0)) or 0
        ),
    }


class LangChainChatProvider(LLMProvider):
    """Compatibility adapter from LangChain AIMessage to the existing contract."""

    def __init__(
        self,
        provider: LangChainProvider,
        api_key: str | Sequence[str],
        default_model: str,
        *,
        model_factory: Callable[..., BaseChatModel] = create_chat_model,
    ) -> None:
        self.name = provider
        # More than one credential may be configured for the same provider. They are
        # tried in order within a single generate() call, which is not the same thing
        # as the provider chain in `fallback.py`: that one moves to a different vendor
        # and drops the requested model with it, because a model name does not travel
        # across vendors. A second key of the same vendor serves the same catalogue,
        # so the requested model has to survive the switch -- which is why the retry
        # belongs here rather than as another entry in that chain.
        keys = (api_key,) if isinstance(api_key, str) else tuple(api_key)
        # A vendor with no API key at all is legitimate: Bedrock authenticates its
        # boto3 client rather than the request, so it is constructed with an empty
        # credential on purpose. It still gets exactly one slot, which is what keeps
        # the loop below identical for every provider -- rejecting the empty case
        # here would make enabling Bedrock raise before a single call was made.
        self.api_keys: tuple[str, ...] = tuple(
            dict.fromkeys(key for key in keys if key)
        ) or ("",)
        # Kept for callers that read the primary credential; the list above is what
        # generate() actually walks.
        self.api_key = self.api_keys[0]
        self.default_model = default_model
        self._model_factory = model_factory
        self._models: dict[tuple[int, str], BaseChatModel] = {}

    def _model(self, key_index: int, name: str) -> BaseChatModel:
        cache_key = (key_index, name)
        if cache_key not in self._models:
            self._models[cache_key] = self._model_factory(
                self.name,
                api_key=self.api_keys[key_index],
                model=name,
            )
        return self._models[cache_key]

    def generate(self, messages: list[dict[str, str]], *, model: str | None = None) -> LLMResult:
        selected = model or self.default_model
        last_error: Exception | None = None
        for index in range(len(self.api_keys)):
            try:
                response = self._model(index, selected).invoke(messages)
            except Exception as exc:
                last_error = exc
                remaining = len(self.api_keys) - index - 1
                if not remaining:
                    raise
                # The key is never logged, only its position. Any failure moves on:
                # telling an exhausted quota apart from a malformed request would mean
                # parsing vendor-specific error shapes, and the cost of being wrong is
                # one extra doomed call rather than a wrong answer.
                logger.warning(
                    "LLM provider '%s' credential #%d failed with %s; trying the next key",
                    self.name,
                    index + 1,
                    exc.__class__.__name__,
                )
                continue
            response_metadata = getattr(response, "response_metadata", None) or {}
            resolved_model = (
                response_metadata.get("model_name")
                or response_metadata.get("model")
                or selected
            )
            return LLMResult(
                content=_message_text(response),
                model=str(resolved_model),
                provider=self.name,
                usage=_usage(response),
            )
        raise last_error if last_error else RuntimeError("no credential was tried")


class OpenAIProvider(LangChainChatProvider):
    name = "openai"

    def __init__(self, api_key: str | Sequence[str], default_model: str | None = None, **kwargs) -> None:
        super().__init__("openai", api_key, default_model or settings.OPENAI_CHAT_MODEL, **kwargs)


class GeminiProvider(LangChainChatProvider):
    name = "gemini"

    def __init__(self, api_key: str | Sequence[str], default_model: str | None = None, **kwargs) -> None:
        super().__init__("gemini", api_key, default_model or settings.GEMINI_CHAT_MODEL, **kwargs)


class BedrockProvider(LangChainChatProvider):
    """Claude on Amazon Bedrock.

    Unlike the other providers there is no API key: the boto3 client carries the
    credentials, so the inherited api_key field is left empty on purpose.
    """

    name = "bedrock"

    def __init__(self, default_model: str | None = None, **kwargs) -> None:
        super().__init__("bedrock", "", default_model or settings.BEDROCK_CHAT_MODEL, **kwargs)


class LocalProvider(LLMProvider):
    """Offline echo used when no vendor is configured or all of them failed."""

    name = "local"

    def generate(self, messages: list[dict[str, str]], *, model: str | None = None) -> LLMResult:
        last_user_message = next(
            (item["content"] for item in reversed(messages) if item["role"] == "user"),
            "",
        )
        return LLMResult(
            content=f"Local provider received: {last_user_message}",
            model=model or "local-deterministic",
            provider=self.name,
            usage={"prompt_tokens": sum(len(item["content"].split()) for item in messages), "completion_tokens": 4},
        )
