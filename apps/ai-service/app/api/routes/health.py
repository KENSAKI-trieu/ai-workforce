"""Liveness, runtime-flag and accelerator status."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_token
from app.core.config import Settings, settings
from app.schemas.health import RuntimeFeatureResponse

router = APIRouter()

# Runtime availability is separate from the rollout flag so a deployment can report
# readiness without activating the graph. Which agents actually run through it is decided
# by the backend; the flag only reports intent and, in production, requires a durable
# checkpointer (see orchestration/persistence.py).
LANGGRAPH_RUNTIME_AVAILABLE = True


def runtime_feature_snapshot(config: Settings = settings) -> dict[str, object]:
    requested = config.LANGGRAPH_ENABLED
    effective = requested and LANGGRAPH_RUNTIME_AVAILABLE
    return {
        "active_runtime": "langgraph" if effective else "legacy",
        "legacy_fallback": True,
        "langgraph": {
            "requested": requested,
            "available": LANGGRAPH_RUNTIME_AVAILABLE,
            "effective": effective,
        },
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "ai-service", "version": "1.0.0"}


@router.get(
    "/health/runtime",
    response_model=RuntimeFeatureResponse,
    dependencies=[Depends(require_internal_token)],
)
def runtime_health() -> RuntimeFeatureResponse:
    return RuntimeFeatureResponse.model_validate(runtime_feature_snapshot())


@router.get("/health/accelerator", dependencies=[Depends(require_internal_token)])
def accelerator_health() -> dict[str, object]:
    try:
        import torch
    except ImportError:
        return {
            "cuda_available": False,
            "torch_version": None,
            "cuda_runtime": None,
            "device_count": 0,
            "device_name": None,
            "embedding_device": settings.EMBEDDING_DEVICE,
            "rerank_device": settings.RERANK_DEVICE,
            "reason": "PyTorch is not installed",
        }
    available = torch.cuda.is_available()
    return {
        "cuda_available": available,
        "torch_version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device_count": torch.cuda.device_count(),
        "device_name": torch.cuda.get_device_name(0) if available else None,
        "embedding_device": settings.EMBEDDING_DEVICE,
        "rerank_device": settings.RERANK_DEVICE,
    }
