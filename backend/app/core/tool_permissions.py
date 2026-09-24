"""The one rule for whether an AI Employee may use a tool, and the tool names it speaks.

Three doors used to answer this question with their own copies of the rule: the chat
executor (`_require_tool`, `_can_use_tool`), the internal tool gateway, and the payload
sent to LangGraph. They now all call `grant_decision`.

Knowledge search also had three names -- `rag_search` for the gateway, `hybrid_rag_search`
for the HR and Legal chats, `hybrid_search_documents` for the Knowledge chat -- for what is
one capability. It is `rag_search` everywhere now. The old names are still understood as
aliases wherever a name is read (agent rows, plugin manifests), so a configuration written
before the rename keeps working until migration w86d1f3a7b95 has rewritten it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

RENAMED_TOOLS: dict[str, str] = {
    "hybrid_rag_search": "rag_search",
    "hybrid_search_documents": "rag_search",
}


def canonical_tool_name(name: str) -> str:
    return RENAMED_TOOLS.get(name, name)


def canonical_tool_names(names: Iterable[str] | None) -> list[str]:
    """Names in their current spelling, first occurrence kept, order preserved."""
    return list(dict.fromkeys(canonical_tool_name(str(name)) for name in names or ()))


class Restriction(Protocol):
    def permits(self, tool_name: str) -> bool: ...


def grant_decision(
    tool_name: str,
    *,
    tools_access: Iterable[str] | None,
    allowed_actions: Iterable[str] | None,
    disallowed_actions: Iterable[str] | None,
    restriction: Restriction | None = None,
) -> str | None:
    """None when the tool may be used, else why not: "DENIED", "NOT_GRANTED" or "PLUGIN".

    A deny always wins; `allowed_actions`, when set, narrows `tools_access`; and a tenant's
    plugins can only take tools away, never add one.
    """
    name = canonical_tool_name(tool_name)
    if name in canonical_tool_names(disallowed_actions):
        return "DENIED"
    allowed = canonical_tool_names(allowed_actions)
    if name not in canonical_tool_names(tools_access) or (allowed and name not in allowed):
        return "NOT_GRANTED"
    if restriction is not None and not restriction.permits(name):
        return "PLUGIN"
    return None
