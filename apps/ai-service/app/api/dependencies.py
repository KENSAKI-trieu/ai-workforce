"""Request guards shared by the internal endpoints."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_internal_token(
    x_ai_service_key: str | None = Header(default=None),
) -> None:
    expected = settings.AI_SERVICE_INTERNAL_TOKEN
    if not expected:
        # Startup already refuses this outside tests. Answering 503 rather than letting
        # the request through keeps the missing-configuration case closed everywhere.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service credential is not configured",
        )
    if not x_ai_service_key or not hmac.compare_digest(x_ai_service_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid AI service credential",
        )


def tool_jwt(x_internal_tool_authorization: str | None) -> str:
    """The backend-issued JWT the graph presents when it calls the tool gateway."""
    prefix = "Bearer "
    if not x_internal_tool_authorization or not x_internal_tool_authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Internal tool authorization is required")
    return x_internal_tool_authorization[len(prefix):].strip()
