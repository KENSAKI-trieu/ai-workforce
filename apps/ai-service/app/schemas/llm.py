"""Request and response bodies of /v1/llm/generate."""

from pydantic import BaseModel, Field


class LLMGenerateRequest(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=100)
    provider: str | None = None
    model: str | None = None


class LLMGenerateResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: dict[str, int]


class LLMKeyVerifyRequest(BaseModel):
    provider: str = Field(pattern="^(openai|gemini)$")
    api_key: str = Field(min_length=8, max_length=512)


class LLMKeyVerifyResponse(BaseModel):
    ok: bool
    # The vendor's exception class when the key was refused; never its message, which
    # may echo the key.
    error: str | None = None
    models: int = 0
