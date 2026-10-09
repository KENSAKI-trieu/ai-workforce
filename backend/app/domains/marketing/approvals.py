"""Approval of a campaign's final posts before they are published.

The author sends the posts they settled on; someone holding marketing.content.approve
signs or rejects them in the approval centre, and may correct a post while approving. The
author never signs their own campaign. Signing changes the campaign's stage; nothing is
posted anywhere by the system.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domains.marketing.guardrails import detect_jailbreak
from app.domains.marketing.pipeline import PLATFORMS
from app.models.models import AgentWorkflow, MarketingCampaign, User, WorkflowApproval

MARKETING_CONTENT_APPROVAL = "MARKETING_CONTENT_APPROVAL"
APPROVE_PERMISSION = "marketing.content.approve"
APPROVE_PERMISSION_LABEL = "Duyệt nội dung truyền thông"


def is_marketing_approval(approval: WorkflowApproval) -> bool:
    return approval.action_type == MARKETING_CONTENT_APPROVAL


def _fact_check_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report or {}
    issues = report.get("issues") or []
    return {
        "passed": bool(report.get("passed")),
        "checked": bool(report.get("checked", True)),
        "summary": report.get("summary") or "",
        "open_issues": len(issues),
        "edited_after_check": list(report.get("edited_platforms") or []),
    }


def open_marketing_approval(db: Session, actor: User, campaign: MarketingCampaign) -> WorkflowApproval:
    """A WAITING approval carrying the posts as they will be published."""
    workflow = AgentWorkflow(
        tenant_id=actor.tenant_id,
        initiator_id=actor.id,
        title=f"Duyệt nội dung: {campaign.title}"[:255],
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={"agent_role": "MARKETING", "steps": ["OUTLINE", "DRAFTS", "HUMAN_APPROVAL"]},
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type=MARKETING_CONTENT_APPROVAL,
        risk_level="MEDIUM",
        payload={
            # Written by the server: who asked, and which campaign the decision lands on.
            "requester_id": str(actor.id),
            "requester_name": actor.full_name,
            "required_permission": APPROVE_PERMISSION,
            "required_permission_label": APPROVE_PERMISSION_LABEL,
            "marketing_campaign_id": str(campaign.id),
            "title": campaign.title,
            "reason": f"Bài truyền thông đa kênh: {campaign.title}",
            "drafts": {platform: (campaign.drafts or {}).get(platform, "") for platform in PLATFORMS},
            "fact_check": _fact_check_summary(campaign.fact_check_report),
        },
        status="WAITING",
    )
    db.add(approval)
    db.flush()
    return approval


def can_approve_marketing(db: Session | None, user: User, approval: WorkflowApproval) -> bool:
    from app.domains.platform.position_service import user_permissions

    payload = approval.payload or {}
    if str(payload.get("requester_id") or "") == str(user.id):
        return False
    if approval.workflow is not None and approval.workflow.initiator_id == user.id:
        return False
    return APPROVE_PERMISSION in user_permissions(db, user)


def apply_marketing_edit(approval: WorkflowApproval, edited: dict[str, Any]) -> dict[str, Any]:
    """The payload after an approver corrected posts: only post texts may change."""
    drafts = (edited or {}).get("drafts")
    if not isinstance(drafts, dict) or not drafts or not set(drafts) <= set(PLATFORMS):
        raise HTTPException(status_code=422, detail="Chỉ sửa được nội dung bài Facebook, Instagram hoặc Threads")
    cleaned = {platform: str(text).strip() for platform, text in drafts.items()}
    if any(not text for text in cleaned.values()):
        raise HTTPException(status_code=422, detail="Bài viết không được để trống")
    if any(detect_jailbreak(text) for text in cleaned.values()):
        raise HTTPException(status_code=422, detail="Nội dung vi phạm chính sách.")
    payload = dict(approval.payload or {})
    payload["drafts"] = {**(payload.get("drafts") or {}), **cleaned}
    payload["edited_by_approver"] = sorted(cleaned)
    return payload


def finalize_marketing_approval(db: Session, approval: WorkflowApproval, approved: bool) -> None:
    """Carry the decision, and an approver's corrections, back to the campaign."""
    campaign_id = (approval.payload or {}).get("marketing_campaign_id")
    campaign = db.query(MarketingCampaign).filter(
        MarketingCampaign.id == campaign_id,
        MarketingCampaign.approval_id == approval.id,
    ).first() if campaign_id else None
    if campaign is None:
        return
    if approved:
        campaign.drafts = {**(campaign.drafts or {}), **((approval.payload or {}).get("drafts") or {})}
    campaign.stage = "APPROVED" if approved else "REJECTED"
    db.flush()
