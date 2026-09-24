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
    # An access token cannot be revoked, so its lifetime is the window an attacker keeps
    # after stealing one. This was 10080 (seven days), which made the refresh mechanism
    # decorative. Short-lived access tokens are the whole reason refresh tokens exist.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30 days

    # --- Refresh cookie ---
    # The refresh cookie used to be issued with secure=False hardcoded, so production
    # would have sent a 30-day credential over plaintext. These are settings now, and
    # production is not allowed to start with the unsafe combination.
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: Optional[str] = None

    # --- Login throttling ---
    # Nothing used to stand between an attacker and unlimited password guesses.
    # The per-email budget is the one that stops credential stuffing; the per-IP budget
    # is deliberately looser because a whole office can share one address.
    LOGIN_RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_MAX_PER_EMAIL: int = 10
    LOGIN_RATE_LIMIT_MAX_PER_IP: int = 50
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 900
    # When Redis cannot be reached the limit cannot be enforced. Closed by default: a
    # brief inability to log in beats an unmetered window for guessing passwords. Set
    # true to trade that for availability -- it is logged loudly either way.
    LOGIN_RATE_LIMIT_FAIL_OPEN: bool = False

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
    # Routers pick a label and have a keyword answer to fall back to, so a stalled
    # provider should cost the user seconds, not the full generation timeout.
    AI_SERVICE_ROUTER_TIMEOUT_SECONDS: float = 15.0
    LANGGRAPH_ENABLED: bool = False
    LANGGRAPH_LEGACY_FALLBACK: bool = True

    # --- Knowledge embeddings ---
    EMBEDDING_BACKEND: str = "deterministic"
    EMBEDDING_MODEL_NAME: str = "gemini-embedding-001"
    EMBEDDING_VERSION: str = "gemini-embedding-001-v1"
    EMBEDDING_DIMENSION: int = 768
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_CACHE_FOLDER: Optional[str] = None
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    EMBEDDING_MAX_RETRIES: int = 3
    EMBEDDING_ALLOW_DETERMINISTIC_FALLBACK: bool = True
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

        samesite = self.COOKIE_SAMESITE.lower()
        if samesite not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be one of: lax, strict, none")
        self.COOKIE_SAMESITE = samesite
        # A SameSite=None cookie without Secure is silently dropped by every current
        # browser, which would look like "refresh is broken" rather than a config error.
        if samesite == "none" and not self.COOKIE_SECURE:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        if self.APP_ENV.lower() == "production" and not self.COOKIE_SECURE:
            raise ValueError(
                "COOKIE_SECURE must be true in production; the refresh cookie is a "
                "30-day credential and must never travel over plaintext HTTP"
            )
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
