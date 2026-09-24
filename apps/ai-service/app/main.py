"""FastAPI entrypoint: startup checks, lifecycle and routers. Endpoints live in app/api."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import require_internal_token
from app.api.routes import health, llm, orchestration, rag
from app.core.config import settings
from app.orchestration.persistence import orchestration_engines
from app.services.embedding.factory import get_embedding_provider

__all__ = ["app", "lifespan", "require_internal_token"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Every /v1 endpoint here answers with company data or spends money on a model
    # provider. A missing credential used to disable the check instead of failing the
    # request, so a deployment that forgot the variable served the whole surface to
    # anyone who could reach the port -- and nothing in the logs said so.
    if not settings.AI_SERVICE_INTERNAL_TOKEN and settings.APP_ENV != "test":
        raise RuntimeError(
            "AI_SERVICE_INTERNAL_TOKEN is not set. The AI service refuses to start "
            "without it because every /v1 endpoint would be reachable unauthenticated."
        )
    orchestration_engines.start()
    try:
        if settings.EMBEDDING_PRELOAD:
            # Load and warm up the local model on the main thread. On Windows,
            # initializing CUDA from FastAPI's worker thread can terminate the
            # process without producing a Python traceback.
            get_embedding_provider().embed(["embedding warmup"])
        yield
    finally:
        orchestration_engines.close()


app = FastAPI(
    title="AI Workforce AI Service",
    version="1.0.0",
    description="Durable agent orchestration, RAG, embedding and prompt runtime.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.FRONTEND_URL.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept"],
)

for router_module in (health, rag, llm, orchestration):
    app.include_router(router_module.router)
