from __future__ import annotations

from pydantic import BaseModel, Field


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=6000)
    max_results: int = Field(default=6, ge=1, le=10)


class WebSearchResult(BaseModel):
    title: str
    url: str
    site: str
    snippet: str


class WebSearchResponse(BaseModel):
    summary: str
    queries: list[str]
    results: list[WebSearchResult]
    grounded: bool
    provider: str
    model: str
    usage: dict[str, int]
