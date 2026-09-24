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

WILDCARD_SELECTORS = frozenset({"*", "none"})


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

    An agent left with no selector at all is set to ``["none"]``, never to ``[]``: an
    empty scope means "every document", and an agent restricted to a document that was
    deleted must lose that access, not be widened to the whole knowledge base.
    The caller commits.
    """
    targets = existing_knowledge_targets(db, tenant_id)
    changed = []
    for agent in db.query(AIAgent).filter(AIAgent.tenant_id == tenant_id).all():
        current = list(agent.knowledge_access or [])
        orphaned = set(orphaned_selectors(current, targets))
        if not orphaned:
            continue
        remaining = [selector for selector in current if selector not in orphaned]
        agent.knowledge_access = remaining or ["none"]
        changed.append(agent)
    return changed
