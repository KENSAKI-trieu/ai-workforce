from collections.abc import Sequence

from app.config import settings
from app.llm.langchain_provider import LangChainChatProvider


class OpenAIProvider(LangChainChatProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str | Sequence[str],
        default_model: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            "openai",
            api_key,
            default_model or settings.OPENAI_CHAT_MODEL,
            **kwargs,
        )
