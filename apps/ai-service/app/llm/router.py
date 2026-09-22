from app.config import settings
from app.llm.base import LLMProvider
from app.llm.bedrock_provider import BedrockProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.local_provider import LocalProvider
from app.llm.openai_provider import OpenAIProvider


class LLMRouter:
    def providers(self, name: str | None = None) -> list[LLMProvider]:
        """Return an ordered provider list ending in the custom local fallback."""
        configured_default = settings.LLM_DEFAULT_PROVIDER
        default_name = "" if configured_default == "auto" else configured_default
        selected = (name or default_name).lower()
        if selected not in {"", "openai", "gemini", "bedrock", "local"}:
            return [LocalProvider()]
        if selected == "local":
            return [LocalProvider()]

        order = [selected] if selected else []
        order.extend(
            item for item in ("bedrock", "openai", "gemini") if item not in order
        )
        providers: list[LLMProvider] = []
        for provider_name in order:
            # One entry per vendor carrying every credential configured for it. A
            # spare key is not a separate entry here: entries in this list are
            # vendors, and moving between them drops the requested model.
            if provider_name == "openai" and settings.openai_api_keys:
                providers.append(OpenAIProvider(settings.openai_api_keys))
            if provider_name == "gemini" and settings.google_api_keys:
                providers.append(GeminiProvider(settings.google_api_keys))
            # Bedrock is gated on a flag rather than a key, because its
            # credentials come from the boto3 chain rather than configuration.
            if provider_name == "bedrock" and settings.BEDROCK_ENABLED:
                providers.append(BedrockProvider())
        providers.append(LocalProvider())
        return providers

    def provider(self, name: str | None = None) -> LLMProvider:
        return self.providers(name)[0]
