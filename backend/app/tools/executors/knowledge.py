"""Knowledge tools."""

from __future__ import annotations

from typing import Any

from app.domains.knowledge.rag_service import (
    hybrid_search_documents,
    in_reading_order,
    user_search_scope,
)
from app.tools.registry import ToolContext
from app.tools.schemas import RAGSearchInput


def search_rag(context: ToolContext, request: RAGSearchInput) -> list[dict[str, Any]]:
    actor = context.actor
    agent = context.agent
    scope = user_search_scope(context.db, actor)
    if agent is not None and (agent.role_code or "").upper() == "HR":
        # As in the deterministic HR chat: HR policy questions search the HR shelf whatever
        # the asker's own department, or a salesperson could not read the leave policy.
        scope["department"] = "HR"
    # The result goes to a model, which reads it top to bottom.
    return in_reading_order(hybrid_search_documents(
        db=context.db,
        tenant_id=actor.tenant_id,
        query_text=request.query,
        top_k=request.top_k,
        collections=request.collections,
        # The AI Employee's configured knowledge scope. Omitting it here let a governed
        # agent read every document its user could reach, ignoring the scope an operator
        # had set for it -- the deterministic executors have always passed this.
        agent_access=(agent.knowledge_access or None) if agent else None,
        **scope,
    ))
