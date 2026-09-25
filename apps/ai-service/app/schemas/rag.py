"""Request and response bodies of the /v1/rag, /v1/embeddings and /v1/token-count endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChunkRequest(BaseModel):
    content: str = Field(min_length=1)
    chunk_size: int | None = Field(default=None, ge=1)
    chunk_overlap: int | None = Field(default=None, ge=0)
    progress_stream_id: str | None = Field(default=None, min_length=16, max_length=128)
    progress_completed_before: int = Field(default=0, ge=0)
    progress_total_count: int | None = Field(default=None, ge=1)
    progress_chunks_before: int = Field(default=0, ge=0)


class ChunkResponse(BaseModel):
    chunks: list[dict[str, Any]]


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)
    input_type: Literal["document", "query"] = "document"
    completed_before: int = Field(default=0, ge=0)
    total_count: int | None = Field(default=None, ge=1)
    progress_stream_id: str | None = Field(default=None, min_length=16, max_length=128)


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    token_counts: list[int]
    model: str
    version: str
    dimension: int
    max_input_tokens: int
    batch_count: int
    embedded_count: int
    total_count: int
    remaining_count: int


class TokenCountRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)


class TokenCountResponse(BaseModel):
    token_counts: list[int]
    max_input_tokens: int


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    candidates: list[dict[str, Any]]
    top_k: int = Field(default=5, ge=1, le=100)


class RerankResponse(BaseModel):
    results: list[dict[str, Any]]
    backend: str
    model: str
    fallback_used: bool
    candidates_scored: int
    latency_ms: float
