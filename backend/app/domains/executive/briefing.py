"""Bản tin điều hành: the company in one read, each section behind its own permission.

Every section is read through the owning department's own service, so it keeps that
department's permission filter. A section the user's position may not see says so, with
the box it needs, rather than disappearing -- a director should know the picture is
partial. Every figure is computed here, in SQL and Decimal, including each change and
percentage: the assistant repeats them, it never works one out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session, load_only

from app.core.permissions import PERMISSIONS
from app.domains.executive.common import local_date, local_today, tenant_zone
from app.domains.executive.inbox import approvals_inbox
from app.domains.executive.tasks import FINISHED
from app.domains.finance.money import plain
from app.domains.finance.reports import aging, budget_vs_actual, income_statement, payment_schedule
from app.domains.finance.settings import get_settings
from app.domains.hr.hr_employee_tools import directory_scope, list_contract_status_summaries
from app.domains.hr.hr_service import leave_scope, list_leave_requests_in_scope
from app.domains.legal.contract_review_store import VIEW_ALL_REVIEWS_PERMISSION
from app.domains.platform.audit_service import get_llm_cost_summary
from app.domains.platform.position_service import user_permissions
from app.models.models import ContractReview, MarketingCampaign, Task, User

SECTIONS = ("FINANCE", "PEOPLE", "LEGAL", "MARKETING", "APPROVALS", "TASKS", "AI_COST")
PERIOD_LABELS = {"TODAY": "hôm nay", "WEEK": "tuần này", "MONTH": "tháng này", "QUARTER": "quý này"}
CONTRACT_HORIZON_DAYS = 30
PAYABLE_HORIZON_DAYS = 7
CAMPAIGN_WAITING = {
    "OUTLINE_PENDING": "dàn ý chờ người viết duyệt",
    "DRAFTS_PENDING": "bài viết chờ người viết duyệt",
    "SUBMITTED": "đã gửi phê duyệt",
}
MARKETING_VIEW_ALL = "marketing.campaign.view_all"
_LABELS = {item.code: item.label for item in PERMISSIONS}
ZERO = Decimal(0)


@dataclass(frozen=True)
class Window:
    period: str
    today: date
    start: date
    end: date
    # Month periods (YYYY-MM) the books are read for, and the ones compared against.
    months: tuple[str, str]
    previous_months: tuple[str, str]
    partial: bool


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def window_for(period: str, today: date) -> Window:
    month_start = today.replace(day=1)
    if period == "QUARTER":
        start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
        end = _shift_month(start, 3) - timedelta(days=1)
        previous_start = _shift_month(start, -3)
        return Window(
            period, today, start, end,
            (_month(start), _month(today)),
            (_month(previous_start), _month(_shift_month(start, -1))),
            partial=today < end,
        )
    if period == "WEEK":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "MONTH":
        start, end = month_start, _shift_month(month_start, 1) - timedelta(days=1)
    else:
        start = end = today
    previous = _month(_shift_month(month_start, -1))
    return Window(
        period, today, start, end,
        (_month(month_start), _month(month_start)),
        (previous, previous),
        partial=today < _shift_month(month_start, 1) - timedelta(days=1),
    )


def _not_permitted(*codes: str) -> dict[str, Any]:
    return {
        "permitted": False,
        "message": "Chức vụ của bạn chưa được cấp quyền xem mục này.",
        "needs": [_LABELS.get(code, code) for code in codes],
    }


def _change(current: Decimal, previous: Decimal) -> dict[str, Any]:
    change = current - previous
    return {
        "change": plain(change),
        # Of the previous period's size, so a loss shrinking reads as an improvement.
        "change_percent": plain(change * 100 / abs(previous)) if previous else None,
        "direction": "tăng" if change > 0 else "giảm" if change < 0 else "không đổi",
    }


# --------------------------------------------------------------------------- sections


def _finance(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    ledger, debts = "finance.ledger.view" in granted, "finance.ar_ap.view" in granted
    if not ledger and not debts:
        return _not_permitted("finance.ledger.view", "finance.ar_ap.view")
    section: dict[str, Any] = {"permitted": True}
    tenant = user.tenant_id
    if ledger:
        chart = get_settings(db, tenant).chart or "TT200"
        current = income_statement(db, tenant, *window.months, chart=chart)
        previous = income_statement(db, tenant, *window.previous_months, chart=chart)
        revenue, last_revenue = Decimal(current["revenue_net"]), Decimal(previous["revenue_net"])
        profit, last_profit = Decimal(current["profit_before_tax"]), Decimal(previous["profit_before_tax"])
        section["results"] = {
            "periods": f"{current['from']}..{current['to']}",
            "so_far": window.partial,
            "revenue": plain(revenue),
            "profit_before_tax": plain(profit),
            "compared_with": f"{previous['from']}..{previous['to']}",
            "previous_revenue": plain(last_revenue),
            "previous_profit_before_tax": plain(last_profit),
            "revenue_vs_previous": _change(revenue, last_revenue),
            "profit_vs_previous": _change(profit, last_profit),
            "note": current.get("note"),
        }
        budget = budget_vs_actual(db, tenant, window.months[1])
        over = sorted(
            (row for row in budget["rows"] if row["over_budget"]),
            key=lambda row: Decimal(row["over_amount"]), reverse=True,
        )
        section["over_budget"] = {
            "period": budget["period"],
            "lines_over_budget": len(over),
            "budget_lines": len(budget["rows"]),
            "top": [
                {key: row[key] for key in ("department", "account", "budget", "actual", "over_amount", "over_percent")}
                for row in over[:3]
            ],
        }
    else:
        section["results"] = _not_permitted("finance.ledger.view")
    if debts:
        receivable = aging(db, tenant, "OUT", window.today, min_days_overdue=1, invoice_limit=0)
        parties = [party for party in receivable["parties"] if Decimal(party["total"]) > 0]
        # Each customer with its own oldest overdue invoice: totals by age bracket, listed
        # beside the customers, were read as if each bracket were one customer's.
        oldest: dict[str, int] = {}
        for invoice in receivable["invoices"]:
            oldest[invoice["party"]] = max(oldest.get(invoice["party"], 0), invoice["days_overdue"])
        section["receivables_overdue"] = {
            "as_of": receivable["as_of"],
            "total": receivable["total"],
            "invoices": receivable["invoice_count"],
            "customers": len(parties),
            "largest": [
                {"customer": party["party"], "overdue": party["total"], "oldest_days_overdue": oldest.get(party["party"])}
                for party in parties[:3]
            ],
        }
        schedule = payment_schedule(db, tenant, window.today, PAYABLE_HORIZON_DAYS)
        section["payables_due"] = {
            "within_days": PAYABLE_HORIZON_DAYS,
            "until": schedule["until"],
            "invoices": len(schedule["invoices"]),
            "already_overdue": sum(1 for row in schedule["invoices"] if row["overdue"]),
            "still_to_schedule": schedule["total_to_schedule"],
        }
    else:
        section["receivables_overdue"] = section["payables_due"] = _not_permitted("finance.ar_ap.view")
    section["source"] = "sổ sách kế toán"
    return section


def _people(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    section: dict[str, Any] = {"permitted": True}
    readable, scope = directory_scope(db, user)
    if "hr.directory.view" in granted:
        section["headcount"] = {
            "active_employees": db.query(User).filter(
                User.tenant_id == user.tenant_id, User.is_active.is_(True), User.id.in_(readable),
            ).count(),
            "scope": scope,
        }
    else:
        section["headcount"] = _not_permitted("hr.directory.view")
    whose, leave_reach = leave_scope(db, user)
    if leave_reach == "SELF":
        section["on_leave"] = section["leave_waiting"] = _not_permitted("hr.leave.view")
    else:
        off = list_leave_requests_in_scope(
            db, user, employee_ids=whose, statuses={"APPROVED"},
            start_date=window.start, end_date=window.end, limit=30,
        )
        section["on_leave"] = {
            "between": f"{window.start.isoformat()}..{window.end.isoformat()}",
            "people": off["total_count"],
            "who": [
                {"name": item["employee"]["name"], "from": item["start_date"], "to": item["end_date"]}
                for item in off["items"]
            ],
        }
        waiting = list_leave_requests_in_scope(db, user, employee_ids=whose, statuses={"WAITING"}, limit=1)
        section["leave_waiting"] = {"requests": waiting["total_count"]}
    if "hr.contract.view" in granted:
        contracts = list_contract_status_summaries(
            db, actor=user, limit=50, tool_name="executive_briefing",
        )["items"]
        ending = [
            item for item in contracts
            if item["days_until_expiry"] is not None and 0 <= item["days_until_expiry"] <= CONTRACT_HORIZON_DAYS
        ]
        section["contracts_ending"] = {
            "within_days": CONTRACT_HORIZON_DAYS,
            "contracts": len(ending),
            "who": [
                {"name": item["employee_name"], "ends": item["end_date"], "type": item["contract_type"]}
                for item in ending[:10]
            ],
        }
    else:
        section["contracts_ending"] = _not_permitted("hr.contract.view")
    section["source"] = "hồ sơ nhân sự, đơn nghỉ phép và hợp đồng lao động trong phạm vi bạn được xem"
    return section


def _legal(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    everyone = VIEW_ALL_REVIEWS_PERMISSION in granted
    query = db.query(ContractReview).options(load_only(
        ContractReview.id, ContractReview.document_name, ContractReview.risk_level,
        ContractReview.risk_score, ContractReview.status, ContractReview.created_at,
        ContractReview.created_by_id,
    )).filter(
        ContractReview.tenant_id == user.tenant_id,
        ContractReview.risk_level.in_(("HIGH", "CRITICAL")),
        ContractReview.status == "OPEN",
    )
    if not everyone:
        query = query.filter(ContractReview.created_by_id == user.id)
    risky = query.order_by(ContractReview.risk_score.desc(), ContractReview.created_at.desc()).all()
    zone = tenant_zone(db, user)
    return {
        "permitted": True,
        "scope": "toàn công ty" if everyone else "chỉ hợp đồng do bạn rà soát",
        "high_risk_unresolved": len(risky),
        "critical": sum(1 for review in risky if review.risk_level == "CRITICAL"),
        "top": [
            {
                "contract": review.document_name,
                "risk": "rất cao" if review.risk_level == "CRITICAL" else "cao",
                "risk_score": review.risk_score,
                "reviewed_on": (local_date(review.created_at, zone) or window.today).isoformat(),
            }
            for review in risky[:5]
        ],
        "source": "hợp đồng đã rà soát rủi ro cao/rất cao còn phát hiện chưa xử lý",
    }


def _marketing(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    everyone = MARKETING_VIEW_ALL in granted
    query = db.query(MarketingCampaign).options(load_only(
        MarketingCampaign.id, MarketingCampaign.title, MarketingCampaign.stage,
        MarketingCampaign.created_by_id, MarketingCampaign.updated_at,
    )).filter(
        MarketingCampaign.tenant_id == user.tenant_id,
        MarketingCampaign.stage.in_(tuple(CAMPAIGN_WAITING)),
    )
    if not everyone:
        query = query.filter(MarketingCampaign.created_by_id == user.id)
    waiting = query.order_by(MarketingCampaign.updated_at.desc()).all()
    by_stage: dict[str, int] = {}
    for campaign in waiting:
        label = CAMPAIGN_WAITING[campaign.stage]
        by_stage[label] = by_stage.get(label, 0) + 1
    return {
        "permitted": True,
        "scope": "toàn công ty" if everyone else "chỉ chiến dịch của bạn",
        "campaigns_waiting": len(waiting),
        "by_stage": by_stage,
        "top": [
            {"campaign": campaign.title or "(chưa đặt tên)", "stage": CAMPAIGN_WAITING[campaign.stage]}
            for campaign in waiting[:5]
        ],
        "source": "chiến dịch marketing đang chờ người duyệt",
    }


def _approvals(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    inbox = approvals_inbox(db, user, limit=5)
    return {"permitted": True, **inbox}


def _tasks(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    zone = tenant_zone(db, user)
    handed_out = db.query(Task).filter(
        Task.tenant_id == user.tenant_id,
        Task.creator_id == user.id,
        Task.status.notin_(tuple(FINISHED)),
    ).all()
    overdue = sorted(
        (task for task in handed_out if task.due_date and task.due_date < now),
        key=lambda task: task.due_date,
    )
    window_end = datetime.combine(window.end, datetime.max.time(), tzinfo=zone)
    own_due = db.query(Task).filter(
        Task.tenant_id == user.tenant_id,
        Task.assignee_id == user.id,
        Task.status.notin_(tuple(FINISHED)),
        Task.due_date.isnot(None),
        Task.due_date <= window_end,
    ).count()
    return {
        "permitted": True,
        "handed_out_open": len(handed_out),
        "handed_out_overdue": len(overdue),
        "overdue": [
            {
                "task": task.title,
                "assignee": task.assignee.full_name if task.assignee else None,
                "due_date": local_date(task.due_date, zone).isoformat(),
                "days_overdue": (window.today - local_date(task.due_date, zone)).days,
            }
            for task in overdue[:5]
        ],
        "my_tasks_due_by_end_of_period": own_due,
        "page": "/tasks",
        "source": "task bạn giao và task giao cho bạn",
    }


def _ai_cost(db: Session, user: User, window: Window, granted: frozenset[str]) -> dict[str, Any]:
    if "finance.expense.view" not in granted:
        return _not_permitted("finance.expense.view")
    summary = get_llm_cost_summary(db, user.tenant_id, window.months[1])
    return {
        "permitted": True,
        "month": window.months[1],
        "cost_usd": summary["total_estimated_cost_usd"],
        "monthly_budget_usd": summary["monthly_budget_usd"],
        "budget_used_percent": summary["budget_usage_pct"],
        "requests": summary["total_requests"],
        "source": "nhật ký chi phí gọi mô hình AI",
    }


def compare_income_statements(
    db: Session, user: User, period: tuple[str, str], compared_with: tuple[str, str]
) -> dict[str, Any]:
    """Each line of the income statement for two ranges of months, with the change worked out.

    "Lợi nhuận quý 3 so với quý 2" is one call: read separately, the model was left to
    subtract one figure from the other itself, which it may not.
    """
    chart = get_settings(db, user.tenant_id).chart or "TT200"
    current = income_statement(db, user.tenant_id, *period, chart=chart)
    previous = income_statement(db, user.tenant_id, *compared_with, chart=chart)
    earlier = {row["code"]: Decimal(row["amount"]) for row in previous["rows"]}
    return {
        "period": f"{current['from']}..{current['to']}",
        "compared_with": f"{previous['from']}..{previous['to']}",
        "lines": [
            {
                "label": row["label"],
                "amount": row["amount"],
                "compared_amount": plain(earlier[row["code"]]),
                **_change(Decimal(row["amount"]), earlier[row["code"]]),
            }
            for row in current["rows"]
        ],
        "notes": [note for note in (current.get("note"), previous.get("note")) if note],
        "source": "báo cáo kết quả kinh doanh từ sổ sách kế toán",
    }


BUILDERS: dict[str, Callable[[Session, User, Window, frozenset[str]], dict[str, Any]]] = {
    "FINANCE": _finance,
    "PEOPLE": _people,
    "LEGAL": _legal,
    "MARKETING": _marketing,
    "APPROVALS": _approvals,
    "TASKS": _tasks,
    "AI_COST": _ai_cost,
}


def build_briefing(db: Session, user: User, *, period: str = "TODAY", sections: list[str] | None = None) -> dict[str, Any]:
    today = local_today(db, user)
    window = window_for(period, today)
    granted = user_permissions(db, user)
    wanted = [name for name in SECTIONS if not sections or name in sections]
    return {
        "period": PERIOD_LABELS.get(period, period.lower()),
        "today": today.isoformat(),
        "from": window.start.isoformat(),
        "to": window.end.isoformat(),
        "sections": {name.lower(): BUILDERS[name](db, user, window, granted) for name in wanted},
    }
