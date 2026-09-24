"""Deterministic PII redaction for graph state and direct tool boundaries."""

from __future__ import annotations

import re
from typing import Any


PHONE_PATTERN = r"(?<!\d)(?:\+?84|0)(?:[ .-]?\d){9,10}(?!\d)"
PII_PATTERNS = (
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), "[REDACTED_CREDIT_CARD]"),
    (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[REDACTED_IP]"),
    (re.compile(PHONE_PATTERN), "[REDACTED_PHONE_NUMBER]"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_sensitive_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    return value
