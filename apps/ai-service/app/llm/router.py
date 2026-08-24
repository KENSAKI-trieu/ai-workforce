from app.config import settings
from app.llm.base import LLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.local_provider import LocalProvider
from app.llm.openai_provider import OpenAIProvider


class LLMRouter:
    def providers(self, name: str | None = None) -> list[LLMProvider]:
        """Return an ordered provider list ending in the custom local fallback."""
        configured_default = settings.LLM_DEFAULT_PROVIDER
        default_name = "" if configured_default == "auto" else configured_default
        selected = (name or default_name).lower()
        if selected not in {"", "openai", "gemini", "local"}:
            return [LocalProvider()]
        if selected == "local":
            return [LocalProvider()]

        order = [selected] if selected else []
        order.extend(item for item in ("openai", "gemini") if item not in order)
        providers: list[LLMProvider] = []
        for provider_name in order:
            if provider_name == "openai" and settings.OPENAI_API_KEY:
                providers.append(OpenAIProvider(settings.OPENAI_API_KEY))
            if provider_name == "gemini" and settings.GOOGLE_AI_API_KEY:
                providers.append(GeminiProvider(settings.GOOGLE_AI_API_KEY))
        providers.append(LocalProvider())
        return providers

    def provider(self, name: str | None = None) -> LLMProvider:
        return self.providers(name)[0]
