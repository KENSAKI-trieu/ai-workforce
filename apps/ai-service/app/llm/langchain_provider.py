from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.llm.base import LLMProvider, LLMResult
from app.models.factory import LangChainProvider, create_chat_model


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
        api_key: str,
        default_model: str,
        *,
        model_factory: Callable[..., BaseChatModel] = create_chat_model,
    ) -> None:
        self.name = provider
        self.api_key = api_key
        self.default_model = default_model
        self._model_factory = model_factory
        self._models: dict[str, BaseChatModel] = {}

    def _model(self, name: str) -> BaseChatModel:
        if name not in self._models:
            self._models[name] = self._model_factory(
                self.name,
                api_key=self.api_key,
                model=name,
            )
        return self._models[name]

    def generate(self, messages: list[dict[str, str]], *, model: str | None = None) -> LLMResult:
        selected = model or self.default_model
        response = self._model(selected).invoke(messages)
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
