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
