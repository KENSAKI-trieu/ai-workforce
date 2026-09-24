from app.config import Settings, settings


# Runtime availability is separate from the rollout flag so a deployment can report
# readiness without activating the graph. Which agents actually run through it is decided
# by the backend; this flag only reports intent and, in production, requires a durable
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
