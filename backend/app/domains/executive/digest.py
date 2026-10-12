"""The morning digest: the briefing's headline figures, sent as a notification.

Built from the same briefing the assistant and the page read, for the person receiving
it, so it shows only what their position may see. Written by code from the figures;
no model takes part, so nothing in it can be invented.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.domains.executive.briefing import build_briefing
from app.domains.finance.money import format_vnd
from app.models.models import User


def _permitted(section: Any) -> bool:
    return isinstance(section, dict) and section.get("permitted", True) is not False


def digest_lines(briefing: dict[str, Any], alert_count: int = 0) -> list[str]:
    """One line per section the reader may see, most pressing first."""
    sections = briefing.get("sections") or {}
    lines: list[str] = []
    approvals = sections.get("approvals") or {}
    if _permitted(approvals):
        waiting = int(approvals.get("waiting_count") or 0)
        risky = sum(count for level, count in (approvals.get("by_risk") or {}).items() if level in {"cao", "rất cao"})
        lines.append(
            f"Chờ bạn duyệt: {waiting} yêu cầu" + (f", {risky} rủi ro cao" if risky else "")
            if waiting else "Không có yêu cầu nào chờ bạn duyệt"
        )
    if alert_count:
        lines.append(f"Cảnh báo đang mở: {alert_count}")
    finance = sections.get("finance") or {}
    if _permitted(finance):
        receivable = finance.get("receivables_overdue") or {}
        if _permitted(receivable) and Decimal(str(receivable.get("total") or 0)) > 0:
            lines.append(
                f"Khách hàng nợ quá hạn: {format_vnd(Decimal(str(receivable['total'])))} "
                f"({receivable.get('customers', 0)} khách)"
            )
        payable = finance.get("payables_due") or {}
        if _permitted(payable) and payable.get("invoices"):
            lines.append(f"Hoá đơn phải trả trong {payable.get('within_days')} ngày: {payable['invoices']}")
        budget = finance.get("over_budget") or {}
        if _permitted(budget) and budget.get("lines_over_budget"):
            lines.append(f"Dòng ngân sách đang vượt: {budget['lines_over_budget']}")
    people = sections.get("people") or {}
    if _permitted(people):
        leave = people.get("on_leave") or {}
        if _permitted(leave):
            lines.append(f"Nghỉ hôm nay: {leave.get('people', 0)} người")
        ending = people.get("contracts_ending") or {}
        if _permitted(ending) and ending.get("contracts"):
            lines.append(f"Hợp đồng lao động hết hạn trong {ending.get('within_days')} ngày: {ending['contracts']}")
    legal = sections.get("legal") or {}
    if _permitted(legal) and legal.get("high_risk_unresolved"):
        lines.append(f"Hợp đồng rủi ro cao chưa xử lý: {legal['high_risk_unresolved']}")
    marketing = sections.get("marketing") or {}
    if _permitted(marketing) and marketing.get("campaigns_waiting"):
        lines.append(f"Chiến dịch marketing đang chờ: {marketing['campaigns_waiting']}")
    tasks = sections.get("tasks") or {}
    if _permitted(tasks) and tasks.get("handed_out_overdue"):
        lines.append(f"Việc bạn giao đã trễ hạn: {tasks['handed_out_overdue']}")
    return lines


def build_digest(db: Session, user: User, alert_count: int = 0) -> dict[str, Any]:
    briefing = build_briefing(db, user, period="TODAY")
    lines = digest_lines(briefing, alert_count)
    return {
        "today": briefing["today"],
        "title": "Bản tin điều hành hôm nay",
        "lines": lines,
        "message": "; ".join(lines) + ".",
    }
