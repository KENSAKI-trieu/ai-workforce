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


class AgentRouteRequest(BaseModel):
    requested_role: str | None = None
    message: str = Field(min_length=1)


class AgentRouteResponse(BaseModel):
    role: str
    agent_name: str
    capabilities: list[str]


class LLMGenerateRequest(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=100)
    provider: str | None = None
    model: str | None = None


class LLMGenerateResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: dict[str, int]


class RuntimeFeatureStatus(BaseModel):
    requested: bool
    available: bool
    effective: bool
    agent_roles: list[str] | None = None


class RuntimeFeatureResponse(BaseModel):
    active_runtime: Literal["legacy", "langchain", "langgraph"]
    legacy_fallback: bool
    langchain: RuntimeFeatureStatus
    langgraph: RuntimeFeatureStatus


class OrchestrationRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=50000)
    requested_agent: str | None = Field(default=None, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    denied_tools: list[str] = Field(default_factory=list, max_length=100)


class OrchestrationResumeRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=50)
    conversation_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    agent_role: str = Field(min_length=1, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    denied_tools: list[str] = Field(default_factory=list, max_length=100)
    resume: Any


class OrchestrationResponse(BaseModel):
    thread_id: str
    status: Literal["COMPLETED", "AWAITING_APPROVAL"]
    state: dict[str, Any]
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
