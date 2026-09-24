from typing import Literal, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.core.config import settings


LangChainProvider = Literal["openai", "gemini", "bedrock"]

# Vendor order when none is preferred ("auto"). Shared by the chat models built here for
# the graph and by LLMRouter for /v1/llm/generate, so both paths fall back the same way.
# Bedrock leads only when BEDROCK_ENABLED is set; otherwise it is skipped.
PROVIDER_ORDER: tuple[LangChainProvider, ...] = ("bedrock", "openai", "gemini")


def provider_keys(provider: str) -> tuple[str, ...]:
    """Every credential configured for a vendor; Bedrock has none but one empty slot."""
    if provider == "bedrock":
        return ("",) if settings.BEDROCK_ENABLED else ()
    if provider == "openai":
        return settings.openai_api_keys
    if provider == "gemini":
        return settings.google_api_keys
    return ()


def default_model_for(provider: LangChainProvider) -> str:
    if provider == "openai":
        return settings.OPENAI_CHAT_MODEL
    if provider == "gemini":
        return settings.GEMINI_CHAT_MODEL
    if provider == "bedrock":
        return settings.BEDROCK_CHAT_MODEL
    raise ValueError(f"Unsupported LangChain provider: {provider}")


def create_chat_model(
    provider: LangChainProvider,
    *,
    api_key: str,
    model: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> BaseChatModel:
    """Build a provider chat model with one shared reliability policy."""
    selected_model = model or default_model_for(provider)
    request_timeout = settings.LLM_TIMEOUT_SECONDS if timeout is None else timeout
    retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries

    if provider == "openai":
        return ChatOpenAI(
            model=selected_model,
            api_key=api_key,
            timeout=request_timeout,
            max_retries=retries,
        )
    if provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=selected_model,
            google_api_key=api_key,
            timeout=request_timeout,
            max_retries=retries,
        )
    if provider == "bedrock":
        # Imported here so a deployment that never enables Bedrock does not pay
        # the boto3 import cost, and so the other providers keep working if the
        # optional dependency is missing.
        from langchain_aws import ChatBedrockConverse

        from app.core.aws import bedrock_runtime_client

        # api_key is ignored: Bedrock authenticates the client, not the request.
        return ChatBedrockConverse(
            model=selected_model,
            client=bedrock_runtime_client(),
            max_tokens=settings.BEDROCK_MAX_TOKENS,
        )
    raise ValueError(f"Unsupported LangChain provider: {provider}")


def configured_chat_models(
    provider: str | None = None,
    *,
    model: str | None = None,
    max_retries: int | None = None,
) -> list[BaseChatModel]:
    """Build configured external models in fallback order: one model per credential.

    With no explicit provider the configured default leads, as it does for
    `LLMRouter`: "auto" keeps PROVIDER_ORDER and "local" builds no external model, so
    callers fall back to their deterministic path.

    Every key of a vendor gets its own model, next to each other in the list, so the
    graph's fallback middleware moves to the vendor's second key when the first one's
    quota runs out -- the graph used to be given only the first key. A requested model
    applies to every key of the leading vendor, which serves the same catalogue under
    each of them; other vendors use their own defaults.
    """
    configured_default = settings.LLM_DEFAULT_PROVIDER
    selected = (provider or ("" if configured_default == "auto" else configured_default)).lower()
    if selected not in {"", *PROVIDER_ORDER}:
        return []
    order = [selected] if selected else []
    order.extend(item for item in PROVIDER_ORDER if item not in order)
    result: list[BaseChatModel] = []
    for position, provider_name in enumerate(order):
        for api_key in provider_keys(provider_name):
            result.append(create_chat_model(
                cast(LangChainProvider, provider_name),
                api_key=api_key,
                model=model if position == 0 else None,
                max_retries=max_retries,
            ))
    return result
