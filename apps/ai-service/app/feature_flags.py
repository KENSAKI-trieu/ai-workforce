from dataclasses import dataclass

from app.config import Settings, settings


# Runtime availability is separate from rollout flags so deployments can expose
# readiness without activating a new execution path.
LANGCHAIN_RUNTIME_AVAILABLE = True
LANGGRAPH_RUNTIME_AVAILABLE = True


def _roles(raw_roles: str) -> frozenset[str]:
    return frozenset(
        item.strip().upper()
        for item in raw_roles.split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class RuntimeSelection:
    backend: str
    requested: bool
    available: bool
    fallback_reason: str | None


def select_langchain_runtime(
    role: str,
    *,
    config: Settings = settings,
    runtime_available: bool = LANGCHAIN_RUNTIME_AVAILABLE,
) -> RuntimeSelection:
    configured_roles = _roles(config.LANGCHAIN_AGENT_ROLES)
    normalized_role = role.strip().upper()
    role_selected = (
        not configured_roles
        or "*" in configured_roles
        or normalized_role in configured_roles
    )
    requested = config.LANGCHAIN_ENABLED and role_selected
    if requested and runtime_available:
        return RuntimeSelection("langchain", True, True, None)
    if requested:
        return RuntimeSelection(
            "legacy",
            True,
            False,
            "langchain_runtime_unavailable",
        )
    return RuntimeSelection("legacy", False, runtime_available, None)


def runtime_feature_snapshot(config: Settings = settings) -> dict[str, object]:
    roles = sorted(_roles(config.LANGCHAIN_AGENT_ROLES))
    chain_requested = config.LANGCHAIN_ENABLED
    graph_requested = config.LANGGRAPH_ENABLED
    langchain_effective = chain_requested and LANGCHAIN_RUNTIME_AVAILABLE
    langgraph_effective = graph_requested and LANGGRAPH_RUNTIME_AVAILABLE
    return {
        "active_runtime": (
            "langgraph" if langgraph_effective
            else "langchain" if langchain_effective
            else "legacy"
        ),
        "legacy_fallback": True,
        "langchain": {
            "requested": chain_requested,
            "available": LANGCHAIN_RUNTIME_AVAILABLE,
            "effective": langchain_effective,
            "agent_roles": roles or ["*"],
        },
        "langgraph": {
            "requested": graph_requested,
            "available": LANGGRAPH_RUNTIME_AVAILABLE,
            "effective": langgraph_effective,
        },
    }
