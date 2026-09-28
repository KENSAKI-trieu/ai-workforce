"""Knowledge tools."""

from __future__ import annotations

from typing import Any

from app.domains.knowledge.rag_service import hybrid_search_documents
from app.tools.registry import ToolContext
from app.tools.schemas import RAGSearchInput


def search_rag(context: ToolContext, request: RAGSearchInput) -> list[dict[str, Any]]:
    actor = context.actor
    agent = context.agent
    return hybrid_search_documents(
        db=context.db,
        tenant_id=actor.tenant_id,
        query_text=request.query,
        department="*" if actor.role in {"Owner", "Admin", "CEO"} else actor.department,
        top_k=request.top_k,
        collections=request.collections,
        # The AI Employee's configured knowledge scope. Omitting it here let a governed
        # agent read every document its user could reach, ignoring the scope an operator
        # had set for it -- the deterministic executors have always passed this.
        agent_access=(agent.knowledge_access or None) if agent else None,
        user_role=actor.role,
        user_department=actor.department,
    )
