from functools import lru_cache
from typing import Literal, Optional
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    AI_SERVICE_HOST: str = "0.0.0.0"
    AI_SERVICE_PORT: int = 8100
    AI_SERVICE_INTERNAL_TOKEN: Optional[str] = None
    BACKEND_TOOL_GATEWAY_URL: str = "http://localhost:8000"
    MODEL_MEMORY_MODE: Literal["shared", "exclusive"] = "shared"

    EMBEDDING_BACKEND: str = "deterministic"
    EMBEDDING_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    EMBEDDING_MODEL_PATH: Optional[str] = None
    EMBEDDING_VERSION: str = "qwen3-embedding-v1"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DTYPE: str = "float32"
    EMBEDDING_CACHE_FOLDER: Optional[str] = None
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    EMBEDDING_PRELOAD: bool = False
    EMBEDDING_MAX_RETRIES: int = 3

    RAG_CHUNK_TARGET_TOKENS: int = 450
    RAG_CHUNK_MAX_TOKENS: int = 700
    RAG_CHUNK_OVERLAP_TOKENS: int = 80
    RAG_MIN_DENSE_SCORE: float = 0.50
    RAG_MIN_RELEVANCE_SCORE: float = 0.50

    RERANK_BACKEND: str = "bge"
    RERANK_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"
    RERANK_MODEL_PATH: Optional[str] = None
    RERANK_DEVICE: str = "cpu"
    RERANK_DTYPE: str = "float32"
    RERANK_CACHE_FOLDER: Optional[str] = None
    RERANK_LOCAL_FILES_ONLY: bool = False
    RERANK_SAFETENSORS_BACKEND: Literal["mmap", "pread"] = "mmap"
    RERANK_BATCH_SIZE: int = Field(default=4, ge=1, le=128)
    RERANK_MAX_LENGTH: int = Field(default=4096, ge=128, le=32768)
    RERANK_CANDIDATE_LIMIT: int = Field(default=30, ge=1, le=200)
    RERANK_MIN_MODEL_SCORE: float = Field(default=0.15, ge=0.0, le=1.0)
    RERANK_MODEL_WEIGHT: float = Field(default=0.90, ge=0.0, le=1.0)
    RERANK_FALLBACK_ENABLED: bool = True
    JINA_API_KEY: Optional[str] = None
    JINA_RERANK_URL: str = "https://api.jina.ai/v1/rerank"
    JINA_RERANK_MODEL: str = "jina-reranker-v3.5"
    JINA_RERANK_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, le=300)
    JINA_RERANK_SCORE_NORMALIZATION: Literal["auto", "none", "sigmoid"] = "auto"
    RERANK_INSTRUCTION: str = (
        "Truy xuất đoạn tài liệu nội bộ chính xác và đủ căn cứ để trả lời câu hỏi."
    )

    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_AI_API_KEY: Optional[str] = None
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    GEMINI_CHAT_MODEL: str = "gemini-2.0-flash"
    LLM_TIMEOUT_SECONDS: float = Field(default=90.0, gt=0)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    AGENT_MAX_MODEL_CALLS: int = Field(default=8, ge=1, le=100)
    AGENT_MAX_TOOL_CALLS: int = Field(default=12, ge=1, le=200)
    AGENT_MIDDLEWARE_MODEL_RETRIES: int = Field(default=2, ge=0, le=10)
    AGENT_COMPLEXITY_THRESHOLD: int = Field(default=4, ge=1, le=20)

    # Migration gates. LangChain is available from phase 2 and can still be
    # rolled out by role while the deterministic provider remains a fallback.
    LANGCHAIN_ENABLED: bool = False
    LANGGRAPH_ENABLED: bool = False
    LANGCHAIN_AGENT_ROLES: str = ""
    LANGGRAPH_CHECKPOINT_BACKEND: Literal["memory", "postgres"] = "memory"
    LANGGRAPH_CHECKPOINT_DATABASE_URL: Optional[str] = None
    LANGGRAPH_CHECKPOINT_AUTO_SETUP: bool = True
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ai_workforce_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: Optional[str] = None

    @property
    def langgraph_checkpoint_database_url(self) -> str | None:
        if self.LANGGRAPH_CHECKPOINT_DATABASE_URL:
            return self.LANGGRAPH_CHECKPOINT_DATABASE_URL.replace("+asyncpg", "")
        if self.LANGGRAPH_CHECKPOINT_BACKEND != "postgres" or self.POSTGRES_PASSWORD is None:
            return None
        return (
            f"postgresql://{quote_plus(self.POSTGRES_USER)}:{quote_plus(self.POSTGRES_PASSWORD)}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{quote_plus(self.POSTGRES_DB)}"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
