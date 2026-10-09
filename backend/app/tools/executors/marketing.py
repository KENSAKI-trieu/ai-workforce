"""Marketing tools."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.agent_models import agent_model_name, using_model
from app.domains.marketing.campaigns import new_writer, start_campaign
from app.models.models import ChatConversation, ChatMessage
from app.tools.registry import ToolContext
from app.tools.schemas import MarketingCampaignInput


def _user_messages(context: ToolContext, request: MarketingCampaignInput) -> list[str]:
    """The user's own messages in their own conversation, newest first."""
    conversation_id = request.audit.conversation_id
    if conversation_id is None:
        return []
    actor = context.actor
    # The id is caller trace data, so ownership is checked against the actor.
    conversation = context.db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.tenant_id == actor.tenant_id,
        ChatConversation.user_id == actor.id,
    ).first()
    if conversation is None:
        return []
    rows = context.db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender == "USER",
    ).order_by(ChatMessage.created_at.desc()).limit(request.from_user_message + 1).all()
    return [row.content or "" for row in rows]


def start_marketing_campaign(context: ToolContext, request: MarketingCampaignInput) -> dict[str, Any]:
    """Brief → documents → outline, then hand the author the page where they review it.

    Terminal: the reply links to the campaign. The outline and the posts are decided on the
    Marketing page, never in the chat, so nothing here is published or sent for approval.
    """
    messages = _user_messages(context, request)
    brief = messages[request.from_user_message].strip() if len(messages) > request.from_user_message else ""
    if not brief:
        return {"status": "NO_BRIEF", "reply": "Bạn mô tả giúp tôi chiến dịch cần lập: sản phẩm, mục tiêu, đối tượng và thời gian."}
    db, actor = context.db, context.actor
    try:
        with using_model(agent_model_name(db, actor.tenant_id, "MARKETING")):
            campaign = start_campaign(
                db, actor, brief,
                writer=new_writer(db, actor),
                conversation_id=request.audit.conversation_id,
            )
    except HTTPException as exc:
        if exc.status_code not in {403, 422}:
            raise
        return {"status": "REFUSED", "reply": str(exc.detail)}
    link = f"/agents/MARKETING?campaign={campaign.id}"
    return {
        "status": campaign.stage,
        "campaign_id": str(campaign.id),
        "url": link,
        "reply": (
            f"Tôi đã lập dàn ý cho chiến dịch **{campaign.title}** từ brief của bạn"
            f"{f' và {len(campaign.sources)} đoạn tài liệu công ty' if campaign.sources else ''}. "
            f"[Mở chiến dịch]({link}) để duyệt, sửa hoặc yêu cầu làm lại dàn ý; sau khi duyệt, "
            "tôi sẽ viết bài Facebook, Instagram, Threads và tự kiểm chứng số liệu."
        ),
    }
