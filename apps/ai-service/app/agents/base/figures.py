"""Holding a reply's figures to the tool results it was written from.

For a domain whose answers are numbers (Finance), a citation proves nothing: the model
can quote a real balance and then add two of them up wrong. So every figure in the reply
must be one a tool returned in this turn, or one the user wrote themselves. Rounded
figures ("1,23 tỷ") match within their rounding; exact ones ("11.000.000 ₫") must match
to the dong. Account numbers, years and day counts are not figures and are left alone.

Deterministic on purpose: no model judges another model's arithmetic here.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

_SCALES = {
    "tỷ": Decimal("1e9"),
    "tỉ": Decimal("1e9"),
    "triệu": Decimal("1e6"),
    "tr": Decimal("1e6"),
    "nghìn": Decimal("1e3"),
    "ngàn": Decimal("1e3"),
}
_MONEY_UNITS = {"đ", "₫", "vnd", "vnđ", "đồng", "dong"}

_TOKEN = re.compile(
    r"(?<![\w.,/-])(-?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d+)?)"
    r"\s*(tỷ|tỉ|triệu|tr\b|nghìn|ngàn|%|₫|đồng|đ\b|vnđ|vnd)?",
    re.IGNORECASE,
)
# Relative tolerance for a figure written with a scale word: "1,23 tỷ" stands for anything
# that rounds to it, which two decimals of a billion put within half a percent.
_ROUNDED_TOLERANCE = Decimal("0.005")
_PERCENT_TOLERANCE = Decimal("0.05")


def _number(text: str) -> Decimal | None:
    """Vietnamese or English separators: 1.234.567 / 1,234,567 / 1,5 / 1.5."""
    value = text.replace(" ", "")
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".") if value.rfind(",") > value.rfind(".") else value.replace(",", "")
    elif "," in value:
        head, _, tail = value.rpartition(",")
        # A thousands group never follows a lone 0: "0,125" is a fraction.
        thousands = len(tail) == 3 and head.lstrip("-") not in {"", "0"}
        value = value.replace(",", "") if thousands else value.replace(",", ".")
    elif value.count(".") > 1 or (
        value.count(".") == 1 and len(value.rpartition(".")[2]) == 3 and value.rpartition(".")[0].lstrip("-") != "0"
    ):
        value = value.replace(".", "")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def figures_in(answer: str) -> list[tuple[str, Decimal, str]]:
    """(as written, value, kind) for every figure in a reply; kind is EXACT, ROUNDED or PERCENT."""
    found: list[tuple[str, Decimal, str]] = []
    for match in _TOKEN.finditer(answer):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        value = _number(raw)
        if value is None:
            continue
        grouped = bool(re.search(r"\d[.,]\d{3}(?:\D|$)", raw)) and raw.count(".") + raw.count(",") >= 1
        digits = len(re.sub(r"\D", "", raw))
        if unit == "%":
            found.append((match.group(0).strip(), value, "PERCENT"))
        elif unit in _SCALES:
            found.append((match.group(0).strip(), value * _SCALES[unit], "ROUNDED"))
        elif unit in _MONEY_UNITS or grouped or digits >= 6:
            found.append((match.group(0).strip(), value, "EXACT"))
        # Anything else -- an account (331, 33311), a year, a day count, a list number --
        # is not a figure the books have to vouch for.
    return found


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def known_figures(results: Iterable[Any], texts: Iterable[str] = ()) -> set[Decimal]:
    """Every number the tools returned, and every number the user wrote."""
    known: set[Decimal] = set()
    for result in results:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass
        for item in _walk(result):
            if isinstance(item, bool) or item is None:
                continue
            if isinstance(item, (int, float, Decimal)):
                known.add(Decimal(str(item)))
            elif isinstance(item, str):
                for token in re.findall(r"-?\d[\d.,]*", item):
                    number = _number(token.rstrip(".,"))
                    if number is not None:
                        known.add(number)
    for text in texts:
        for _, value, _ in figures_in(text):
            known.add(value)
    return known


def _matches(value: Decimal, kind: str, known: set[Decimal]) -> bool:
    if kind == "EXACT":
        return any(abs(value - item) < Decimal("0.01") or abs(value + item) < Decimal("0.01") for item in known)
    if kind == "PERCENT":
        return any(abs(value - item) <= _PERCENT_TOLERANCE for item in known)
    return any(item and abs(value - abs(item)) <= abs(item) * _ROUNDED_TOLERANCE for item in known)


def unsupported_figures(answer: str, results: Iterable[Any], user_texts: Iterable[str] = ()) -> list[str]:
    """Figures in `answer` that no tool result (and no user message) contains."""
    known = known_figures(results, user_texts)
    return [written for written, value, kind in figures_in(answer) if not _matches(value, kind, known)]


def render_results(results: list[Any], *, limit: int = 30) -> str:
    """The tools' own figures as plain lines, shown in place of a withheld answer."""
    lines: list[str] = []

    def emit(prefix: str, value: Any) -> None:
        if len(lines) >= limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source", "invoice_id", "party_id", "id"}:
                    continue
                emit(f"{prefix}{key}." if isinstance(item, (dict, list)) else f"{prefix}{key}", item)
        elif isinstance(value, list):
            for index, item in enumerate(value[:10], start=1):
                emit(f"{prefix}{index}.", item)
        elif value not in (None, ""):
            lines.append(f"- {prefix.rstrip('.')}: {value}")

    for result in results:
        emit("", result)
    return "\n".join(lines)
