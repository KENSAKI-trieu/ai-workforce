"""Shared plumbing for LLM calls whose answer must be a JSON object.

Both agent routers ask a model for a small, closed-vocabulary decision and then have to
survive whatever the provider actually sends back: fenced code blocks, a sentence around
the object, or nothing usable at all. That parsing, and the rule that metering must never
cost a user their answer, are identical for every router, so they live here rather than
being copied into each one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Called with the raw `/v1/llm/generate` payload after a billable call returns, so the
# caller -- which is the layer holding the database session -- can meter token usage.
# Router modules stay free of database imports; they only report what the provider charged.
UsageReporter = Callable[[dict[str, Any]], None]


def extract_json_object(content: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object out of a model reply, or None."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def report_usage(on_usage: UsageReporter | None, result: dict[str, Any]) -> None:
    """Hand a completed provider response to the meter, if one was supplied.

    Metering must never cost the user their answer, so a failing reporter is logged and
    swallowed here rather than being allowed to abort a turn that already succeeded.
    """
    if on_usage is None:
        return
    try:
        on_usage(result)
    except Exception:  # noqa: BLE001 - telemetry must not break the chat turn
        logger.warning("LLM usage reporting failed", exc_info=True)


def is_echo_provider(result: dict[str, Any]) -> bool:
    """Whether this reply came from the deterministic local provider.

    That provider echoes its input and cannot classify anything. It also bills nothing,
    so a caller seeing this must fall back and must not meter the call.
    """
    return str(result.get("provider") or "").lower() == "local"
