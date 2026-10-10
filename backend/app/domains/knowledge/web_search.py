"""Web search for AI Employees, through the AI service (Gemini with Google Search).

A search leaves the company: Google sees the query. So the query is the agent's own
search phrase, or a brief already pseudonymised by the caller, and what comes back is
outside data -- quoted with its link, never followed as an instruction.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.llm_json import report_usage
from app.models.models import AIAgent, User

logger = logging.getLogger(__name__)

WEB_SEARCH_TOOL = "web_search"


class WebSearchUnavailable(RuntimeError):
    """The AI service could not search: no key, quota spent, or the vendor failed."""


def web_search_enabled(db: Session, agent: AIAgent | None) -> bool:
    """Whether the agent's configuration, after plugins, lets it search the web."""
    if agent is None:
        return False
    from app.agents.access import _attach_plugin_restriction, _can_use_tool
    from app.domains.platform.auth_service import upgrade_agent_grants
    from app.plugins.resolver import resolve_skill_restriction

    # A row seeded before the tool existed catches up first, as it does for chat.
    upgrade_agent_grants(agent)
    _attach_plugin_restriction(agent, resolve_skill_restriction(db, agent.tenant_id, agent.role_code))
    return _can_use_tool(agent, WEB_SEARCH_TOOL)


def search_web(
    db: Session, user: User, agent_role: str, query: str, *, max_results: int = 6
) -> dict[str, Any]:
    """{summary, queries, results: [{title, url, site, snippet}], grounded}, metered to the agent."""
    from app.agents.usage import _llm_usage_recorder
    from app.clients.ai_service_client import AIServiceError, get_ai_service_client

    client = get_ai_service_client()
    if not client.enabled:
        raise WebSearchUnavailable("AI service is not configured")
    try:
        result = client.web_search(query, max_results=max_results)
    except AIServiceError as exc:
        raise WebSearchUnavailable(str(exc)) from exc
    report_usage(_llm_usage_recorder(db, user, agent_role), result)
    return {
        "summary": str(result.get("summary") or ""),
        "queries": list(result.get("queries") or []),
        "results": [
            {
                "title": str(item.get("title") or item.get("site") or ""),
                "url": str(item.get("url") or ""),
                "site": str(item.get("site") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
            for item in result.get("results") or []
            if item.get("url")
        ],
        "grounded": bool(result.get("grounded")),
    }
