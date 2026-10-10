"""Web search for the backend's agents (Gemini with Google Search grounding)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_internal_token
from app.schemas.web import WebSearchRequest, WebSearchResponse
from app.services.web_search import WebSearchUnavailable, search_web

router = APIRouter()


@router.post("/v1/web/search", response_model=WebSearchResponse, dependencies=[Depends(require_internal_token)])
def web_search(request: WebSearchRequest) -> WebSearchResponse:
    try:
        return WebSearchResponse(**search_web(request.query, max_results=request.max_results))
    except WebSearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
