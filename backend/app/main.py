"""
AI Workforce — FastAPI Application Entry Point
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.router import api_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.APP_DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
# SentenceTransformer probes optional architecture-specific config files while
# resolving a local model. Missing probe files are expected and not actionable.
logging.getLogger("sentence_transformers.util.file_io").setLevel(logging.WARNING)
logger = logging.getLogger("ai_workforce")


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown events
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 AI Workforce backend starting up...")
    logger.info(f"   Environment : {settings.APP_ENV}")
    logger.info(f"   Database    : {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
    logger.info(f"   Debug mode  : {settings.APP_DEBUG}")
    try:
        from app.core.database import sync_engine
        from sqlalchemy import text

        with sync_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("✅ Database connection verified; schema is managed by Alembic.")
    except Exception as e:
        logger.error(f"⚠️ Database connectivity check failed: {e}")
        raise
    if settings.LANGGRAPH_ENABLED:
        # Turning this flag on routes every non-HR agent through LangGraph, which makes
        # the whole deterministic LEGAL branch unreachable. The gateway's LEGAL policy
        # grants only the three tools named below -- none of them reviews contract risk --
        # so contract review, the represented-party question and redline generation all
        # disappear from chat with no error anywhere. The tool names are hardcoded on
        # purpose: the backend talks to apps/ai-service over HTTP and must not import it.
        logger.warning(
            "⚠️ LANGGRAPH_ENABLED=true routes every non-HR agent through LangGraph. "
            "The LEGAL domain policy exposes only rag_search, generate_legal_document "
            "and submit_approval_request -- there is no contract risk-review tool -- so "
            "contract review, the represented-party question and redline generation are "
            "UNREACHABLE in chat until a review tool is added to DOMAIN_POLICIES['LEGAL']."
        )
    yield
    logger.info("🛑 AI Workforce backend shutting down...")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AI Workforce — Enterprise Multi-Agent Platform",
    description=(
        "Backend API for AI Workforce: a platform with autonomous AI Employees "
        "(HR, Legal, IT, Finance, Sales, Knowledge, CEO) that orchestrate real enterprise workflows."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({
        settings.FRONTEND_URL.rstrip("/"),
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(api_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Simple health check endpoint."""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "AI Workforce Backend",
            "version": "1.0.0",
            "environment": settings.APP_ENV,
        }
    )


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Dependency-aware readiness without exposing queue or database details."""
    from sqlalchemy import text

    from app.core.database import sync_engine
    from app.services.work_queue import queue_stats

    dependencies = {"database": False, "redis": False, "worker": False}
    try:
        with sync_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        dependencies["database"] = True
    except Exception:
        logger.exception("Readiness database check failed")
    try:
        queue = queue_stats()
        dependencies["redis"] = queue["available"]
        dependencies["worker"] = queue["worker_online"]
    except Exception:
        logger.exception("Readiness queue check failed")
    ready = all(dependencies.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "dependencies": dependencies},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_DEBUG,
    )
