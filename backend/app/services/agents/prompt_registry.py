"""One lookup from a prompt slot name to the text the platform ships for it.

The plugin layer folds overrides by slot name, without a role in hand: a manifest names
``classifier``, and the overlay it produces is handed to whichever flow asked for that
tenant and role. So the defaults have to be reachable from the slot name alone, which is
why Legal's slots carry a ``legal_`` prefix instead of a second bare ``classifier``.

Importing this module must stay cheap. Both prompt modules are leaves holding nothing but
strings, so the deferred imports the plugin package uses elsewhere are not needed here --
but the agents *package* eagerly loads the executor, so nothing in this file may import
``app.services.agents`` itself.
"""

from __future__ import annotations

from app.services.agents.hr_prompts import DEFAULT_HR_PROMPTS, HR_PROMPT_SLOTS
from app.services.agents.legal_prompts import DEFAULT_LEGAL_PROMPTS, LEGAL_PROMPT_SLOTS

PROMPT_SLOTS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "HR": tuple(HR_PROMPT_SLOTS),
    "LEGAL": tuple(LEGAL_PROMPT_SLOTS),
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


def prompt_slots_for_role(role_code: str) -> tuple[str, ...] | None:
    """Slot names a role accepts overrides for, or None when it accepts none."""
    return PROMPT_SLOTS_BY_ROLE.get(role_code.strip().upper())
