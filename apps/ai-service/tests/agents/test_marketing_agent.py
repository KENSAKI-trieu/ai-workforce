"""The Marketing agent's graph: knowledge and web search, and a tool that starts a campaign."""

from __future__ import annotations

from app.agents.marketing.agent import POLICY
from app.agents.registry import AGENTS, resolve_agent


def test_marketing_has_its_own_graph_and_a_narrow_ceiling() -> None:
    assert "MARKETING" in AGENTS
    assert resolve_agent("marketing") == "MARKETING"
    assert POLICY.tools == ("rag_search", "web_search", "start_marketing_campaign")
    assert POLICY.citation_required
