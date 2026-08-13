from app.llm.base import LLMProvider, LLMResult


def generate_with_fallback(
    providers: list[LLMProvider],
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
) -> LLMResult:
    failures: list[str] = []
    for index, provider in enumerate(providers):
        try:
            # A requested model name belongs to the primary provider. Each
            # fallback uses its own configured default model.
            return provider.generate(messages, model=model if index == 0 else None)
        except Exception as exc:
            failures.append(f"{provider.name}: {exc.__class__.__name__}")
    raise RuntimeError(f"All LLM providers failed: {', '.join(failures)}")
