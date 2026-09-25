from app.core.config import settings
from app.core.llm.base import LLMProvider
from app.core.llm.factory import PROVIDER_ORDER, provider_keys
from app.core.llm.providers import BedrockProvider, GeminiProvider, LocalProvider, OpenAIProvider


class LLMRouter:
    def providers(self, name: str | None = None) -> list[LLMProvider]:
        """Return an ordered provider list ending in the custom local fallback."""
        configured_default = settings.LLM_DEFAULT_PROVIDER
        default_name = "" if configured_default == "auto" else configured_default
        selected = (name or default_name).lower()
        if selected not in {"", *PROVIDER_ORDER}:
            return [LocalProvider()]

        order = [selected] if selected else []
        order.extend(item for item in PROVIDER_ORDER if item not in order)
        providers: list[LLMProvider] = []
        for provider_name in order:
            # One entry per vendor carrying every credential configured for it. A
            # spare key is not a separate entry here: entries in this list are
            # vendors, and moving between them drops the requested model.
            keys = provider_keys(provider_name)
            if not keys:
                continue
            if provider_name == "openai":
                providers.append(OpenAIProvider(keys))
            elif provider_name == "gemini":
                providers.append(GeminiProvider(keys))
            elif provider_name == "bedrock":
                # Credentials come from the boto3 chain; BEDROCK_ENABLED is the gate.
                providers.append(BedrockProvider())
        providers.append(LocalProvider())
        return providers

    def provider(self, name: str | None = None) -> LLMProvider:
        return self.providers(name)[0]
