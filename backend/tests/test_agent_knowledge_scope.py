"""An agent's knowledge scope must survive the deletion of a document it names.

A deleted document's selector used to stay on the agent: the configuration page could not
show it, the update API rejected it as unknown -- so even saving the agent unchanged
failed with 422 -- and retrieval filtered on a document that matched nothing.
"""

from __future__ import annotations

import pytest

from app.models.models import AIAgent, User
from app.services.agent_knowledge_scope import (
    orphaned_selectors,
    prune_orphaned_knowledge_selectors,
)
from app.services.rag_service import ingest_document

DOC_ID = "scope-test-contract-policy.md"
DOC_TEXT = "# Chính sách hợp đồng\n\n## Trình ký\nHợp đồng trên 500 triệu phải qua pháp chế."


def test_orphaned_selectors_are_those_naming_nothing():
    targets = ({"General Knowledge"}, {"a.md"}, {"c1"})

    assert orphaned_selectors(
        ["*", "none", "General Knowledge", "collection:Gone", "document:a.md",
         "document:b.md", "chunk:c1", "chunk:c2", "bogus:x"],
        targets,
    ) == ["collection:Gone", "document:b.md", "chunk:c2", "bogus:x"]


@pytest.fixture(scope="module")
def admin(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "admin@company.com").one()


@pytest.fixture(scope="module")
def legal(transactional_db_session, admin):
    return transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == admin.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()


def _ingest(db, tenant_id):
    ingest_document(db, tenant_id, DOC_ID, DOC_TEXT, collection_name="Legal", document_id=DOC_ID)


def _set_scope(db, agent, selectors):
    agent.knowledge_access = selectors
    db.flush()


def test_a_scope_left_empty_by_pruning_becomes_no_access_not_everything(
    transactional_db_session, admin, legal
):
    """[] means every document; an agent scoped to a deleted one must not be widened."""
    _set_scope(transactional_db_session, legal, ["document:never-existed.md"])

    changed = prune_orphaned_knowledge_selectors(transactional_db_session, admin.tenant_id)

    assert legal in changed
    assert legal.knowledge_access == ["none"]


def test_deleting_a_document_removes_it_from_agent_scopes(
    client, ceo_token_headers, transactional_db_session, admin, legal
):
    _ingest(transactional_db_session, admin.tenant_id)
    _set_scope(
        transactional_db_session, legal,
        [f"document:{DOC_ID}", "collection:General Knowledge"],
    )

    response = client.delete(f"/api/v1/documents/{DOC_ID}", headers=ceo_token_headers)

    assert response.status_code == 200, response.text
    assert "LEGAL" in response.json()["agents_rescoped"]
    transactional_db_session.refresh(legal)
    assert legal.knowledge_access == ["collection:General Knowledge"]


def test_an_agent_holding_a_dangling_selector_can_still_be_saved(
    client, ceo_token_headers, transactional_db_session, legal
):
    _set_scope(transactional_db_session, legal, ["document:deleted-long-ago.md"])

    options = client.get("/api/v1/agents/LEGAL/configuration-options", headers=ceo_token_headers)
    assert options.json()["orphaned_knowledge"] == ["document:deleted-long-ago.md"]

    unchanged = client.patch(
        "/api/v1/agents/LEGAL",
        json={"knowledge_access": ["document:deleted-long-ago.md"]},
        headers=ceo_token_headers,
    )
    assert unchanged.status_code == 200, unchanged.text

    # Only selectors the agent already held get this allowance.
    added = client.patch(
        "/api/v1/agents/LEGAL",
        json={"knowledge_access": ["document:deleted-long-ago.md", "document:made-up.md"]},
        headers=ceo_token_headers,
    )
    assert added.status_code == 422
    assert "made-up.md" in added.json()["detail"]
    _set_scope(transactional_db_session, legal, [])
