"""Deterministic PII redaction for graph state and direct tool boundaries.

Each detector here is also handed to the model-call PIIMiddleware (see stack.py), so the
graph's own redaction and the middleware's agree on what counts as personal data.

A bare shape is not enough in contracts: "1.850.000.000 đồng" has the shape of an IPv4
address, a company tax code "0108765432" the shape of a phone number, and a bank account
the shape of a card number. Redacting those took the contract value out of the question
before the model read it, so a signing-authority question went unanswered.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Any

# The shape PIIMiddleware expects from a detector: type, value, start, end.
PIIMatch = dict[str, Any]

EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
CARD_PATTERN = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
# A dotted group that is part of a longer dotted number, or is followed by a currency,
# is an amount of money.
IPV4_PATTERN = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)(?!\s*(?:đồng|vnđ|vnd|đ\b|usd|\$))",
    re.IGNORECASE,
)
# Vietnamese mobile prefixes (03x, 05x, 07x, 08x, 09x after the 2018 renumbering) and
# 11-digit landlines (02x). A tax code starts 01, 03[01], 04... and is not a phone.
PHONE_PATTERN = re.compile(
    r"(?<![\d+])(?:(?:\+84|84|0)[ .-]?(?:3[2-9]|5[2689]|7[06-9]|8[1-9]|9\d)(?:[ .-]?\d){7}"
    r"|0[ .-]?2(?:[ .-]?\d){9})(?!\d)"
)
TAX_CODE_LABEL = re.compile(r"(?i)(?:mst|mã\s*số\s*thuế|mã\s*số\s*doanh\s*nghiệp|tax\s*(?:code|id))\s*[:.]?\s*$")


def _matches(pii_type: str, pattern: re.Pattern[str], content: str, keep: Callable[[re.Match[str]], bool]) -> list[PIIMatch]:
    return [
        {"type": pii_type, "value": match.group(), "start": match.start(), "end": match.end()}
        for match in pattern.finditer(content)
        if keep(match)
    ]


def _passes_luhn(number: str) -> bool:
    digits = [int(char) for char in number if char.isdigit()]
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _is_ipv4(match: re.Match[str]) -> bool:
    try:
        ipaddress.IPv4Address(match.group())  # rejects octets over 255 and leading zeros
    except ValueError:
        return False
    return True


def _follows_tax_label(match: re.Match[str]) -> bool:
    return bool(TAX_CODE_LABEL.search(match.string[max(0, match.start() - 40):match.start()]))


def detect_email(content: str) -> list[PIIMatch]:
    return _matches("email", EMAIL_PATTERN, content, lambda _: True)


def detect_credit_card(content: str) -> list[PIIMatch]:
    return _matches("credit_card", CARD_PATTERN, content, lambda match: _passes_luhn(match.group()))


def detect_ip(content: str) -> list[PIIMatch]:
    return _matches("ip", IPV4_PATTERN, content, _is_ipv4)


def detect_phone_number(content: str) -> list[PIIMatch]:
    return _matches("phone_number", PHONE_PATTERN, content, lambda match: not _follows_tax_label(match))


PII_DETECTORS: tuple[tuple[Callable[[str], list[PIIMatch]], str], ...] = (
    (detect_email, "[REDACTED_EMAIL]"),
    (detect_credit_card, "[REDACTED_CREDIT_CARD]"),
    (detect_ip, "[REDACTED_IP]"),
    (detect_phone_number, "[REDACTED_PHONE_NUMBER]"),
)


def redact_text(value: str) -> str:
    redacted = value
    for detector, replacement in PII_DETECTORS:
        for match in sorted(detector(redacted), key=lambda item: item["start"], reverse=True):
            redacted = redacted[:match["start"]] + replacement + redacted[match["end"]:]
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
