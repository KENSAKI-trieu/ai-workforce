"""One lookup from a prompt slot name to the text the platform ships for it.

The plugin layer folds overrides by slot name, without a role in hand: a manifest names
``classifier``, and the overlay it produces is handed to whichever flow asked for that
tenant and role. So the defaults have to be reachable from the slot name alone, which is
why Legal's slots carry a ``legal_`` prefix instead of a second bare ``classifier``.

Importing this module must stay cheap. Both prompt modules are leaves holding nothing but
strings, and the plugin resolver imports this file lazily; nothing here may import the chat
flows in ``app.agents``, which import the resolver.
"""

from __future__ import annotations

from app.agents.hr.prompts import DEFAULT_HR_PROMPTS, HR_PROMPT_SLOTS
from app.agents.legal.prompts import DEFAULT_LEGAL_PROMPTS, LEGAL_PROMPT_SLOTS

PROMPT_SLOTS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "HR": tuple(HR_PROMPT_SLOTS),
    "LEGAL": tuple(LEGAL_PROMPT_SLOTS),
}

# The slot that writes the reply the user reads. The administrator's free text on the
# agent page lands here, and it is what a tenant's conventions (tone, terminology) are
# taken from when the agent runs through LangGraph. The other slots route turns or parse
# arguments and have no equivalent in the graph.
ANSWER_SLOT_BY_ROLE: dict[str, str] = {
    "HR": "answer",
    "LEGAL": "legal_answer",
}

_DEFAULTS: dict[str, str] = {**DEFAULT_HR_PROMPTS, **DEFAULT_LEGAL_PROMPTS}

# A slot name shared by two roles would make `default_prompt` ambiguous and silently give
# one role the other's prompt. Caught at import rather than at the first tenant preview.
assert len(_DEFAULTS) == len(DEFAULT_HR_PROMPTS) + len(DEFAULT_LEGAL_PROMPTS), (
    "prompt slot names must be unique across roles"
)


def default_prompt(slot: str) -> str:
    """Return the shipped prompt for a slot, or raise for an unknown slot name."""
    try:
        return _DEFAULTS[slot]
    except KeyError:
        raise KeyError(f"Unknown prompt slot: {slot}") from None


def answer_slot_for_role(role_code: str) -> str | None:
    """The slot writing this role's replies, or None for a role with no LLM reply."""
    return ANSWER_SLOT_BY_ROLE.get(role_code.strip().upper())


def prompt_slots_for_role(role_code: str) -> tuple[str, ...] | None:
    """Slot names a role accepts overrides for, or None when it accepts none."""
    return PROMPT_SLOTS_BY_ROLE.get(role_code.strip().upper())
