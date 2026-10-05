"""Plain text generation for the backend's routers and answer synthesis."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import require_internal_token
from app.core.llm.catalog import model_catalog
from app.schemas.llm import LLMGenerateRequest, LLMGenerateResponse
from app.services.generation import generate_chat

router = APIRouter()


@router.post("/v1/llm/generate", response_model=LLMGenerateResponse, dependencies=[Depends(require_internal_token)])
def generate_text(request: LLMGenerateRequest) -> LLMGenerateResponse:
    result = generate_chat(
        request.messages,
        provider=request.provider,
        model=request.model,
    )
    return LLMGenerateResponse(
        content=result.content,
        provider=result.provider,
        model=result.model,
        usage=result.usage,
    )


@router.get("/v1/llm/models", dependencies=[Depends(require_internal_token)])
def list_models(refresh: bool = Query(False)) -> dict:
    """Chat models the configured vendors serve, for the agent configuration picker."""
    return model_catalog.snapshot(refresh=refresh)
