"""Which engine runs each AI Employee's turns: the deterministic flow or LangGraph.

Chosen per role rather than all at once, so one agent can move to LangGraph once its
evaluation passes while the others stay where they are:

    AGENT_ENGINES=KNOWLEDGE=langgraph,LEGAL=langgraph

A role not named there follows LANGGRAPH_ENABLED, the switch that used to move every
non-HR agent together. Two cases never reach the graph whatever is configured:

- agents under development answer with their fixed reply before any engine runs;
- HR, until its capabilities exist as gateway tools with server-side section checks.
  Its graph today would only have knowledge search.
"""

from __future__ import annotations

import logging

from app.core.agent_status import is_under_development
from app.core.config import settings

logger = logging.getLogger(__name__)

DETERMINISTIC = "deterministic"
LANGGRAPH = "langgraph"
ENGINES = frozenset({DETERMINISTIC, LANGGRAPH})
# Roles whose tools are not in the gateway yet, so the graph cannot serve them.
NOT_READY_FOR_LANGGRAPH = frozenset({"HR"})


def parse_agent_engines(raw: str | None) -> dict[str, str]:
    """`ROLE=engine` pairs separated by commas; malformed entries are skipped and logged."""
    engines: dict[str, str] = {}
    for item in str(raw or "").split(","):
        if not item.strip():
            continue
        role, separator, engine = item.partition("=")
        role, engine = role.strip().upper(), engine.strip().lower()
        if not separator or not role or engine not in ENGINES:
            logger.warning("Ignoring AGENT_ENGINES entry %r: expected ROLE=deterministic|langgraph", item)
            continue
        engines[role] = engine
    return engines


def engine_for(role_code: str) -> str:
    role = str(role_code or "").strip().upper()
    if is_under_development(role):
        return DETERMINISTIC
    configured = parse_agent_engines(settings.AGENT_ENGINES).get(role)
    wanted = configured or (LANGGRAPH if settings.LANGGRAPH_ENABLED and role != "HR" else DETERMINISTIC)
    if wanted == LANGGRAPH and role in NOT_READY_FOR_LANGGRAPH:
        if configured:
            logger.warning("AGENT_ENGINES asks for LangGraph for %s, whose tools are not ready; using the deterministic flow", role)
        return DETERMINISTIC
    return wanted


def uses_langgraph(role_code: str) -> bool:
    return engine_for(role_code) == LANGGRAPH
