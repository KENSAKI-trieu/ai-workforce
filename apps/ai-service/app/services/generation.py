from app.core.llm.base import LLMResult
from app.core.llm.fallback import generate_with_fallback
from app.core.llm.router import LLMRouter


def generate_chat(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    router: LLMRouter | None = None,
) -> LLMResult:
    """Invoke LangChain chat models while preserving the legacy LLMResult."""
    selected_router = router or LLMRouter()
    providers = selected_router.providers(provider)
    requested_provider = (provider or "").lower()
    selected_model = model
    if requested_provider and providers[0].name != requested_provider:
        selected_model = None
    return generate_with_fallback(
        providers,
        messages,
        model=selected_model,
    )
