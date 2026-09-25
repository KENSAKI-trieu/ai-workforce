"""Body of /health/runtime."""

from typing import Literal

from pydantic import BaseModel


class RuntimeFeatureStatus(BaseModel):
    requested: bool
    available: bool
    effective: bool


class RuntimeFeatureResponse(BaseModel):
    active_runtime: Literal["legacy", "langgraph"]
    legacy_fallback: bool
    langgraph: RuntimeFeatureStatus
