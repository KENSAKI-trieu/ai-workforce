"""The chat model an AI Employee runs on, carried to every LLM call of its turn.

An administrator picks the model per agent (`ai_agents.model_name`; NULL means the AI
service's default). The flows reach the model through a dozen `generate_text` calls in
as many modules, none of which knows which agent it works for, so the choice travels in a
context variable that `AIServiceClient.generate_text` reads when its caller names no model.

Two ways to bind it, depending on how the work runs:

- `using_model` around synchronous work in one thread;
- `bind_while_iterating` around a sync generator that a StreamingResponse drains. Starlette
  runs each `next()` in a fresh copy of the request's context, so a value set inside the
  generator would be gone by the next step; it is bound again around every step instead.

The graph does not use either: its payload names the model explicitly.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")

_selected_model: ContextVar[str | None] = ContextVar("agent_llm_model", default=None)


def normalized_model_name(value: str | None) -> str | None:
    """Blank means "the default", stored and sent as None."""
    text = (value or "").strip()
    return text or None


def agent_model_name(db: Session, tenant_id: uuid.UUID, role_code: str) -> str | None:
    from app.models.models import AIAgent

    value = (
        db.query(AIAgent.model_name)
        .filter(AIAgent.tenant_id == tenant_id, AIAgent.role_code == role_code.upper())
        .scalar()
    )
    return normalized_model_name(value)


def selected_model() -> str | None:
    return _selected_model.get()


@contextmanager
def using_model(model: str | None) -> Iterator[None]:
    token = _selected_model.set(normalized_model_name(model))
    try:
        yield
    finally:
        _selected_model.reset(token)


def bind_while_iterating(items: Iterator[T], model: str | None) -> Iterator[T]:
    iterator = iter(items)
    while True:
        with using_model(model):
            try:
                item = next(iterator)
            except StopIteration:
                return
        yield item


def agent_model_dependency(role_code: str):
    """A router dependency binding the agent's model for the whole request.

    Async on purpose: FastAPI awaits it in the request's own task, so the value lands in
    the context that the sync endpoint and a streamed body are both copied from. A sync
    dependency would run in a worker thread and set it in a context nobody else sees.
    """
    from fastapi import Depends
    from fastapi.concurrency import run_in_threadpool

    from app.core.database import get_db
    from app.core.security import get_current_active_user

    async def bind(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_active_user),
    ) -> None:
        model = await run_in_threadpool(agent_model_name, db, current_user.tenant_id, role_code)
        _selected_model.set(model)

    return bind
