from typing import Literal, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.config import settings


LangChainProvider = Literal["openai", "gemini"]


def default_model_for(provider: LangChainProvider) -> str:
    if provider == "openai":
        return settings.OPENAI_CHAT_MODEL
    if provider == "gemini":
        return settings.GEMINI_CHAT_MODEL
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
    raise ValueError(f"Unsupported LangChain provider: {provider}")


def configured_chat_models(
    provider: str | None = None,
    *,
    model: str | None = None,
    max_retries: int | None = None,
) -> list[BaseChatModel]:
    """Build configured external models in provider-fallback order."""
    selected = (provider or "").lower()
    if selected not in {"", "openai", "gemini"}:
        return []
    order = [selected] if selected else []
    order.extend(item for item in ("openai", "gemini") if item not in order)
    result: list[BaseChatModel] = []
    for index, provider_name in enumerate(order):
        api_key = (
            settings.OPENAI_API_KEY
            if provider_name == "openai"
            else settings.GOOGLE_AI_API_KEY
        )
        if not api_key:
            continue
        result.append(create_chat_model(
            cast(LangChainProvider, provider_name),
            api_key=api_key,
            model=model if index == 0 else None,
            max_retries=max_retries,
        ))
    return result
