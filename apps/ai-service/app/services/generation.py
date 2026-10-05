from app.core.llm.base import LLMResult
from app.core.llm.catalog import provider_for_model
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
    # A model chosen for an agent arrives without its vendor. Sent to the default vendor
    # instead, a name it does not serve fails the call and the fallback drops the choice.
    if model and not provider:
        provider = provider_for_model(model)
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
