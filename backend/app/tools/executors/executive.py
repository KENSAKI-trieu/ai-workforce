"""The CEO assistant's tools: the overview, the decisions waiting, and the work handed out.

Every reading goes through the owning department's own filter (see app/domains/executive),
so a director sees through the assistant only what their position already shows them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import load_only

from app.domains.executive.briefing import (
    CAMPAIGN_WAITING,
    MARKETING_VIEW_ALL,
    build_briefing,
    compare_income_statements as compare,
)
from app.domains.executive.common import local_date, tenant_zone
from app.domains.executive.inbox import approvals_inbox
from app.domains.executive.tasks import assign_task as assign, list_tasks as tasks_of
from app.domains.legal.contract_review.schemas import CONTRACT_REVIEW_SCHEMAS
from app.domains.legal.contract_review_store import VIEW_ALL_REVIEWS_PERMISSION
from app.domains.platform.position_service import has_permission
from app.models.models import ContractReview, MarketingCampaign, User
from app.tools.executors.hr import _EMAIL, _user_messages
from app.tools.registry import ToolContext
from app.tools.schemas import (
    AssignTaskInput,
    CompareIncomeStatementsInput,
    ContractReviewListInput,
    ExecutiveBriefingInput,
    MarketingCampaignListInput,
    MyApprovalsInput,
    TaskListInput,
)

RISK_LABELS = {"CRITICAL": "rất cao", "HIGH": "cao", "MEDIUM": "trung bình", "LOW": "thấp"}
REVIEW_STATUS = {"OPEN": "còn phát hiện chưa xử lý", "RESOLVED": "đã xử lý hết phát hiện"}
CAMPAIGN_STAGES = {
    **CAMPAIGN_WAITING,
    "DRAFTING": "đang viết bài",
    "FINAL": "đã chốt bài, chưa gửi duyệt",
    "APPROVED": "đã được duyệt",
    "REJECTED": "bị từ chối",
}


def executive_briefing(context: ToolContext, request: ExecutiveBriefingInput) -> dict[str, Any]:
    return build_briefing(context.db, context.actor, period=request.period, sections=list(request.sections))


def compare_income_statements(context: ToolContext, request: CompareIncomeStatementsInput) -> dict[str, Any]:
    return compare(
        context.db, context.actor,
        (request.from_period, request.to_period), (request.compare_from, request.compare_to),
    )


def list_my_approvals(context: ToolContext, request: MyApprovalsInput) -> dict[str, Any]:
    return approvals_inbox(context.db, context.actor, limit=request.limit)


def list_contract_reviews(context: ToolContext, request: ContractReviewListInput) -> dict[str, Any]:
    """Saved contract reviews, riskiest first; everyone's only for "Xem mọi hồ sơ rà soát"."""
    db, actor = context.db, context.actor
    everyone = has_permission(db, actor, VIEW_ALL_REVIEWS_PERMISSION)
    # The listed columns only: the contract text and findings are encrypted and not needed.
    query = db.query(ContractReview).options(load_only(
        ContractReview.id, ContractReview.document_name, ContractReview.contract_type,
        ContractReview.risk_level, ContractReview.risk_score, ContractReview.total_findings,
        ContractReview.status, ContractReview.created_at, ContractReview.created_by_id,
    )).filter(ContractReview.tenant_id == actor.tenant_id)
    if not everyone:
        query = query.filter(ContractReview.created_by_id == actor.id)
    if request.risk_levels:
        query = query.filter(ContractReview.risk_level.in_(request.risk_levels))
    if request.unresolved_only:
        query = query.filter(ContractReview.status == "OPEN")
    total = query.count()
    reviews = query.order_by(ContractReview.risk_score.desc(), ContractReview.created_at.desc()).limit(request.limit).all()
    names = dict(db.query(User.id, User.full_name).filter(
        User.id.in_({review.created_by_id for review in reviews})
    ).all()) if reviews else {}
    zone = tenant_zone(db, actor)
    return {
        "scope": "toàn công ty" if everyone else "chỉ hợp đồng do bạn rà soát",
        "matching_count": total,
        "items": [
            {
                "contract": review.document_name,
                "contract_type": CONTRACT_REVIEW_SCHEMAS.get(review.contract_type, {}).get("label", review.contract_type),
                "risk": RISK_LABELS.get(review.risk_level, review.risk_level.lower()),
                "risk_score": review.risk_score,
                "findings": review.total_findings,
                "status": REVIEW_STATUS.get(review.status, review.status.lower()),
                "reviewed_on": local_date(review.created_at, zone).isoformat() if review.created_at else None,
                "reviewed_by": names.get(review.created_by_id),
            }
            for review in reviews
        ],
        "page": "/agents/LEGAL",
        "source": "hồ sơ rà soát hợp đồng",
    }


def list_marketing_campaigns(context: ToolContext, request: MarketingCampaignListInput) -> dict[str, Any]:
    """Campaigns and where each stands; everyone's only for "Xem mọi chiến dịch"."""
    db, actor = context.db, context.actor
    everyone = has_permission(db, actor, MARKETING_VIEW_ALL)
    # Brief, outline and posts are encrypted and not needed for a status list.
    query = db.query(MarketingCampaign).options(load_only(
        MarketingCampaign.id, MarketingCampaign.title, MarketingCampaign.stage,
        MarketingCampaign.created_by_id, MarketingCampaign.created_at, MarketingCampaign.updated_at,
    )).filter(MarketingCampaign.tenant_id == actor.tenant_id)
    if not everyone:
        query = query.filter(MarketingCampaign.created_by_id == actor.id)
    if request.waiting_only:
        query = query.filter(MarketingCampaign.stage.in_(tuple(CAMPAIGN_WAITING)))
    total = query.count()
    campaigns = query.order_by(MarketingCampaign.updated_at.desc()).limit(request.limit).all()
    names = dict(db.query(User.id, User.full_name).filter(
        User.id.in_({campaign.created_by_id for campaign in campaigns})
    ).all()) if campaigns else {}
    zone = tenant_zone(db, actor)
    return {
        "scope": "toàn công ty" if everyone else "chỉ chiến dịch của bạn",
        "matching_count": total,
        "items": [
            {
                "campaign": campaign.title or "(chưa đặt tên)",
                "stage": CAMPAIGN_STAGES.get(campaign.stage, campaign.stage.lower()),
                "author": names.get(campaign.created_by_id),
                "last_change": local_date(campaign.updated_at or campaign.created_at, zone).isoformat(),
            }
            for campaign in campaigns
        ],
        "page": "/agents/MARKETING",
        "source": "chiến dịch marketing",
    }


def assign_task(context: ToolContext, request: AssignTaskInput) -> dict[str, Any]:
    """Terminal: the reply says what was assigned to whom, or asks who was meant."""
    email = None
    if not request.assignee.strip() or "REDACTED" in request.assignee.upper():
        # An email reaches the model masked; the address is in the user's own message.
        email = next(
            (match.group(0) for text in _user_messages(context, request, 3) if (match := _EMAIL.search(text))),
            None,
        )
    return assign(
        context.db,
        context.actor,
        assignee=request.assignee,
        title=request.title,
        description=request.description,
        due_date=request.due_date,
        priority=request.priority,
        email=email,
    )


def list_tasks(context: ToolContext, request: TaskListInput) -> dict[str, Any]:
    return tasks_of(context.db, context.actor, scope=request.scope, status=request.status, limit=request.limit)
