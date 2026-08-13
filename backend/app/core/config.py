from functools import lru_cache
from typing import Optional
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- App ---
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_DEBUG: bool = True

    # --- Database ---
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ai_workforce_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str
    DATABASE_URL: Optional[str] = None

    # --- Security ---
    SECRET_KEY: str
    SEED_DEFAULT_PASSWORD: Optional[str] = None
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30 days

    # --- LLM ---
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GOOGLE_AI_API_KEY: Optional[str] = None

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    WORK_QUEUE_NAME: str = "ai-workforce:jobs"
    WORK_QUEUE_PROCESSING_NAME: str = "ai-workforce:jobs:processing"
    WORK_QUEUE_DEAD_LETTER_NAME: str = "ai-workforce:jobs:dead-letter"
    WORKER_HEARTBEAT_KEY: str = "ai-workforce:worker:heartbeat"

    # --- Email delivery ---
    EMAIL_DELIVERY_MODE: str = "outbox"
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    SMTP_USE_TLS: bool = True

    # --- Storage ---
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: Optional[str] = None
    MINIO_SECRET_KEY: Optional[str] = None
    KNOWLEDGE_STORAGE_PATH: str = "data/knowledge"
    LEGAL_DRAFT_STORAGE_PATH: str = "data/legal-drafts"
    CLOUDINARY_URL: Optional[str] = None

    # --- Internal AI service ---
    AI_SERVICE_URL: Optional[str] = None
    AI_SERVICE_INTERNAL_TOKEN: Optional[str] = None
    AI_SERVICE_TIMEOUT_SECONDS: float = 120.0
    LANGGRAPH_ENABLED: bool = False
    LANGGRAPH_LEGACY_FALLBACK: bool = True

    # --- Knowledge embeddings ---
    EMBEDDING_BACKEND: str = "deterministic"
    EMBEDDING_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    EMBEDDING_VERSION: str = "qwen3-embedding-v1"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_CACHE_FOLDER: Optional[str] = None
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    EMBEDDING_MAX_RETRIES: int = 3
    RAG_CHUNK_MIN_TOKENS: int = 100
    RAG_CHUNK_TARGET_TOKENS: int = 450
    RAG_CHUNK_MAX_TOKENS: int = 700
    RAG_CHUNK_OVERLAP_TOKENS: int = 80
    RAG_MIN_DENSE_SCORE: float = 0.50
    RAG_MIN_RELEVANCE_SCORE: float = 0.50

    # --- CORS ---
    FRONTEND_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def derive_database_url(self) -> "Settings":
        if not self.DATABASE_URL:
            user = quote_plus(self.POSTGRES_USER)
            password = quote_plus(self.POSTGRES_PASSWORD)
            database = quote_plus(self.POSTGRES_DB)
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{user}:{password}@"
                f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"
            )
        if not 0 <= self.RAG_CHUNK_OVERLAP_TOKENS < self.RAG_CHUNK_MAX_TOKENS:
            raise ValueError("RAG chunk overlap must be smaller than max tokens")
        if not (
            0 < self.RAG_CHUNK_MIN_TOKENS
            <= self.RAG_CHUNK_TARGET_TOKENS
            <= self.RAG_CHUNK_MAX_TOKENS
        ):
            raise ValueError("RAG chunk token limits must satisfy min <= target <= max")
        if not -1.0 <= self.RAG_MIN_DENSE_SCORE <= 1.0:
            raise ValueError("RAG minimum dense score must be between -1 and 1")
        if not 0.0 <= self.RAG_MIN_RELEVANCE_SCORE <= 1.0:
            raise ValueError("RAG minimum relevance score must be between 0 and 1")
        return self

    @property
    def sync_database_url(self) -> str:
        """Return a synchronous SQLAlchemy URL for Alembic."""
        assert self.DATABASE_URL is not None
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg2")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
