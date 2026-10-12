"""What waits for the user's own decision, most pressing first.

Exactly what the Approvals page lists for them (`can_approve`), so the two never disagree:
their own requests included when their position may sign them. The assistant lists and
ranks; deciding stays on that page, by a person.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.domains.executive.common import local_date, local_today, tenant_zone
from app.domains.platform.approval_access import can_approve
from app.models.models import AgentWorkflow, User, WorkflowApproval

APPROVALS_PAGE = "/approvals"

KIND_LABELS = {
    "LEAVE_REQUEST": "Đơn nghỉ phép",
    # The demo tenant's seeded sample (init_db), which the Approvals page still settles.
    "XIN_NGHI_PHEP": "Đơn nghỉ phép",
    "LEGAL_DOCUMENT_APPROVAL": "Văn bản pháp lý",
    "FINANCE_JOURNAL_APPROVAL": "Bút toán",
    "FINANCE_PAYMENT_VOUCHER": "Phiếu chi",
    "FINANCE_REMINDER_SEND": "Thư nhắc nợ",
    "MARKETING_CONTENT_APPROVAL": "Nội dung marketing",
    "SUPPORT_EMAIL_SEND": "Email trả lời khách hàng",
    "LANGGRAPH_INTERRUPT": "Thao tác của trợ lý AI",
}
RISK_LABELS = {"CRITICAL": "rất cao", "HIGH": "cao", "MEDIUM": "trung bình", "LOW": "thấp"}
_RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _amount(approval: WorkflowApproval) -> Decimal | None:
    # Only the amount the server wrote (Finance); a caller-supplied payload has no say.
    raw = (approval.payload or {}).get("amount")
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def waiting_for(db: Session, user: User) -> list[WorkflowApproval]:
    """Requests waiting for a decision that ``user`` may take."""
    rows = db.query(WorkflowApproval).join(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == user.tenant_id,
        WorkflowApproval.status == "WAITING",
    ).all()
    return [approval for approval in rows if can_approve(db, user, approval)]


def approvals_inbox(db: Session, user: User, *, limit: int = 10) -> dict[str, Any]:
    zone = tenant_zone(db, user)
    today = local_today(db, user)
    approvals = waiting_for(db, user)

    def waiting_days(approval: WorkflowApproval) -> int:
        sent = local_date(approval.workflow.created_at, zone) or today
        return max((today - sent).days, 0)

    approvals.sort(key=lambda item: (
        _RISK_ORDER.get(str(item.risk_level or "").upper(), 4),
        -(_amount(item) or Decimal(0)),
        -waiting_days(item),
    ))
    by_risk: dict[str, int] = {}
    for approval in approvals:
        level = str(approval.risk_level or "MEDIUM").upper()
        by_risk[level] = by_risk.get(level, 0) + 1
    items = []
    for approval in approvals[:limit]:
        amount = _amount(approval)
        level = str(approval.risk_level or "MEDIUM").upper()
        items.append({
            "approval_id": str(approval.id),
            "title": approval.workflow.title,
            "kind": KIND_LABELS.get(approval.action_type, approval.action_type),
            "requester": (approval.payload or {}).get("requester_name"),
            "sent_by_you": approval.workflow.initiator_id == user.id,
            "risk": RISK_LABELS.get(level, level.lower()),
            "amount": str(amount) if amount is not None else None,
            "waiting_days": waiting_days(approval),
            "sent_on": (local_date(approval.workflow.created_at, zone) or today).isoformat(),
            "link": f"{APPROVALS_PAGE}?id={approval.id}",
        })
    return {
        "waiting_count": len(approvals),
        "by_risk": {RISK_LABELS.get(level, level.lower()): count for level, count in by_risk.items()},
        "oldest_waiting_days": max((waiting_days(item) for item in approvals), default=0),
        "items": items,
        "page": APPROVALS_PAGE,
        "note": "Chỉ người duyệt bấm duyệt hoặc từ chối trên trang Phê duyệt; trợ lý không duyệt thay.",
        "source": "các yêu cầu chờ bạn duyệt trên trang Phê duyệt",
    }
