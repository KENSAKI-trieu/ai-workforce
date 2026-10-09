"""Replies and cards a governed tool keeps on the backend for the chat turn that called it.

See ``AgentToolOutput``: the AI service only ever carries a reference to these.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import AgentToolOutput, User

# Response keys an output may restore, the cards the chat renders.
CARD_KEYS = ("hr_card", "approval_card")

# A row nobody read -- the turn failed after the tool ran -- is cleared this long after.
UNREAD_OUTPUT_TTL = timedelta(hours=1)


def store_tool_output(
    db: Session,
    *,
    actor: User,
    conversation_id: uuid.UUID | None,
    tool_name: str,
    response: dict[str, Any],
) -> uuid.UUID:
    db.query(AgentToolOutput).filter(
        AgentToolOutput.tenant_id == actor.tenant_id,
        AgentToolOutput.user_id == actor.id,
        AgentToolOutput.created_at < datetime.now(timezone.utc) - UNREAD_OUTPUT_TTL,
    ).delete(synchronize_session=False)
    output = AgentToolOutput(
        tenant_id=actor.tenant_id,
        user_id=actor.id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        reply=str(response.get("reply") or ""),
        cards={key: response[key] for key in CARD_KEYS if response.get(key) is not None},
    )
    db.add(output)
    db.commit()
    return output.id


def take_tool_output(
    db: Session,
    *,
    user: User,
    conversation_id: str,
    output_id: Any,
) -> dict[str, Any] | None:
    """The stored reply and cards, deleted as they are read.

    The id came back through the graph, so it is trusted only as far as it names an output
    of this user, in this tenant and this conversation.
    """
    try:
        output_uuid = uuid.UUID(str(output_id))
        conversation_uuid = uuid.UUID(str(conversation_id))
    except ValueError:
        return None
    output = db.query(AgentToolOutput).filter(
        AgentToolOutput.id == output_uuid,
        AgentToolOutput.tenant_id == user.tenant_id,
        AgentToolOutput.user_id == user.id,
        AgentToolOutput.conversation_id == conversation_uuid,
    ).first()
    if output is None:
        return None
    restored = {"reply": output.reply, **(output.cards or {})}
    db.delete(output)
    db.commit()
    return restored
