"""The one rule that decides whether a governed tool may be used.

Three places need this answer: binding the LangChain tools for a request, checking the
model's chosen tool before an approval gate, and re-checking it after the gate resumes. The
rule used to be written inline at each of them while this module went uncalled, which is
how a grant list in the wrong name space could silently produce an empty toolset instead of
a refusal anyone could see.

`allowed` is the AI Employee's `tools_access`; `denied` is its `disallowed_actions`. A deny
always wins, and an empty `allowed` grants nothing -- absence of a grant is not permission.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_tool_allowed(tool_name: str, allowed: Iterable[str], denied: Iterable[str]) -> bool:
    return tool_name not in set(denied) and tool_name in set(allowed)


def ensure_tool_allowed(tool_name: str, allowed: Iterable[str], denied: Iterable[str]) -> None:
    if not is_tool_allowed(tool_name, allowed, denied):
        raise PermissionError(f"Tool is not allowed: {tool_name}")
