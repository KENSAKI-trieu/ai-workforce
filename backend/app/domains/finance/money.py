"""Reading and writing amounts the way Vietnamese books write them.

`1.234.567`, `1,234,567`, `1 234 567,50` and a bare `1234567` all mean what an accountant
expects; Excel cells arrive as numbers already. Amounts are Decimal end to end -- a float
turns 0.1 + 0.2 into a debit that no longer equals its credit.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0")


class AmountError(ValueError):
    pass


def to_decimal(value: Any) -> Decimal:
    """An amount from a cell, an XML text node or a model's JSON; raises AmountError."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    if isinstance(value, bool):
        raise AmountError(f"Not an amount: {value!r}")
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    text = re.sub(r"[\s ]|VNĐ|VND|đ", "", str(value), flags=re.IGNORECASE)
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if not text:
        return ZERO
    if "," in text and "." in text:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = text.replace(",", "") if len(tail) == 3 and head else text.replace(",", ".")
    elif text.count(".") > 1 or (text.count(".") == 1 and len(text.rpartition(".")[2]) == 3):
        # 1.234.567, or 1.234 -- a thousands separator. Vietnamese amounts with exactly
        # three decimals do not occur in practice.
        text = text.replace(".", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise AmountError(f"Not an amount: {value!r}") from exc
    if negative:
        amount = -amount
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_vnd(amount: Decimal | int | float) -> str:
    """`1.234.567 ₫` -- whole dong unless the amount has a fraction."""
    value = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{abs(value):,.2f}".partition(".")
    text = whole.replace(",", ".")
    if fraction != "00":
        text += "," + fraction
    return ("-" if value < 0 else "") + text + " ₫"


def plain(amount: Decimal) -> str:
    """A JSON-safe exact form: `"1234567.00"`."""
    return str(Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP))


def to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Not a date: {value!r}")


PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def normalize_period(value: Any) -> str:
    """`2026-09`, `09/2026`, `9/2026` or a date -> `2026-09`."""
    if isinstance(value, (date, datetime)):
        return period_of(value if isinstance(value, date) else value.date())
    text = str(value or "").strip()
    if PERIOD_RE.match(text):
        return text
    match = re.match(r"^(\d{1,2})[/-](\d{4})$", text)
    if match and 1 <= int(match.group(1)) <= 12:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    raise ValueError(f"Not a period: {value!r}")


TAX_CODE_RE = re.compile(r"^\d{10}(-\d{3})?$")


def normalize_tax_code(value: Any) -> str | None:
    """A tax code (MST): 10 digits, or 10 + `-` + 3 for a branch. None when empty."""
    if value is None:
        return None
    text = re.sub(r"\s", "", str(value))
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    if not text:
        return None
    if re.fullmatch(r"\d{13}", text):
        text = f"{text[:10]}-{text[10:]}"
    if not TAX_CODE_RE.match(text):
        raise ValueError(f"Mã số thuế không hợp lệ: {value!r}")
    return text
