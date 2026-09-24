"""Dispatch of one AI Employee chat turn.

Loads the agent, applies the tenant's plugin narrowing, answers agents that are under
development, sends non-HR agents through LangGraph when it is on, and otherwise hands the
turn to the role's own flow in app/agents/<role>/.
"""

from __future__ import annotations

import logging
from typing import Dict, Any

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.models import AIAgent, User
from app.core.agent_status import UNDER_DEVELOPMENT_REPLY, is_under_development
from app.core.agent_engines import uses_langgraph
from app.core.config import settings
from app.agents.langgraph.engine import LangGraphEngine
from app.clients.ai_service_client import AIServiceError
from app.plugins.resolver import resolve_skill_restriction
from app.agents.hr.llm_flow import UsageReporter
from app.agents.access import _attach_plugin_restriction
from app.agents.hr.flow import run_hr_turn
from app.agents.knowledge.flow import run_knowledge_turn
from app.agents.legal.flow import run_legal_turn

logger = logging.getLogger(__name__)


def execute_agent_chat(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
    *,
    allow_graph: bool = True,
) -> Dict[str, Any]:
    """Run the HR LLM-first gate, then dispatch to retrieval or governed tools."""
    # The HR gate calls back into the core below, so it is imported when used.
    from app.agents.hr.stream import stream_hr_chat_events

    if role_code.upper() != "HR":
        return _execute_agent_chat_core(
            db, user, role_code, message, thread_id, allow_graph=allow_graph
        )

    response: Dict[str, Any] | None = None
    for event in stream_hr_chat_events(db, user, role_code, message, thread_id):
        if event["event"] == "complete":
            response = event["response"]
    if response is None:
        raise RuntimeError("HR chat flow ended without a response")
    return response




def _execute_agent_chat_core(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
    *,
    hr_intent_override: str | None = None,
    leave_draft: dict[str, Any] | None = None,
    leave_cancel_request: bool = False,
    on_llm_usage: UsageReporter | None = None,
    allow_graph: bool = True,
) -> Dict[str, Any]:
    """
    Main dispatch entry point for processing agent queries.
    Returns structured response containing answer text, citations, tool calls, and specialized card payloads.
    """
    role_code_upper = role_code.upper()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id,
        AIAgent.role_code == role_code_upper
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{role_code_upper}' not found")
    if not agent.is_active:
        raise HTTPException(status_code=409, detail=f"Agent '{role_code_upper}' is inactive")

    # Resolved once per turn, immediately after the row is loaded, so every later
    # `_require_tool` and `_can_use_tool` call in this request sees the same narrowing.
    _attach_plugin_restriction(
        agent, resolve_skill_restriction(db, user.tenant_id, role_code_upper)
    )

    agent_name = agent.name if agent else f"{role_code_upper} Agent"
    agent_emoji = agent.avatar_emoji if agent else "🤖"

    response_data: Dict[str, Any] = {
        "agent_name": agent_name,
        "agent_role": role_code_upper,
        "avatar_emoji": agent_emoji,
        "reply": "",
        "citations": [],
        "tools_executed": [],
        "approval_card": None,
        "hr_card": None,
        "jira_card": None,
        "legal_risk_card": None,
        "invoice_card": None,
        "quote_card": None,
        "dag_plan_card": None,
    }

    # Checked before either engine: an unfinished agent answers the same way whether or
    # not LangGraph is on, and never reaches its placeholder logic.
    if is_under_development(role_code_upper):
        response_data["reply"] = UNDER_DEVELOPMENT_REPLY
        return response_data

    # The engine is chosen per role (AGENT_ENGINES); HR keeps its own LLM-first gate.
    if allow_graph and uses_langgraph(role_code_upper):
        try:
            return LangGraphEngine().execute(
                db=db,
                user=user,
                agent=agent,
                message=message,
                conversation_id=thread_id,
            )
        except AIServiceError as exc:
            if (
                not settings.LANGGRAPH_LEGACY_FALLBACK
                or (exc.status_code is not None and exc.status_code < 500)
            ):
                raise
            logger.exception("LangGraph runtime failed; using the legacy deterministic executor")

    # -----------------------------------------------------------------------
    # 1. HR Agent Processing
    # -----------------------------------------------------------------------
    if role_code_upper == "HR":
        return run_hr_turn(
            db=db,
            user=user,
            agent=agent,
            role_code_upper=role_code_upper,
            message=message,
            thread_id=thread_id,
            response_data=response_data,
            hr_intent_override=hr_intent_override,
            leave_draft=leave_draft,
            leave_cancel_request=leave_cancel_request,
            on_llm_usage=on_llm_usage,
        )

    # -----------------------------------------------------------------------
    # 2. KNOWLEDGE Agent Processing (Hybrid RAG)
    # -----------------------------------------------------------------------
    elif role_code_upper == "KNOWLEDGE":
        return run_knowledge_turn(
            db=db,
            user=user,
            agent=agent,
            message=message,
            response_data=response_data,
        )

    # -----------------------------------------------------------------------
    # 3. LEGAL Agent Processing (Contract Risk Audit & Redline)
    # -----------------------------------------------------------------------
    elif role_code_upper == "LEGAL":
        return run_legal_turn(
            db=db,
            user=user,
            agent=agent,
            role_code_upper=role_code_upper,
            message=message,
            thread_id=thread_id,
            response_data=response_data,
        )

    else:
        response_data["reply"] = f"Agent {role_code_upper} đã tiếp nhận chỉ thị: {message}"
        return response_data
