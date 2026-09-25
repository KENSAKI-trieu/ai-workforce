"""Knowledge search is `rag_search` everywhere; the two old names still work as aliases.

`hybrid_rag_search` (HR and Legal chats) and `hybrid_search_documents` (Knowledge chat)
named the same capability as the gateway's `rag_search`, as three grants that could
disagree. Migration w86d1f3a7b95 rewrites stored grants and packages; until a row or
package has been rewritten, the old spelling must still mean search.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.gateway_tools import effective_tool_grants
from app.core.tool_permissions import canonical_tool_names, grant_decision
from app.models.models import AIAgent
from app.plugins.manifest import parse_manifest
from app.plugins.resolver import SkillRestriction


def test_old_names_still_grant_and_deny_search() -> None:
    assert grant_decision(
        "rag_search", tools_access=["hybrid_rag_search"], allowed_actions=[], disallowed_actions=[]
    ) is None
    # A deny under either old name is a deny of search.
    assert grant_decision(
        "rag_search",
        tools_access=["rag_search"],
        allowed_actions=[],
        disallowed_actions=["hybrid_search_documents"],
    ) == "DENIED"
    # Asking under an old name is asking for search.
    assert grant_decision(
        "hybrid_rag_search", tools_access=["rag_search"], allowed_actions=[], disallowed_actions=[]
    ) is None
    assert grant_decision(
        "rag_search",
        tools_access=["rag_search"],
        allowed_actions=[],
        disallowed_actions=[],
        restriction=SkillRestriction(denied=frozenset({"rag_search"})),
    ) == "PLUGIN"


def test_the_graph_grant_is_spelled_once() -> None:
    assert effective_tool_grants(
        ["hybrid_rag_search", "rag_search", "audit_contract_risk"], [], []
    ) == ["audit_contract_risk", "rag_search"]
    assert canonical_tool_names(["hybrid_search_documents", "rag_search"]) == ["rag_search"]


def test_a_package_written_with_an_old_name_installs_as_search() -> None:
    manifest = parse_manifest(
        {
            "name": "old-spelling",
            "version": "1.0.0",
            "display_name": "Old spelling",
            "target_role": "HR",
            "skills": {"disallowed_actions": ["hybrid_rag_search"]},
        },
        known_tools=frozenset({"rag_search", "export_hr_directory"}),
        known_roles=frozenset({"HR"}),
    )
    assert manifest.disallowed_actions == ("rag_search",)


def test_saving_an_agent_with_an_old_name_stores_the_new_one(
    client, ceo_token_headers, transactional_db_session
) -> None:
    agent = transactional_db_session.query(AIAgent).filter(AIAgent.role_code == "KNOWLEDGE").first()
    original = (agent.tools_access, agent.allowed_actions, agent.disallowed_actions)
    try:
        response = client.patch(
            "/api/v1/agents/KNOWLEDGE",
            json={"tools_access": ["hybrid_search_documents"], "allowed_actions": [], "disallowed_actions": []},
            headers=ceo_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["tools_access"] == ["rag_search"]
    finally:
        agent.tools_access, agent.allowed_actions, agent.disallowed_actions = original
        transactional_db_session.commit()


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "w86d1f3a7b95_unify_search_tool_names.py"
    spec = importlib.util.spec_from_file_location("unify_search_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (["hybrid_rag_search", "request_leave"], ["rag_search", "request_leave"]),
        (["rag_search", "hybrid_search_documents"], ["rag_search"]),
        (["audit_contract_risk"], ["audit_contract_risk"]),
    ],
)
def test_the_migration_rewrites_each_grant_list_once(stored, expected) -> None:
    assert _migration()._renamed(stored) == expected
