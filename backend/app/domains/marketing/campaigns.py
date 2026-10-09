"""A campaign's stages, and which decision each one waits for.

    OUTLINE_PENDING --approve/edit--> DRAFTING --> DRAFTS_PENDING --approve/edit--> FINAL
          ^ |                                          ^                           |
          | reject (new outline from the feedback)     | edit after a rejection    submit
          +-+                                          |                           v
                                              REJECTED <--- approver --- SUBMITTED ---> APPROVED

Only the author decides the outline and the posts; a decision for any other stage is a
409, so a double click or a second tab cannot draft the same campaign twice. Whatever the
author types -- brief, edited outline or post, a rejection's reason -- goes through the
same jailbreak filter before it reaches a model or a stored campaign.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domains.knowledge.rag_service import hybrid_search_documents, in_reading_order, user_search_scope
from app.domains.marketing.approvals import open_marketing_approval
from app.domains.marketing.guardrails import detect_jailbreak
from app.domains.marketing.pipeline import (
    PLATFORMS,
    Progress,
    Writer,
    draft_and_check,
    no_progress,
    write_outline,
)
from app.models.models import AIAgent, MarketingCampaign, User

logger = logging.getLogger(__name__)

CREATE_PERMISSION = "marketing.campaign.create"
VIEW_ALL_PERMISSION = "marketing.campaign.view_all"
BLOCKED_MESSAGE = "Nội dung vi phạm chính sách."
MAX_BRIEF_CHARS = 8000
MAX_POST_CHARS = 8000
RAG_TOP_K = 6
# A campaign left in DRAFTING this long was abandoned by a request that died midway, and
# may be drafted again; a live one finishes in a minute or two.
STALE_DRAFTING = timedelta(minutes=10)


def _permissions(db: Session, user: User) -> set[str]:
    from app.domains.platform.position_service import user_permissions

    return set(user_permissions(db, user))


def require_create(db: Session, user: User) -> None:
    if CREATE_PERMISSION not in _permissions(db, user):
        raise HTTPException(status_code=403, detail="Chức vụ của bạn chưa được cấp quyền lập chiến dịch truyền thông")


def _guard(*texts: str | None) -> None:
    if any(detect_jailbreak(text) for text in texts if text):
        raise HTTPException(status_code=422, detail=BLOCKED_MESSAGE)


def new_writer(db: Session, user: User) -> Writer:
    """Model access metered to the Marketing agent."""
    from app.agents.usage import _llm_usage_recorder
    from app.clients.ai_service_client import get_ai_service_client

    return Writer(client=get_ai_service_client(), on_usage=_llm_usage_recorder(db, user, "MARKETING"))


def _title(brief: str) -> str:
    first = next((line.strip() for line in brief.splitlines() if line.strip()), "Chiến dịch")
    return first if len(first) <= 120 else first[:119].rstrip() + "…"


def retrieve_sources(db: Session, user: User, query: str) -> list[dict[str, Any]]:
    """The company documents the brief is about, numbered as the outline cites them.

    The Marketing agent's knowledge scope and the asker's own reach both apply. A failed
    search does not stop the campaign: it is written from the brief alone, and the prompts
    mark what is missing as "[cần bổ sung số liệu]".
    """
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "MARKETING"
    ).first()
    try:
        results = in_reading_order(hybrid_search_documents(
            db,
            user.tenant_id,
            query,
            top_k=RAG_TOP_K,
            agent_access=(agent.knowledge_access or None) if agent else None,
            **user_search_scope(db, user),
        ))
    except Exception:  # noqa: BLE001 - retrieval is an aid, not a precondition
        logger.exception("Marketing retrieval failed")
        return []
    return [
        {
            "ref": index,
            "chunk_id": str(item.get("id") or ""),
            "document_id": str(item.get("document_id") or ""),
            "document_title": item.get("document_title") or item.get("document_name") or "Tài liệu",
            "section_title": item.get("section_title"),
            "chunk_index": item.get("chunk_index"),
            "content": str(item.get("content") or ""),
        }
        for index, item in enumerate(results, start=1)
    ]


def context_of(sources: list[dict[str, Any]]) -> list[str]:
    """The numbered blocks the model reads, rebuilt from the stored sources."""
    blocks = []
    for source in sources or []:
        section = f" — {source['section_title']}" if source.get("section_title") else ""
        blocks.append(f"[{source.get('ref')}] {source.get('document_title')}{section}\n{source.get('content') or ''}")
    return blocks


def _visible(db: Session, user: User):
    query = db.query(MarketingCampaign).filter(MarketingCampaign.tenant_id == user.tenant_id)
    if VIEW_ALL_PERMISSION not in _permissions(db, user):
        query = query.filter(MarketingCampaign.created_by_id == user.id)
    return query


def _campaign_uuid(campaign_id: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(campaign_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Không tìm thấy chiến dịch") from None


def get_campaign(db: Session, user: User, campaign_id: Any) -> MarketingCampaign:
    campaign = _visible(db, user).filter(MarketingCampaign.id == _campaign_uuid(campaign_id)).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chiến dịch")
    return campaign


def list_campaigns(db: Session, user: User, *, limit: int = 50) -> list[MarketingCampaign]:
    return _visible(db, user).order_by(MarketingCampaign.created_at.desc()).limit(max(1, min(limit, 200))).all()


def _authored(db: Session, user: User, campaign_id: Any) -> MarketingCampaign:
    """The author's own campaign, locked: only they decide its stages."""
    campaign = db.query(MarketingCampaign).filter(
        MarketingCampaign.id == _campaign_uuid(campaign_id),
        MarketingCampaign.tenant_id == user.tenant_id,
        MarketingCampaign.created_by_id == user.id,
    ).with_for_update().first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chiến dịch")
    return campaign


def _expect(campaign: MarketingCampaign, *stages: str) -> None:
    if campaign.stage not in stages:
        raise HTTPException(
            status_code=409,
            detail=f"Chiến dịch đang ở bước {campaign.stage}, không nhận thao tác này",
        )


def start_campaign(
    db: Session,
    user: User,
    brief: str,
    *,
    writer: Writer,
    progress: Progress = no_progress,
    conversation_id: uuid.UUID | None = None,
) -> MarketingCampaign:
    """Brief → documents → outline, stopped for the author's first decision."""
    require_create(db, user)
    brief = (brief or "").strip()
    if not brief:
        raise HTTPException(status_code=422, detail="Hãy nhập brief chiến dịch")
    if len(brief) > MAX_BRIEF_CHARS:
        raise HTTPException(status_code=422, detail=f"Brief dài quá {MAX_BRIEF_CHARS} ký tự")
    progress("GUARDRAIL", "running", None)
    _guard(brief)
    progress("GUARDRAIL", "done", None)
    progress("RAG", "running", None)
    sources = retrieve_sources(db, user, brief)
    progress("RAG", "done", f"{len(sources)} đoạn tài liệu")
    progress("OUTLINE", "running", None)
    outline = write_outline(writer, brief=brief, context=context_of(sources))
    progress("OUTLINE", "done", None)
    campaign = MarketingCampaign(
        tenant_id=user.tenant_id,
        created_by_id=user.id,
        conversation_id=conversation_id,
        title=_title(brief),
        brief=brief,
        stage="OUTLINE_PENDING",
        outline=outline,
        sources=sources,
        drafts={},
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def decide_outline(
    db: Session,
    user: User,
    campaign_id: Any,
    action: str,
    *,
    writer: Writer,
    updated_outline: str | None = None,
    feedback: str | None = None,
    progress: Progress = no_progress,
) -> MarketingCampaign:
    """The first stop: approve or edit the outline (the posts follow), or reject it."""
    action = (action or "").lower()
    if action not in {"approve", "edit", "reject"}:
        raise HTTPException(status_code=422, detail="action phải là approve, edit hoặc reject")
    campaign = _authored(db, user, campaign_id)
    stale = (
        campaign.stage == "DRAFTING"
        and campaign.updated_at is not None
        and campaign.updated_at < datetime.now(timezone.utc) - STALE_DRAFTING
    )
    if not stale:
        _expect(campaign, "OUTLINE_PENDING")

    if action == "reject":
        feedback = (feedback or "").strip()
        if not feedback:
            raise HTTPException(status_code=422, detail="Hãy cho biết lý do từ chối để tôi đề xuất dàn ý khác")
        _guard(feedback)
        db.commit()  # release the row while the model works
        progress("RAG", "running", None)
        sources = retrieve_sources(db, user, f"{campaign.brief}\n{feedback}")
        progress("RAG", "done", f"{len(sources)} đoạn tài liệu")
        progress("OUTLINE", "running", None)
        outline = write_outline(
            writer,
            brief=campaign.brief,
            context=context_of(sources),
            previous_outline=campaign.outline,
            feedback=feedback,
        )
        progress("OUTLINE", "done", None)
        campaign = _authored(db, user, campaign_id)
        _expect(campaign, "OUTLINE_PENDING", "DRAFTING")
        campaign.outline, campaign.sources, campaign.outline_feedback = outline, sources, feedback
        campaign.stage = "OUTLINE_PENDING"
        db.commit()
        db.refresh(campaign)
        return campaign

    if action == "edit":
        updated_outline = (updated_outline or "").strip()
        if not updated_outline:
            raise HTTPException(status_code=422, detail="Dàn ý sửa không được để trống")
        _guard(updated_outline)
        campaign.outline = updated_outline
    campaign.outline_feedback = None
    campaign.stage = "DRAFTING"
    db.commit()
    brief, outline, context = campaign.brief, campaign.outline, context_of(campaign.sources)
    try:
        posts, report, rounds = draft_and_check(
            writer, brief=brief, outline=outline, context=context, progress=progress
        )
    except BaseException:
        db.rollback()
        campaign = _authored(db, user, campaign_id)
        campaign.stage = "OUTLINE_PENDING"
        db.commit()
        raise
    campaign = _authored(db, user, campaign_id)
    campaign.drafts, campaign.fact_check_report, campaign.refine_rounds = posts, report, rounds
    campaign.stage = "DRAFTS_PENDING"
    db.commit()
    db.refresh(campaign)
    return campaign


def decide_drafts(
    db: Session,
    user: User,
    campaign_id: Any,
    action: str,
    *,
    updated_drafts: dict[str, str] | None = None,
) -> MarketingCampaign:
    """The second stop: settle the posts as they are, or as the author corrected them."""
    action = (action or "").lower()
    if action not in {"approve", "edit"}:
        raise HTTPException(status_code=422, detail="action phải là approve hoặc edit")
    campaign = _authored(db, user, campaign_id)
    # A rejected campaign comes back here: the author corrects the posts and sends again.
    _expect(campaign, "DRAFTS_PENDING", "REJECTED")
    if action == "edit":
        edits = {key: str(value).strip() for key, value in (updated_drafts or {}).items()}
        if not edits or not set(edits) <= set(PLATFORMS):
            raise HTTPException(status_code=422, detail="Chỉ sửa được bài Facebook, Instagram hoặc Threads")
        if any(not text or len(text) > MAX_POST_CHARS for text in edits.values()):
            raise HTTPException(status_code=422, detail="Bài viết trống hoặc quá dài")
        _guard(*edits.values())
        changed = [platform for platform, text in edits.items() if text != (campaign.drafts or {}).get(platform)]
        campaign.drafts = {**(campaign.drafts or {}), **edits}
        if changed:
            # The fact-check read the earlier text: the approver is told which posts it
            # no longer vouches for.
            report = dict(campaign.fact_check_report or {})
            report["edited_platforms"] = sorted(set(report.get("edited_platforms") or []) | set(changed))
            campaign.fact_check_report = report
    campaign.stage = "FINAL"
    db.commit()
    db.refresh(campaign)
    return campaign


def submit_for_approval(db: Session, user: User, campaign_id: Any) -> tuple[MarketingCampaign, Any]:
    """Send the settled posts to whoever signs content; never to their author."""
    from app.domains.platform.approval_access import eligible_approvers
    from app.domains.platform.notification_service import create_notification

    campaign = _authored(db, user, campaign_id)
    _expect(campaign, "FINAL")
    approval = open_marketing_approval(db, user, campaign)
    campaign.approval_id = approval.id
    campaign.stage = "SUBMITTED"
    db.flush()
    db.refresh(approval)
    for approver in eligible_approvers(db, approval):
        create_notification(
            db,
            user=approver,
            event_type="APPROVAL_REQUIRED",
            title="Nội dung truyền thông cần duyệt",
            message=f"{user.full_name}: {campaign.title}",
            severity="INFO",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"marketing-approval:{approval.id}:{approver.id}",
        )
    db.commit()
    db.refresh(campaign)
    return campaign, approval


def serialize_campaign(campaign: MarketingCampaign, *, with_sources: bool = True) -> dict[str, Any]:
    item = {
        "id": str(campaign.id),
        "title": campaign.title,
        "stage": campaign.stage,
        "brief": campaign.brief,
        "outline": campaign.outline,
        "outline_feedback": campaign.outline_feedback,
        "drafts": campaign.drafts or {},
        "fact_check_report": campaign.fact_check_report,
        "refine_rounds": campaign.refine_rounds,
        "approval_id": str(campaign.approval_id) if campaign.approval_id else None,
        "created_by_id": str(campaign.created_by_id),
        "created_by_name": campaign.created_by.full_name if campaign.created_by else None,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
    }
    if with_sources:
        item["sources"] = campaign.sources or []
    return item
