"""The HR gate: route the turn with the LLM, then run it, announcing each phase."""

from __future__ import annotations

from typing import Dict, Any, Iterator

from sqlalchemy.orm import Session
from app.models.models import User
from app.plugins.resolver import resolve_prompt_overlay
from app.agents.hr.llm_flow import (
    ACTION_INTENTS,
    SYNTHESIZABLE_INTENTS,
    classify_hr_request,
    classify_leave_draft_turn,
    generate_grounded_hr_answer,
)
from app.agents.chat import _execute_agent_chat_core
from app.agents.hr.intent import _classify_hr_intent
from app.agents.hr.leave import (
    _is_leave_cancel_message,
    _is_leave_draft_continuation,
    _load_leave_draft,
)
from app.agents.text import _normalize_intent_text
from app.agents.usage import _hr_llm_usage_recorder


def stream_hr_chat_events(
    db: Session,
    user: User,
    role_code: str,
    message: str,
    thread_id: str | None = None,
) -> Iterator[Dict[str, Any]]:
    """Run the HR gate, yielding a phase before each blocking step.

    Every step here is slow enough to be worth announcing: intent routing and answer
    synthesis each call the LLM, and dispatch may hit retrieval. Callers that only want
    the answer drain the generator; the SSE endpoint forwards the phases as they arrive.
    """
    yield {"event": "status", "phase": "ANALYZING"}

    record_usage = _hr_llm_usage_recorder(db, user)
    # One lookup per turn. None when the tenant installed nothing, which is the common
    # case and keeps the default path identical to what shipped.
    prompt_overlay = resolve_prompt_overlay(db, user.tenant_id, role_code)
    detailed_intent = _classify_hr_intent(message)
    leave_draft = _load_leave_draft(db, user, thread_id)
    normalized_message = _normalize_intent_text(message)
    leave_cancel_request = bool(
        leave_draft and _is_leave_cancel_message(normalized_message)
    )
    leave_continuation = bool(
        leave_draft
        and not leave_cancel_request
        and _is_leave_draft_continuation(message, leave_draft)
    )
    draft_turn_read_by_model = False
    if leave_draft:
        # The keyword rules above only recognise dates, weekday words and four fixed
        # cancel phrases. The router reads the sentence instead, and their answer is
        # what it falls back to.
        draft_turn = classify_leave_draft_turn(
            message,
            draft=leave_draft,
            fallback_turn=(
                "CANCEL" if leave_cancel_request
                else "CONTINUE" if leave_continuation
                else "UNRELATED"
            ),
            on_usage=record_usage,
            prompts=prompt_overlay,
        )
        # Cancelling is the outcome that writes nothing, so either reading of it wins.
        leave_cancel_request = leave_cancel_request or draft_turn.turn == "CANCEL"
        leave_continuation = not leave_cancel_request and draft_turn.turn == "CONTINUE"
        draft_turn_read_by_model = draft_turn.source == "llm"
    stateful_leave_action = leave_cancel_request or leave_continuation
    if stateful_leave_action:
        detailed_intent = "ACTION_LEAVE_REQUEST"

    if stateful_leave_action and draft_turn_read_by_model:
        # The draft router read this message next to the draft it answers, and it had
        # UNRELATED for a question. The general router sees only the bare sentence --
        # "Lý do là đi khám bệnh", "Ngày 4/1/2027" -- and files it as a question, so
        # letting it overrule CONTINUE meant no draft could be finished across turns.
        request_kind = "ACTION"
        routed_intent = detailed_intent
    else:
        classification = classify_hr_request(
            message,
            detailed_intent=detailed_intent,
            on_usage=record_usage,
            prompts=prompt_overlay,
        )

        # Only a keyword reading of the draft reaches here as a continuation. Those
        # rules take any date-like token for an answer, so the router still gets to say
        # this turn is a question. Cancelling is never ambiguous and is not reopened.
        if (
            leave_continuation
            and classification.source == "llm"
            and classification.kind == "QUESTION"
            and classification.intent is not None
            and classification.intent not in ACTION_INTENTS
        ):
            stateful_leave_action = False

        request_kind = "ACTION" if stateful_leave_action else classification.kind

        routed_intent = detailed_intent
        if classification.source == "llm" and not stateful_leave_action:
            # The router understands paraphrases the keyword rules cannot cover. Its
            # label is already restricted to HR_INTENT_LABELS, and the branch it selects
            # still enforces tool permissions and purpose limitation.
            if classification.intent:
                routed_intent = classification.intent
            if request_kind == "QUESTION" and routed_intent in ACTION_INTENTS:
                # A question about an operation must not execute that operation.
                routed_intent = "POLICY_QUERY"
            elif request_kind == "QUESTION" and routed_intent == "UNKNOWN":
                # The HR agent was explicitly selected, so retrieve governed HR context.
                routed_intent = "POLICY_QUERY"
            elif request_kind == "ACTION" and routed_intent not in ACTION_INTENTS:
                # The model cannot invent a tool name or arguments. Unknown actions
                # fail closed.
                routed_intent = "UNKNOWN"

    yield {"event": "status", "phase": "SEARCHING"}
    response = _execute_agent_chat_core(
        db,
        user,
        role_code,
        message,
        thread_id,
        hr_intent_override=routed_intent,
        leave_draft=leave_draft,
        leave_cancel_request=leave_cancel_request,
        on_llm_usage=record_usage,
    )
    if response.get("tools_executed"):
        yield {"event": "status", "phase": "TOOL_CALLING"}

    if request_kind == "QUESTION" and routed_intent in SYNTHESIZABLE_INTENTS:
        # Self-service profile, compensation and leave-balance answers are already exact
        # and already contain personal data; they are neither improved nor safely
        # rewritten by a generative pass.
        response = generate_grounded_hr_answer(
            message, response, on_usage=record_usage, prompts=prompt_overlay
        )

    yield {"event": "status", "phase": "COMPLETED"}
    yield {"event": "complete", "response": response}
