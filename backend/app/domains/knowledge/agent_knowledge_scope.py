"""Keep AI Employees' knowledge scopes pointing at knowledge that still exists.

An agent's ``knowledge_access`` names collections, documents and chunks by identifier.
Deleting a document used to leave its selectors behind: the configuration page could not
show them (it draws only what exists), the update API rejected them as unknown -- so the
agent could no longer be saved at all -- and retrieval kept filtering on a document that
matched nothing, so the agent silently found nothing.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.models import AIAgent, DocumentChunk

# Spellings of "nothing" an older row may still hold. Neither names knowledge, so neither
# can be orphaned. "*" used to mean "every document" and no longer grants anything.
WILDCARD_SELECTORS = frozenset({"*", "none"})

# Roles that read a skill shelf (`ai_agents.skill_access`) next to their knowledge.
SKILL_KNOWLEDGE_ROLES = frozenset({"MARKETING"})


def agent_scope(agent: AIAgent | None) -> list[str]:
    """The selectors an agent may retrieve: exactly what is ticked on its configuration.

    Never None -- `hybrid_search_documents` reads None as "a person searching", which
    applies no agent filter at all. Every caller used to pass
    `agent.knowledge_access or None`, so an agent nobody had configured read the whole
    knowledge base. A missing agent row reads nothing either.
    """
    if agent is None:
        return []
    return [str(value) for value in (agent.knowledge_access or [])]


def skill_scope(agent: AIAgent | None) -> list[str]:
    """The selectors of the agent's skill shelf; empty for a role that has none."""
    if agent is None or str(agent.role_code or "").upper() not in SKILL_KNOWLEDGE_ROLES:
        return []
    return [str(value) for value in (agent.skill_access or []) if str(value) not in WILDCARD_SELECTORS]


def agent_scope_for_role(db: Session, tenant_id: uuid.UUID, role_code: str) -> list[str]:
    """`agent_scope` for code that holds a role rather than the agent row."""
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == tenant_id, AIAgent.role_code == role_code.upper()
    ).first()
    return agent_scope(agent)


def existing_knowledge_targets(
    db: Session, tenant_id: uuid.UUID
) -> tuple[set[str], set[str], set[str]]:
    """The collections, document ids and chunk ids a selector may currently name."""
    rows = db.query(
        DocumentChunk.id,
        DocumentChunk.document_id,
        DocumentChunk.document_name,
        DocumentChunk.collection_name,
    ).filter(DocumentChunk.tenant_id == tenant_id).all()
    collections = {row.collection_name for row in rows}
    documents = {row.document_id or row.document_name for row in rows}
    chunks = {str(row.id) for row in rows}
    return collections, documents, chunks


def orphaned_selectors(
    selectors: Iterable[str],
    targets: tuple[set[str], set[str], set[str]],
) -> list[str]:
    """The selectors that name knowledge which no longer exists."""
    collections, documents, chunks = targets
    orphaned = []
    for selector in selectors:
        if selector in WILDCARD_SELECTORS:
            continue
        prefix, separator, value = selector.partition(":")
        if not separator:
            exists = selector in collections
        elif prefix == "collection":
            exists = value in collections
        elif prefix == "document":
            exists = value in documents
        elif prefix == "chunk":
            exists = value in chunks
        else:
            exists = False
        if not exists:
            orphaned.append(selector)
    return orphaned


def prune_orphaned_knowledge_selectors(db: Session, tenant_id: uuid.UUID) -> list[AIAgent]:
    """Drop dangling selectors from every agent in the tenant; return the agents changed.

    An agent left with no selector at all reads nothing. It is stored as ``["none"]``
    rather than ``[]`` only so a row read by an older build -- where empty meant "every
    document" -- is not widened to the whole knowledge base. The caller commits.
    """
    targets = existing_knowledge_targets(db, tenant_id)
    changed = []
    for agent in db.query(AIAgent).filter(AIAgent.tenant_id == tenant_id).all():
        touched = False
        current = list(agent.knowledge_access or [])
        orphaned = set(orphaned_selectors(current, targets))
        if orphaned:
            remaining = [selector for selector in current if selector not in orphaned]
            agent.knowledge_access = remaining or ["none"]
            touched = True
        skills = list(agent.skill_access or [])
        orphaned_skills = set(orphaned_selectors(skills, targets))
        if orphaned_skills:
            # The skill shelf never meant "everything", so an emptied one is plain [].
            agent.skill_access = [selector for selector in skills if selector not in orphaned_skills]
            touched = True
        if touched:
            changed.append(agent)
    return changed
