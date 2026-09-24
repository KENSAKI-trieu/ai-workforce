from typing import Literal, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.core.config import settings


LangChainProvider = Literal["openai", "gemini", "bedrock"]


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
    """Build configured external models in provider-fallback order.

    With no explicit provider the configured default leads, as it does for
    `LLMRouter`: "auto" keeps the built-in order and "local" builds no external
    model, so callers fall back to their deterministic path.
    """
    configured_default = settings.LLM_DEFAULT_PROVIDER
    selected = (provider or ("" if configured_default == "auto" else configured_default)).lower()
    if selected not in {"", "openai", "gemini", "bedrock"}:
        return []
    order = [selected] if selected else []
    order.extend(item for item in ("openai", "gemini", "bedrock") if item not in order)
    result: list[BaseChatModel] = []
    for index, provider_name in enumerate(order):
        if provider_name == "bedrock":
            # Bedrock has no per-request key; BEDROCK_ENABLED is what gates it.
            api_key = "" if settings.BEDROCK_ENABLED else None
        elif provider_name == "openai":
            api_key = settings.OPENAI_API_KEY
        else:
            api_key = settings.GOOGLE_AI_API_KEY
        if api_key is None or (provider_name != "bedrock" and not api_key):
            continue
        result.append(create_chat_model(
            cast(LangChainProvider, provider_name),
            api_key=api_key,
            model=model if index == 0 else None,
            max_retries=max_retries,
        ))
    return result
