"""Input, output and tool-permission guardrails applied by the graph's nodes.

PII redaction lives in ``governance/middleware/redaction.py``; the model-call middleware
stack is in ``governance/middleware/stack.py``.
"""

from __future__ import annotations

from collections.abc import Iterable


def validate_input(text: str, *, max_characters: int = 50000) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Input cannot be empty")
    if len(normalized) > max_characters:
        raise ValueError("Input exceeds the configured limit")
    return normalized


def validate_grounded_output(answer: str, *, has_context: bool) -> str:
    """Reject an answer that cannot be shown to anyone.

    An empty answer is a failure whether or not retrieval returned context: the caller
    streams this string to the user, and "" reads as the agent having silently given up.
    The check used to apply only when context existed, so the ungrounded path -- the one
    where something did go wrong -- was the one that skipped it.
    """
    validated = answer.strip()
    if not validated:
        raise ValueError(
            "Grounded answer cannot be empty" if has_context else "Answer cannot be empty"
        )
    return validated


def is_tool_allowed(tool_name: str, allowed: Iterable[str], denied: Iterable[str]) -> bool:
    """The one rule that decides whether a governed tool may be used.

    Three places need this answer: binding the LangChain tools for a request, checking the
    model's chosen tool before an approval gate, and re-checking it after the gate resumes.
    ``allowed`` is the AI Employee's effective grant; ``denied`` its ``disallowed_actions``.
    A deny always wins, and an empty ``allowed`` grants nothing -- absence of a grant is
    not permission.
    """
    return tool_name not in set(denied) and tool_name in set(allowed)
