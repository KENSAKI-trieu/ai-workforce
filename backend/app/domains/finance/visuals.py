"""Charts drawn from the Finance reports: the same rows a tool or a page already returned.

A chart never carries a figure of its own. Each builder below reads one report's result
and lays its numbers out as categories and series; the kind of chart follows the shape
of the data -- a trend is a line, a part of a whole a donut, a comparison bars -- and the
viewer can switch only between the kinds that fit that shape. No model is involved.

A spec is plain JSON the frontend draws with recharts:
    {"title", "subtitle", "chart", "alternatives", "categories",
     "series": [{"key", "name", "values", "role"?}], "parts": [{"name", "value"}] | None,
     "ordinal", "unit", "source"}
Values stay exact decimal strings, as the reports give them.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app.domains.finance.charts import normal_balance
from app.domains.finance.money import ZERO, plain

# Beyond this many categories a pie and a stack stop being readable: the tail folds into
# "Khác" instead of taking a ninth colour.
MAX_PARTS = 6
MAX_STACK_CATEGORIES = 8
MAX_CARDS = 3

AGING_LABELS = {
    "NOT_DUE": "Chưa đến hạn",
    "1_30": "Quá hạn 1–30 ngày",
    "31_60": "Quá hạn 31–60 ngày",
    "61_90": "Quá hạn 61–90 ngày",
    "OVER_90": "Quá hạn trên 90 ngày",
}


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _month(period: str) -> str:
    return f"{period[5:7]}/{period[:4]}"


def _day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _spec(
    title: str,
    chart: str,
    alternatives: list[str],
    categories: list[str],
    series: list[dict[str, Any]],
    *,
    source: str,
    subtitle: str | None = None,
    parts: list[dict[str, str]] | None = None,
    ordinal: bool = False,
) -> dict[str, Any]:
    return {
        "type": "FINANCE_CHART",
        "title": title,
        "subtitle": subtitle,
        "chart": chart,
        "alternatives": alternatives,
        "categories": categories,
        "series": series,
        "parts": parts,
        "ordinal": ordinal,
        "unit": "VND",
        "source": source,
    }


def _fold(items: list[tuple[str, Decimal]], limit: int = MAX_PARTS) -> list[dict[str, str]]:
    """The largest positive parts, the rest summed as "Khác"."""
    positive = sorted(((name, value) for name, value in items if value > 0), key=lambda item: item[1], reverse=True)
    head, tail = positive[: limit - 1], positive[limit - 1:]
    if len(tail) == 1:
        head, tail = positive, []
    parts = [{"name": name, "value": plain(value)} for name, value in head]
    if tail:
        parts.append({"name": "Khác", "value": plain(sum((value for _, value in tail), ZERO))})
    return parts


def _net(side: dict[str, Any], normal: str) -> Decimal:
    """A balance as one signed figure on the account's normal side."""
    net = _d(side.get("debit")) - _d(side.get("credit"))
    return -net if normal == "CREDIT" else net


# ------------------------------------------------------------------------------ builders


def budget_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("rows") or []
    if not rows:
        return []
    categories = [f"{row['department']} · TK {row['account']}" for row in rows]
    over = [row["department"] for row in rows if row.get("over_budget")]
    return [_spec(
        f"Ngân sách và thực chi {_month(result['period'])}",
        "bar", ["bar", "table"], categories,
        [
            # The bar the spending is read against: drawn muted, so the spending stands out.
            {"key": "budget", "name": "Ngân sách", "values": [row["budget"] for row in rows], "role": "reference"},
            {"key": "actual", "name": "Thực chi", "values": [row["actual"] for row in rows]},
        ],
        subtitle=f"Vượt ngân sách: {', '.join(dict.fromkeys(over))}" if over else "Không phòng nào vượt ngân sách",
        source=result.get("source", ""),
    )]


def aging_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    parties = result.get("parties") or []
    if not parties:
        return []
    shown = parties[:MAX_STACK_CATEGORIES]
    rest = parties[MAX_STACK_CATEGORIES:]
    categories = [row["party"] or "Không rõ" for row in shown] + (["Khác"] if rest else [])
    series = []
    for bucket, label in AGING_LABELS.items():
        values = [row[bucket] for row in shown]
        if rest:
            values.append(plain(sum((_d(row[bucket]) for row in rest), ZERO)))
        if any(_d(value) for value in values):
            series.append({"key": bucket, "name": label, "values": values})
    buckets = result.get("buckets") or {}
    kind = "Phải thu khách hàng" if result.get("kind") == "RECEIVABLE" else "Phải trả nhà cung cấp"
    return [_spec(
        f"Tuổi nợ — {kind.lower()}",
        "stacked_bar", ["stacked_bar", "pie", "table"], categories, series,
        subtitle=f"Tính đến {_day(result['as_of'])}",
        parts=[
            {"name": AGING_LABELS[name], "value": value}
            for name, value in buckets.items() if _d(value) > 0
        ],
        ordinal=True,
        source=result.get("source", ""),
    )]


def schedule_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("invoices") or []
    if not rows:
        return []
    by_day: dict[str, dict[str, Decimal]] = {}
    by_vendor: dict[str, Decimal] = {}
    for row in rows:
        day = by_day.setdefault(row["due_date"], {"to_schedule": ZERO, "in_pending_voucher": ZERO, "already_scheduled": ZERO})
        for key in day:
            day[key] += _d(row[key])
        by_vendor[row["party"] or "Không rõ"] = by_vendor.get(row["party"] or "Không rõ", ZERO) + _d(row["remaining"])
    days = sorted(by_day)
    labels = {
        "to_schedule": "Chưa lập phiếu chi",
        "in_pending_voucher": "Phiếu chi chờ duyệt",
        "already_scheduled": "Đã duyệt chi",
    }
    series = [
        {"key": key, "name": label, "values": [plain(by_day[day][key]) for day in days]}
        for key, label in labels.items()
        if any(by_day[day][key] for day in days)
    ]
    return [_spec(
        "Hoá đơn mua vào đến hạn trả",
        "stacked_bar", ["stacked_bar", "pie", "table"], [_day(day) for day in days], series,
        subtitle=f"Từ {_day(result['as_of'])} đến {_day(result['until'])}; biểu đồ tròn chia theo nhà cung cấp",
        parts=_fold(list(by_vendor.items())),
        source=result.get("source", ""),
    )]


def balance_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Opening -> what came in -> what went out -> closing, for a one-sided account."""
    if result.get("two_sided") or not result.get("ledger_rows"):
        return []
    normal = normal_balance(result["account"])
    opening, closing = _net(result["opening"], normal), _net(result["closing"], normal)
    debit, credit = _d(result["period_debit"]), _d(result["period_credit"])
    increase, decrease = (credit, debit) if normal == "CREDIT" else (debit, credit)
    increase_label, decrease_label = ("Phát sinh Có", "Phát sinh Nợ") if normal == "CREDIT" else ("Phát sinh Nợ", "Phát sinh Có")
    name = f"TK {result['account']}" + (f" {result['account_name']}" if result.get("account_name") else "")
    return [_spec(
        f"Biến động {name} tháng {_month(result['period'])}",
        "waterfall", ["waterfall", "table"],
        ["Số dư đầu kỳ", increase_label, decrease_label, "Số dư cuối kỳ"],
        [{"key": "amount", "name": "Số tiền", "values": [plain(opening), plain(increase), plain(-decrease), plain(closing)]}],
        source=result.get("source", ""),
    )]


def trend_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    periods = result.get("periods") or []
    if not periods:
        return []
    categories = [_month(row["period"]) for row in periods]
    name = f"TK {result['account']}" + (f" {result['account_name']}" if result.get("account_name") else "")
    if result.get("two_sided"):
        balances = [
            {"key": "closing_debit", "name": "Dư Nợ cuối kỳ", "values": [row["closing"]["debit"] for row in periods]},
            {"key": "closing_credit", "name": "Dư Có cuối kỳ", "values": [row["closing"]["credit"] for row in periods]},
        ]
    else:
        normal = normal_balance(result["account"])
        balances = [{"key": "closing", "name": "Số dư cuối kỳ", "values": [plain(_net(row["closing"], normal)) for row in periods]}]
    source = result.get("source", "")
    # Balances and movements are drawn apart: a balance in billions next to movements in
    # millions flattens the movements to nothing on one axis.
    return [
        _spec(f"Số dư {name} qua các tháng", "line", ["line", "area", "bar", "table"], categories, balances, source=source),
        _spec(
            f"Phát sinh {name} qua các tháng", "bar", ["bar", "line", "table"], categories,
            [
                {"key": "period_debit", "name": "Phát sinh Nợ", "values": [row["period_debit"] for row in periods]},
                {"key": "period_credit", "name": "Phát sinh Có", "values": [row["period_credit"] for row in periods]},
            ],
            source=source,
        ),
    ]


def expense_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in result.get("rows") or [] if _d(row["amount"]) > 0]
    if not rows:
        return []
    by_account = result.get("group_by") == "account"
    labels = [
        (f"{row['key']} {row['name']}" if by_account and row.get("name") else row["name"] or row["key"])
        for row in rows
    ]
    span = _month(result["from"]) if result["from"] == result["to"] else f"{_month(result['from'])}–{_month(result['to'])}"
    many = len(rows) > MAX_PARTS
    return [_spec(
        f"Cơ cấu chi phí {span} theo {'tài khoản' if by_account else 'phòng ban'}",
        "bar" if many else "pie", ["bar", "pie", "table"] if many else ["pie", "bar", "table"],
        labels, [{"key": "amount", "name": "Chi phí", "values": [row["amount"] for row in rows]}],
        subtitle="Không tính bút toán kết chuyển sang TK 911",
        parts=_fold(list(zip(labels, (_d(row["amount"]) for row in rows)))),
        source=result.get("source", ""),
    )]


def ledger_chart(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The running balance of one account (or one party), line by line."""
    lines = [line for line in result.get("lines") or [] if line.get("balance")]
    if result.get("two_sided") or len(lines) < 2:
        return []
    normal = normal_balance(result["account"])
    return [_spec(
        f"Số dư TK {result['account']} theo từng nghiệp vụ",
        "line", ["line", "area", "table"],
        [f"{_day(line['date'])} {line.get('voucher_no') or ''}".strip() for line in lines],
        [{"key": "balance", "name": "Số dư", "values": [plain(_net(line["balance"], normal)) for line in lines]}],
        subtitle=f"Từ {_day(result['from'])} đến {_day(result['to'])}" + (" (đã cắt bớt dòng)" if result.get("truncated") else ""),
        source=result.get("source", ""),
    )]


# Gateway tool name -> builder. The same builders back the /finance pages.
BUILDERS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "budget_vs_actual": budget_chart,
    "ar_ap_aging": aging_chart,
    "payment_schedule": schedule_chart,
    "get_account_balance": balance_chart,
    "get_account_trend": trend_chart,
    "get_expense_breakdown": expense_chart,
    "get_ledger_detail": ledger_chart,
}


def charts_from_tool_calls(tool_calls: list[Any]) -> list[dict[str, Any]]:
    """The charts for a chat turn: one per chartable result the turn read, at most three."""
    cards: list[dict[str, Any]] = []
    for call in tool_calls or []:
        if not isinstance(call, dict) or call.get("status") != "SUCCESS":
            continue
        builder = BUILDERS.get(str(call.get("name") or call.get("tool_name") or ""))
        result = call.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if builder is None or not isinstance(result, dict) or result.get("found") is False:
            continue
        try:
            cards.extend(builder(result))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # A chart is a convenience: a result it cannot read leaves the answer as it is.
            continue
    return cards[:MAX_CARDS]
