"""One Knowledge turn: answer from the shared knowledge base."""

from __future__ import annotations

import re
from typing import Dict, Any

from sqlalchemy.orm import Session
from app.models.models import AIAgent, User
from app.domains.knowledge.rag_service import hybrid_search_documents
from app.domains.platform.audit_service import log_audit_action
from app.agents.access import _require_tool


def run_knowledge_turn(
    *,
    db: Session,
    user: User,
    agent: AIAgent,
    message: str,
    response_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Answer a Knowledge turn from the shared knowledge base."""
    _require_tool(agent, "rag_search")
    search_results = hybrid_search_documents(
        db,
        user.tenant_id,
        message,
        department="*" if user.role in {"Owner", "Admin", "CEO"} else user.department,
        collections=None,
        agent_access=agent.knowledge_access if agent.knowledge_access else None,
        user_role=user.role,
        user_department=user.department,
    )

    # Out-of-domain query check: ensure at least some word overlap with knowledge base
    msg_words = set(re.findall(r'\w+', message.lower()))
    filtered_results = []
    for c in search_results:
        c_words = set(re.findall(r'\w+', c["content"].lower()))
        if len(msg_words.intersection(c_words)) > 0:
            filtered_results.append(c)

    search_results = filtered_results

    response_data["tools_executed"].append({
        "tool_name": "rag_search",
        "input": {"query": message, "department": user.department},
        "result_count": len(search_results),
    })
    log_audit_action(
        db,
        user.tenant_id,
        "KNOWLEDGE",
        "rag_search",
        {"query": message},
        {
            "count": len(search_results),
            "chunks": [
                {
                    "chunk_id": item["id"],
                    "document_id": item["document_id"],
                    "version": item["version"],
                    "page": item["page"],
                }
                for item in search_results
            ],
        },
    )

    if search_results:
        response_data["citations"] = search_results
        best_chunk = search_results[0]
        citations_str = " ".join([c["citation_tag"] for c in search_results[:2]])

        response_data["reply"] = (
            f"Theo tài liệu tri thức doanh nghiệp:\n\n"
            f"{best_chunk['content']}\n\n"
            f"**Nguồn trích dẫn:** {citations_str}"
        )
    else:
        response_data["reply"] = (
            f"Hiện chưa tìm thấy tài liệu phù hợp trong Kho tri thức cho câu hỏi: *'{message}'*.\n"
            f"Bạn có thể tải thêm tài liệu quy định/SOP vào trang **Knowledge Base** để tôi truy xuất!"
        )
    return response_data
