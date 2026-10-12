"""Proactive alerts of the CEO assistant: rules in code, thresholds chosen per tenant.

Each rule reads the owning department's own service and is evaluated for one person,
with that person's permissions, exactly as the briefing is: an alert never tells someone
what their position may not see. A rule outside their reach simply finds nothing. Every
figure is worked out here; no model writes or judges an alert.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.domains.executive.briefing import window_for
from app.domains.executive.common import local_date, local_today, tenant_zone
from app.domains.executive.inbox import approvals_inbox
from app.domains.executive.tasks import FINISHED
from app.domains.finance.money import format_vnd
from app.domains.finance.reports import aging, budget_vs_actual
from app.domains.hr.hr_employee_tools import list_contract_status_summaries
from app.domains.platform.audit_service import get_llm_cost_summary
from app.domains.platform.position_service import user_permissions
from app.models.models import ExecutiveAlertRule, Task, User

CEO_PAGE = "/agents/CEO"


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    description: str
    unit: str  # what the threshold counts: "%" or "ngày"
    default: Decimal
    minimum: Decimal
    maximum: Decimal


RULES: tuple[Rule, ...] = (
    Rule("BUDGET_OVERRUN", "Vượt ngân sách", "Một dòng ngân sách tháng này đã chi vượt quá ngưỡng.", "%", Decimal(10), Decimal(0), Decimal(500)),
    Rule("RECEIVABLE_OVERDUE", "Khách hàng nợ quá hạn lâu", "Một khách hàng có hoá đơn quá hạn từ ngưỡng số ngày trở lên.", "ngày", Decimal(30), Decimal(1), Decimal(365)),
    Rule("CONTRACT_ENDING", "Hợp đồng lao động sắp hết hạn", "Hợp đồng lao động hết hạn trong ngưỡng số ngày tới.", "ngày", Decimal(30), Decimal(1), Decimal(180)),
    Rule("APPROVAL_WAITING", "Yêu cầu chờ bạn duyệt quá lâu", "Một yêu cầu chờ chính bạn duyệt từ ngưỡng số ngày trở lên.", "ngày", Decimal(3), Decimal(1), Decimal(60)),
    Rule("TASK_OVERDUE", "Việc bạn giao bị trễ hạn", "Việc bạn giao đã quá hạn từ ngưỡng số ngày trở lên mà chưa xong.", "ngày", Decimal(1), Decimal(1), Decimal(60)),
    Rule("AI_COST", "Chi phí AI gần chạm ngân sách", "Chi phí gọi mô hình AI tháng này đã dùng tới ngưỡng phần trăm ngân sách AI.", "%", Decimal(80), Decimal(1), Decimal(100)),
)
RULES_BY_KEY = {rule.key: rule for rule in RULES}


@dataclass(frozen=True)
class Setting:
    rule: Rule
    enabled: bool
    threshold: Decimal


def rule_settings(db: Session, tenant_id: uuid.UUID) -> list[Setting]:
    """Every rule with the tenant's choice, or its default when they made none."""
    stored = {
        row.rule_key: row
        for row in db.query(ExecutiveAlertRule).filter(ExecutiveAlertRule.tenant_id == tenant_id).all()
    }
    settings = []
    for rule in RULES:
        row = stored.get(rule.key)
        threshold = row.threshold if row is not None and row.threshold is not None else rule.default
        settings.append(Setting(rule, row.enabled if row is not None else True, Decimal(threshold)))
    return settings


def save_rule_settings(db: Session, actor: User, changes: list[dict[str, Any]]) -> list[Setting]:
    """Store the tenant's choices. A threshold outside the rule's range is refused, not clipped."""
    from fastapi import HTTPException

    stored = {
        row.rule_key: row
        for row in db.query(ExecutiveAlertRule).filter(ExecutiveAlertRule.tenant_id == actor.tenant_id).all()
    }
    for change in changes:
        rule = RULES_BY_KEY.get(str(change.get("key") or ""))
        if rule is None:
            raise HTTPException(status_code=422, detail=f"Không có luật cảnh báo {change.get('key')!r}")
        threshold = Decimal(str(change.get("threshold", rule.default)))
        if not rule.minimum <= threshold <= rule.maximum:
            raise HTTPException(
                status_code=422,
                detail=f"Ngưỡng của “{rule.label}” phải từ {rule.minimum} đến {rule.maximum} {rule.unit}",
            )
        row = stored.get(rule.key)
        if row is None:
            row = ExecutiveAlertRule(tenant_id=actor.tenant_id, rule_key=rule.key)
            db.add(row)
        row.enabled = bool(change.get("enabled", True))
        row.threshold = threshold
        row.updated_by_id = actor.id
    db.flush()
    return rule_settings(db, actor.tenant_id)


def serialize_setting(setting: Setting) -> dict[str, Any]:
    rule = setting.rule
    return {
        "key": rule.key,
        "label": rule.label,
        "description": rule.description,
        "unit": rule.unit,
        "enabled": setting.enabled,
        "threshold": _number(setting.threshold),
        "default": _number(rule.default),
        "min": _number(rule.minimum),
        "max": _number(rule.maximum),
    }


def _number(value: Decimal) -> float | int:
    return int(value) if value == value.to_integral_value() else float(value)


def _alert(rule: str, subject: str, title: str, message: str, *, severity: str = "WARNING", link: str = CEO_PAGE) -> dict[str, Any]:
    return {"rule": rule, "subject": subject, "title": title, "message": message, "severity": severity, "link": link}


# ----------------------------------------------------------------------------- rules


def _budget(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    if "finance.ledger.view" not in granted:
        return []
    today = local_today(db, user)
    period = window_for("MONTH", today).months[1]
    rows = budget_vs_actual(db, user.tenant_id, period)["rows"]
    over = [
        row for row in rows
        if row["over_budget"] and row.get("over_percent") is not None and Decimal(str(row["over_percent"])) >= threshold
    ]
    over.sort(key=lambda row: Decimal(str(row["over_amount"])), reverse=True)
    return [
        _alert(
            "BUDGET_OVERRUN",
            f"{period}:{row['department']}:{row['account']}",
            f"Vượt ngân sách: phòng {row['department']}, TK {row['account']}",
            (
                f"Tháng {period[5:]}/{period[:4]} đã chi {format_vnd(Decimal(str(row['actual'])))} trên ngân sách "
                f"{format_vnd(Decimal(str(row['budget'])))}, vượt {format_vnd(Decimal(str(row['over_amount'])))} "
                f"({Decimal(str(row['over_percent'])).normalize():f}%)."
            ),
            severity="CRITICAL" if Decimal(str(row["over_percent"])) >= threshold * 2 else "WARNING",
            link="/agents/FINANCE",
        )
        for row in over[:5]
    ]


def _receivables(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    if "finance.ar_ap.view" not in granted:
        return []
    today = local_today(db, user)
    report = aging(db, user.tenant_id, "OUT", today, min_days_overdue=1, invoice_limit=0)
    oldest: dict[str, int] = {}
    owed: dict[str, Decimal] = {}
    for invoice in report["invoices"]:
        party = invoice["party"]
        oldest[party] = max(oldest.get(party, 0), int(invoice["days_overdue"]))
    for party in report["parties"]:
        owed[party["party"]] = Decimal(str(party["total"]))
    late = sorted(
        (party for party, days in oldest.items() if days >= threshold and owed.get(party, Decimal(0)) > 0),
        key=lambda party: owed[party], reverse=True,
    )
    return [
        _alert(
            "RECEIVABLE_OVERDUE",
            party,
            f"Nợ quá hạn {oldest[party]} ngày: {party}",
            f"{party} còn nợ quá hạn {format_vnd(owed[party])}; hoá đơn cũ nhất đã quá hạn {oldest[party]} ngày.",
            severity="CRITICAL" if oldest[party] >= threshold * 2 else "WARNING",
            link="/agents/FINANCE",
        )
        for party in late[:5]
    ]


def _contracts(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    if "hr.contract.view" not in granted:
        return []
    items = list_contract_status_summaries(db, actor=user, limit=50, tool_name="executive_alerts")["items"]
    ending = [
        item for item in items
        if item["days_until_expiry"] is not None and 0 <= item["days_until_expiry"] <= threshold
    ]
    return [
        _alert(
            "CONTRACT_ENDING",
            f"{item['employee_id']}:{item['end_date']}",
            f"Hợp đồng lao động sắp hết hạn: {item['employee_name']}",
            (
                f"Hợp đồng của {item['employee_name']} hết hạn ngày "
                f"{datetime.fromisoformat(item['end_date']).strftime('%d/%m/%Y')}, còn {item['days_until_expiry']} ngày."
            ),
            severity="CRITICAL" if item["days_until_expiry"] <= 7 else "WARNING",
            link="/users-mgmt",
        )
        for item in ending[:10]
    ]


# Past this many, the rest of a rule's findings become one line rather than one each.
APPROVALS_LISTED = 4


def _day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _approvals(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    inbox = approvals_inbox(db, user, limit=200)
    stale = [item for item in inbox["items"] if item["waiting_days"] >= threshold]
    found = [
        _alert(
            "APPROVAL_WAITING",
            item["approval_id"],
            f"Chờ bạn duyệt {item['waiting_days']} ngày: {item['kind']}",
            f"“{item['title']}” gửi ngày {_day(item['sent_on'])} vẫn chờ bạn quyết định (rủi ro {item['risk']}).",
            severity="CRITICAL" if item["risk"] in {"cao", "rất cao"} else "WARNING",
            link=item["link"],
        )
        for item in stale[:APPROVALS_LISTED]
    ]
    rest = stale[APPROVALS_LISTED:]
    if rest:
        found.append(_alert(
            "APPROVAL_WAITING",
            f"more:{len(rest)}",
            f"Còn {len(rest)} yêu cầu khác chờ bạn duyệt từ {int(threshold)} ngày trở lên",
            f"Lâu nhất đã chờ {max(item['waiting_days'] for item in rest)} ngày. Mở trang Phê duyệt để xem hết.",
            link="/approvals",
        ))
    return found


def _tasks(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    zone = tenant_zone(db, user)
    today = local_today(db, user)
    now = datetime.now(timezone.utc)
    late = db.query(Task).filter(
        Task.tenant_id == user.tenant_id,
        Task.creator_id == user.id,
        Task.status.notin_(tuple(FINISHED)),
        Task.due_date.isnot(None),
        Task.due_date < now,
    ).order_by(Task.due_date.asc()).all()
    alerts = []
    for task in late:
        days = (today - local_date(task.due_date, zone)).days
        if days < threshold:
            continue
        who = task.assignee.full_name if task.assignee else "chưa có người nhận"
        alerts.append(_alert(
            "TASK_OVERDUE",
            str(task.id),
            f"Việc trễ hạn {days} ngày: {task.title}",
            f"Việc “{task.title}” giao cho {who} đã quá hạn {days} ngày mà chưa xong.",
            link="/tasks",
        ))
    return alerts[:10]


def _ai_cost(db: Session, user: User, threshold: Decimal, granted: frozenset[str]) -> list[dict[str, Any]]:
    if "finance.expense.view" not in granted:
        return []
    month = window_for("MONTH", local_today(db, user)).months[1]
    summary = get_llm_cost_summary(db, user.tenant_id, month)
    budget = Decimal(str(summary.get("monthly_budget_usd") or 0))
    used = Decimal(str(summary.get("budget_usage_pct") or 0))
    # Without an AI budget there is nothing to approach.
    if budget <= 0 or used < threshold:
        return []
    return [_alert(
        "AI_COST",
        month,
        f"Chi phí AI đã dùng {used.quantize(Decimal(1))}% ngân sách tháng",
        (
            f"Tháng {month[5:]}/{month[:4]} đã chi {Decimal(str(summary['total_estimated_cost_usd'])).quantize(Decimal('0.01'))} USD "
            f"trên ngân sách {budget.quantize(Decimal('0.01'))} USD."
        ),
        severity="CRITICAL" if used >= 100 else "WARNING",
        link="/costs",
    )]


CHECKS: dict[str, Callable[[Session, User, Decimal, frozenset[str]], list[dict[str, Any]]]] = {
    "BUDGET_OVERRUN": _budget,
    "RECEIVABLE_OVERDUE": _receivables,
    "CONTRACT_ENDING": _contracts,
    "APPROVAL_WAITING": _approvals,
    "TASK_OVERDUE": _tasks,
    "AI_COST": _ai_cost,
}
_SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


def evaluate_alerts(db: Session, user: User, settings: list[Setting] | None = None) -> list[dict[str, Any]]:
    """What the rules find today for ``user``, most serious first."""
    settings = settings if settings is not None else rule_settings(db, user.tenant_id)
    granted = user_permissions(db, user)
    found: list[dict[str, Any]] = []
    for setting in settings:
        if setting.enabled:
            found.extend(CHECKS[setting.rule.key](db, user, setting.threshold, granted))
    found.sort(key=lambda alert: _SEVERITY_ORDER.get(alert["severity"], 3))
    return found
