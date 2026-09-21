from app.config import settings
from app.llm.langchain_provider import LangChainChatProvider


class BedrockProvider(LangChainChatProvider):
    """Claude on Amazon Bedrock.

    Unlike the other providers there is no API key: the boto3 client carries the
    credentials, so the inherited api_key field is left empty on purpose.
    """

    name = "bedrock"

    def __init__(self, default_model: str | None = None, **kwargs) -> None:
        super().__init__(
            "bedrock",
            "",
            default_model or settings.BEDROCK_CHAT_MODEL,
            **kwargs,
        )
