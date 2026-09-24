"""Persistent chat with AI Employees, citations, feedback and task conversion."""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.agent_status import is_under_development
from app.core.config import settings
from app.core.security import get_current_active_user
from app.models.models import AIAgent, ChatConversation, ChatMessage, Task, User
from app.services.agents.agent_executor import execute_agent_chat, stream_hr_chat_events
from app.services.agents.langgraph_engine import LangGraphEngine

router = APIRouter(prefix="/agent", tags=["Agent Chat"])
logger = logging.getLogger(__name__)


class AgentChatRequest(BaseModel):
    agent_role: str = Field(min_length=2, max_length=50)
    message: str = Field(min_length=1, max_length=50000)
    conversation_id: Optional[uuid.UUID] = None
    thread_id: Optional[str] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class AgentChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    agent_name: str
    agent_role: str
    avatar_emoji: str
    reply: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tools_executed: list[dict[str, Any]] = Field(default_factory=list)
    approval_card: Optional[dict[str, Any]] = None
    hr_card: Optional[dict[str, Any]] = None
    jira_card: Optional[dict[str, Any]] = None
    legal_risk_card: Optional[dict[str, Any]] = None
    invoice_card: Optional[dict[str, Any]] = None
    quote_card: Optional[dict[str, Any]] = None
    dag_plan_card: Optional[dict[str, Any]] = None


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: Optional[str] = Field(None, max_length=2000)


class CreateTaskFromChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    priority: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|URGENT)$")


def _get_conversation(
    db: Session,
    current_user: User,
    conversation_id: uuid.UUID,
    *,
    allow_shared: bool = True,
) -> ChatConversation:
    query = db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.tenant_id == current_user.tenant_id,
    )
    if allow_shared:
        query = query.filter(or_(
            ChatConversation.user_id == current_user.id,
            ChatConversation.is_shared.is_(True),
        ))
    else:
        query = query.filter(ChatConversation.user_id == current_user.id)
    conversation = query.first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "sender": message.sender,
        "content": message.content,
        "citations": message.citations or [],
        "tools_executed": message.tools_executed or [],
        "attachments": message.attachments or [],
        "feedback_rating": message.feedback_rating,
        "feedback_comment": message.feedback_comment,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _result_attachments(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist interactive response cards so chat history can render them again."""
    card_keys = (
        "approval_card",
        "hr_card",
        "jira_card",
        "legal_risk_card",
        "invoice_card",
        "quote_card",
        "dag_plan_card",
    )
    return [
        {"type": key.upper(), "payload": result[key]}
        for key in card_keys
        if result.get(key) is not None
    ]


def _encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _persist_assistant_message(
    db: Session,
    conversation: ChatConversation,
    result: dict[str, Any],
) -> ChatMessage:
    assistant_message = ChatMessage(
        conversation_id=conversation.id,
        sender="ASSISTANT",
        content=result["reply"],
        citations=result.get("citations", []),
        tools_executed=result.get("tools_executed", []),
        attachments=_result_attachments(result),
    )
    db.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


@router.post("/chat", response_model=AgentChatResponse, summary="Chat with an AI Employee")
def chat_with_agent(
    req: AgentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AgentChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    role_code = req.agent_role.upper()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == current_user.tenant_id,
        AIAgent.role_code == role_code,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{role_code}' not found")

    if req.conversation_id:
        conversation = _get_conversation(
            db, current_user, req.conversation_id, allow_shared=False
        )
        if conversation.ai_agent_id != agent.id:
            raise HTTPException(status_code=409, detail="Conversation belongs to another AI Employee")
    else:
        conversation_id = uuid.uuid4()
        conversation = ChatConversation(
            id=conversation_id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            ai_agent_id=agent.id,
            title=req.message.strip()[:120],
            thread_id=str(conversation_id),
        )
        db.add(conversation)
        db.flush()
    if conversation.thread_id != str(conversation.id):
        conversation.thread_id = str(conversation.id)

    db.add(ChatMessage(
        conversation_id=conversation.id,
        sender="USER",
        content=req.message.strip(),
        attachments=req.attachments,
    ))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()

    result = execute_agent_chat(
        db=db,
        user=current_user,
        role_code=role_code,
        message=req.message,
        thread_id=str(conversation.id),
    )
    assistant_message = _persist_assistant_message(db, conversation, result)
    return AgentChatResponse(
        conversation_id=str(conversation.id),
        message_id=str(assistant_message.id),
        **result,
    )


@router.post("/chat/stream", summary="Stream chat tokens and sanitized execution status over SSE")
def stream_chat_with_agent(
    req: AgentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    role_code = req.agent_role.upper()
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == current_user.tenant_id,
        AIAgent.role_code == role_code,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{role_code}' not found")
    if not agent.is_active:
        raise HTTPException(status_code=409, detail=f"Agent '{role_code}' is inactive")

    if req.conversation_id:
        conversation = _get_conversation(
            db, current_user, req.conversation_id, allow_shared=False
        )
        if conversation.ai_agent_id != agent.id:
            raise HTTPException(status_code=409, detail="Conversation belongs to another AI Employee")
    else:
        conversation_id = uuid.uuid4()
        conversation = ChatConversation(
            id=conversation_id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            ai_agent_id=agent.id,
            title=req.message.strip()[:120],
            thread_id=str(conversation_id),
        )
        db.add(conversation)
        db.flush()
    if conversation.thread_id != str(conversation.id):
        conversation.thread_id = str(conversation.id)

    db.add(ChatMessage(
        conversation_id=conversation.id,
        sender="USER",
        content=req.message.strip(),
        attachments=req.attachments,
    ))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()

    def event_stream():
        try:
            result: dict[str, Any] | None = None
            # HR uses its governed LLM-first flow, including contextual leave-slot extraction.
            # An unfinished agent takes the executor path, which answers it with the fixed
            # under-development reply before any engine runs.
            if settings.LANGGRAPH_ENABLED and role_code != "HR" and not is_under_development(role_code):
                for item in LangGraphEngine().execute_stream(
                    db=db,
                    user=current_user,
                    agent=agent,
                    message=req.message,
                    conversation_id=str(conversation.id),
                ):
                    event = str(item.get("event") or "message")
                    if event == "complete":
                        result = dict(item["response"])
                    else:
                        yield _encode_sse(
                            event,
                            {key: value for key, value in item.items() if key != "event"},
                        )
            elif role_code == "HR":
                # The HR flow reports a phase before each blocking step — intent routing,
                # dispatch, answer synthesis — so the client is not left on ANALYZING for
                # the whole round trip.
                for item in stream_hr_chat_events(
                    db,
                    current_user,
                    role_code,
                    req.message,
                    str(conversation.id),
                ):
                    if item["event"] == "complete":
                        result = dict(item["response"])
                    elif item["phase"] != "COMPLETED":
                        yield _encode_sse("status", {"phase": item["phase"]})
                if result is not None and result.get("approval_card"):
                    yield _encode_sse("status", {"phase": "WAITING_APPROVAL"})
                elif result is not None:
                    for token in re.findall(r"\S+\s*|\s+", str(result.get("reply") or "")):
                        yield _encode_sse("token", {"delta": token})
                yield _encode_sse("status", {"phase": "COMPLETED"})
            else:
                yield _encode_sse("status", {"phase": "ANALYZING"})
                result = execute_agent_chat(
                    db=db,
                    user=current_user,
                    role_code=role_code,
                    message=req.message,
                    thread_id=str(conversation.id),
                )
                if result.get("tools_executed"):
                    yield _encode_sse("status", {"phase": "TOOL_CALLING"})
                if result.get("approval_card"):
                    yield _encode_sse("status", {"phase": "WAITING_APPROVAL"})
                else:
                    for token in re.findall(r"\S+\s*|\s+", str(result.get("reply") or "")):
                        yield _encode_sse("token", {"delta": token})
                yield _encode_sse("status", {"phase": "COMPLETED"})

            if result is None:
                raise RuntimeError("Chat stream ended without a response")
            assistant_message = _persist_assistant_message(db, conversation, result)
            yield _encode_sse("complete", {
                "conversation_id": str(conversation.id),
                "message_id": str(assistant_message.id),
                **result,
            })
        except HTTPException as exc:
            # A permission or state refusal is an answer, not a crash. The non-streaming
            # endpoint returns its detail, so the stream must not flatten it into a
            # generic failure.
            db.rollback()
            yield _encode_sse("error", {
                "message": str(exc.detail),
                "status_code": exc.status_code,
            })
        except Exception:
            db.rollback()
            logger.exception("Agent chat stream failed")
            yield _encode_sse("error", {
                "message": "Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/conversations", summary="List the current user's chat history")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversations = db.query(ChatConversation).filter(
        ChatConversation.tenant_id == current_user.tenant_id,
        or_(
            ChatConversation.user_id == current_user.id,
            ChatConversation.is_shared.is_(True),
        ),
    ).order_by(ChatConversation.updated_at.desc()).all()
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "agent_role": item.ai_agent.role_code,
            "agent_name": item.ai_agent.name,
            "owner_id": str(item.user_id),
            "is_shared": item.is_shared,
            "message_count": len(item.messages),
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        for item in conversations
    ]


@router.get("/conversations/{conversation_id}", summary="Get conversation messages")
def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(db, current_user, conversation_id)
    messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id
    ).order_by(ChatMessage.created_at.asc()).all()
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "agent_role": conversation.ai_agent.role_code,
        "is_shared": conversation.is_shared,
        "messages": [_serialize_message(message) for message in messages],
    }


@router.patch("/conversations/{conversation_id}/share", summary="Share/unshare within workspace")
def toggle_share_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(
        db, current_user, conversation_id, allow_shared=False
    )
    conversation.is_shared = not conversation.is_shared
    db.commit()
    return {"id": str(conversation.id), "is_shared": conversation.is_shared}


@router.post("/messages/{message_id}/feedback", summary="Rate an AI response")
def submit_feedback(
    message_id: uuid.UUID,
    req: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    message = db.query(ChatMessage).join(ChatConversation).filter(
        ChatMessage.id == message_id,
        ChatMessage.sender == "ASSISTANT",
        ChatConversation.tenant_id == current_user.tenant_id,
        ChatConversation.user_id == current_user.id,
    ).first()
    if not message:
        raise HTTPException(status_code=404, detail="AI message not found")
    if req.rating == 0:
        raise HTTPException(status_code=422, detail="rating must be -1 or 1")
    message.feedback_rating = req.rating
    message.feedback_comment = req.comment
    db.commit()
    return {"message": "Feedback saved"}


@router.post("/conversations/{conversation_id}/regenerate", response_model=AgentChatResponse)
def regenerate_last_response(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(
        db, current_user, conversation_id, allow_shared=False
    )
    last_user_message = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender == "USER",
    ).order_by(ChatMessage.created_at.desc()).first()
    if not last_user_message:
        raise HTTPException(status_code=409, detail="Conversation has no user message")
    result = execute_agent_chat(
        db,
        current_user,
        conversation.ai_agent.role_code,
        last_user_message.content,
        conversation.thread_id,
    )
    assistant_message = ChatMessage(
        conversation_id=conversation.id,
        sender="ASSISTANT",
        content=result["reply"],
        citations=result.get("citations", []),
        tools_executed=result.get("tools_executed", []),
        attachments=_result_attachments(result),
    )
    db.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_message)
    return AgentChatResponse(
        conversation_id=str(conversation.id),
        message_id=str(assistant_message.id),
        **result,
    )


@router.post("/conversations/{conversation_id}/task", status_code=201)
def create_task_from_conversation(
    conversation_id: uuid.UUID,
    req: CreateTaskFromChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(
        db, current_user, conversation_id, allow_shared=False
    )
    last_response = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender == "ASSISTANT",
    ).order_by(ChatMessage.created_at.desc()).first()
    task = Task(
        tenant_id=current_user.tenant_id,
        title=req.title,
        description=f"Created from conversation: {conversation.title}",
        creator_id=current_user.id,
        assignee_id=current_user.id,
        ai_agent_id=conversation.ai_agent_id,
        priority=req.priority,
        status="PENDING",
        output_result=(
            {"chat_message_id": str(last_response.id), "content": last_response.content}
            if last_response else None
        ),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"message": "Task created from conversation", "task_id": str(task.id)}


@router.get(
    "/conversations/{conversation_id}/export",
    response_class=PlainTextResponse,
    summary="Export a conversation as Markdown",
)
def export_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(db, current_user, conversation_id)
    lines = [f"# {conversation.title}", ""]
    for message in sorted(conversation.messages, key=lambda item: item.created_at):
        lines.extend([
            f"## {'Employee' if message.sender == 'USER' else conversation.ai_agent.name}",
            "",
            message.content,
            "",
        ])
        if message.citations:
            lines.append("Sources: " + ", ".join(
                citation.get("citation_tag", citation.get("document_name", "source"))
                for citation in message.citations
            ))
            lines.append("")
    return "\n".join(lines)


@router.delete("/conversations/{conversation_id}", summary="Delete own conversation")
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conversation = _get_conversation(
        db, current_user, conversation_id, allow_shared=False
    )
    db.delete(conversation)
    db.commit()
    return {"message": "Conversation deleted"}
