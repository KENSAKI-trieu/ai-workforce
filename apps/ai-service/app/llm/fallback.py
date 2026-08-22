import logging
import re

from app.llm.base import LLMProvider, LLMResult

logger = logging.getLogger(__name__)


def _safe_provider_error(exc: Exception) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ")[:1000]
    message = re.sub(r"AIza[0-9A-Za-z_-]+", "[REDACTED_API_KEY]", message)
    message = re.sub(
        r"(?i)(api[_-]?key|key)(\s*[=:]\s*)[^&\s,;]+",
        r"\1\2[REDACTED]",
        message,
    )
    return message


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
            logger.warning(
                "LLM provider '%s' failed with %s: %s",
                provider.name,
                exc.__class__.__name__,
                _safe_provider_error(exc),
            )
    raise RuntimeError(f"All LLM providers failed: {', '.join(failures)}")
