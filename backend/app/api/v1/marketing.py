"""The Marketing Agent's campaigns: start one, decide its two stops, send it for approval.

The two steps that call the model -- the outline, then the posts and their fact-check --
stream their stages over SSE, as the Legal contract review does: they run for a minute or
more, and one spinner for all of it hid a slow stage from a stuck one. Each stage arrives
as ``progress`` (stage, status, detail, elapsed_ms), the campaign as ``complete``, a
refusal or failure as ``error``.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.agent_models import agent_model_name, using_model
from app.core.database import get_db
from app.core.security import PermissionRequired
from app.domains.marketing.campaigns import (
    MAX_BRIEF_CHARS,
    decide_drafts,
    decide_outline,
    get_campaign,
    list_campaigns,
    new_writer,
    serialize_campaign,
    start_campaign,
    submit_for_approval,
)
from app.domains.marketing.pipeline import MarketingModelUnavailable
from app.domains.platform.approval_access import eligible_approvers, no_approver_warning
from app.models.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing", tags=["Marketing Agent"])

_MEMBERS = PermissionRequired("marketing.campaign.create", "marketing.campaign.view_all")
_AUTHORS = PermissionRequired("marketing.campaign.create")

MODEL_UNAVAILABLE = "Dịch vụ AI tạm thời không phản hồi nên chưa viết được. Vui lòng thử lại sau ít phút."


class CampaignStart(BaseModel):
    brief: str = Field(min_length=1, max_length=MAX_BRIEF_CHARS)


class OutlineDecision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    updated_outline: str | None = Field(default=None, max_length=20000)
    feedback: str | None = Field(default=None, max_length=4000)


class DraftsDecision(BaseModel):
    action: Literal["approve", "edit"]
    updated_drafts: dict[str, str] | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _stream(db: Session, user: User, run: Callable[[Callable[..., None]], dict[str, Any]]) -> StreamingResponse:
    """Run one model step on a worker thread, forwarding its stages as SSE."""
    events: "queue.Queue[tuple[str, dict[str, Any]] | None]" = queue.Queue()
    started = time.monotonic()
    # Read here, on the request's thread: a plain thread does not inherit the context the
    # model would otherwise be bound in.
    model = agent_model_name(db, user.tenant_id, "MARKETING")

    def progress(stage: str, status: str, detail: str | None = None) -> None:
        events.put(("progress", {
            "stage": stage, "status": status, "detail": detail,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }))

    def work() -> None:
        try:
            with using_model(model):
                events.put(("complete", run(progress)))
        except HTTPException as exc:
            db.rollback()
            events.put(("error", {"message": str(exc.detail), "status_code": exc.status_code}))
        except MarketingModelUnavailable:
            db.rollback()
            events.put(("error", {"message": MODEL_UNAVAILABLE, "status_code": 503}))
        except Exception:
            db.rollback()
            logger.exception("Streamed marketing step failed")
            events.put(("error", {"message": "Không thể hoàn tất bước này. Vui lòng thử lại."}))
        finally:
            # The request's own cleanup does not wait for a streamed body; without this the
            # reads after the last commit would hold a transaction open.
            db.close()
            events.put(None)

    def body():
        threading.Thread(target=work, name="marketing-campaign", daemon=True).start()
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/campaigns/stream", summary="Start a campaign: brief, documents, outline (SSE)")
def stream_start_campaign(
    req: CampaignStart,
    db: Session = Depends(get_db),
    current_user: User = Depends(_AUTHORS),
) -> StreamingResponse:
    def run(progress) -> dict[str, Any]:
        campaign = start_campaign(
            db, current_user, req.brief, writer=new_writer(db, current_user), progress=progress
        )
        return serialize_campaign(campaign)

    return _stream(db, current_user, run)


@router.post(
    "/campaigns/{campaign_id}/outline/stream",
    summary="Approve, edit or reject the outline; approving writes and checks the posts (SSE)",
)
def stream_decide_outline(
    campaign_id: str,
    req: OutlineDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(_AUTHORS),
) -> StreamingResponse:
    def run(progress) -> dict[str, Any]:
        campaign = decide_outline(
            db, current_user, campaign_id, req.action,
            writer=new_writer(db, current_user),
            updated_outline=req.updated_outline,
            feedback=req.feedback,
            progress=progress,
        )
        return serialize_campaign(campaign)

    return _stream(db, current_user, run)


@router.post("/campaigns/{campaign_id}/drafts", summary="Settle the posts, as written or corrected")
def decide_campaign_drafts(
    campaign_id: str,
    req: DraftsDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(_AUTHORS),
) -> dict[str, Any]:
    campaign = decide_drafts(db, current_user, campaign_id, req.action, updated_drafts=req.updated_drafts)
    return serialize_campaign(campaign)


@router.post("/campaigns/{campaign_id}/submit-approval", summary="Send the settled posts for approval")
def submit_campaign_for_approval(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_AUTHORS),
) -> dict[str, Any]:
    campaign, approval = submit_for_approval(db, current_user, campaign_id)
    approvers = eligible_approvers(db, approval)
    return {
        "campaign": serialize_campaign(campaign),
        "approval_id": str(approval.id),
        "eligible_approver_count": len(approvers),
        # Sent all the same: the approval waits until someone is given the permission.
        "warning": None if approvers else no_approver_warning(approval),
    }


@router.get("/campaigns", summary="Campaigns the user may see, newest first")
def list_marketing_campaigns(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(_MEMBERS),
) -> list[dict[str, Any]]:
    return [
        serialize_campaign(campaign, with_sources=False)
        for campaign in list_campaigns(db, current_user, limit=limit)
    ]


@router.get("/campaigns/{campaign_id}", summary="One campaign with its sources")
def get_marketing_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_MEMBERS),
) -> dict[str, Any]:
    return serialize_campaign(get_campaign(db, current_user, campaign_id))
