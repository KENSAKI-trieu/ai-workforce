from collections.abc import Sequence

from app.config import settings
from app.llm.langchain_provider import LangChainChatProvider


class GeminiProvider(LangChainChatProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | Sequence[str],
        default_model: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            "gemini",
            api_key,
            default_model or settings.GEMINI_CHAT_MODEL,
            **kwargs,
        )
