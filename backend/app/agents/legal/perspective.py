"""Which side of a contract the user acts for, read from one of their messages."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import User
from app.plugins.resolver import resolve_prompt_overlay
from app.agents.legal.intent import _parse_represented_party
from app.agents.legal.llm_flow import extract_represented_party
from app.agents.usage import _llm_usage_recorder


def read_represented_party(
    db: Session,
    user: User,
    message: str,
    *,
    keyword_fallback: bool = True,
) -> str | None:
    """PARTY_A, PARTY_B or NEUTRAL as the user stated it, or None when they have not.

    The Legal reader decides; the keyword rules only stand in when no model answers.
    Without `keyword_fallback` the model's reading is the only one: when the message is
    the contract itself, the keywords would take "Bên A" in its text as the user's answer.
    """
    perspective = extract_represented_party(
        message,
        fallback_party=_parse_represented_party(message) if keyword_fallback else None,
        fallback_cancel=False,
        on_usage=_llm_usage_recorder(db, user, "LEGAL"),
        prompts=resolve_prompt_overlay(db, user.tenant_id, "LEGAL"),
    )
    return perspective.represented_party if perspective.decision == "ANSWER" else None
