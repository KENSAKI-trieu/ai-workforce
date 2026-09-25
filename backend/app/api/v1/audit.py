"""
API Endpoints for Audit Logging and LLM Token Cost Metering.
"""

from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, RoleRequired
from app.models.models import User
from app.schemas.costs import CostSummaryResponse
from app.domains.platform.audit_service import get_audit_logs, get_llm_cost_summary
from app.domains.platform.audit_events import query_audit_events

router = APIRouter(prefix="/audit", tags=["Audit & Billing"])


@router.get(
    "/logs",
    summary="Query audit trail of agent tool executions (Manager & CEO only)",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "Manager", "CEO"))],
)
def fetch_audit_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[dict[str, Any]]:
    return get_audit_logs(db, current_user.tenant_id, limit=limit)


@router.get(
    "/events",
    summary="Query structured enterprise audit events",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "Manager", "CEO"))],
)
def fetch_audit_events(
    actor_type: Optional[str] = Query(None, pattern="^(USER|AGENT|SYSTEM)$"),
    status: Optional[str] = Query(None, pattern="^(SUCCESS|FAILED|PENDING|DENIED)$"),
    action: Optional[str] = Query(None, max_length=150),
    resource_type: Optional[str] = Query(None, max_length=100),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    events, total = query_audit_events(
        db,
        current_user,
        actor_type=actor_type,
        status=status,
        action=action,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=limit,
    )
    return {"items": events, "total": total, "offset": offset, "limit": limit}


@router.get(
    "/costs",
    response_model=CostSummaryResponse,
    summary="Query LLM token usage and cost metering report (CEO only)",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "CEO"))],
)
def fetch_cost_summary(
    month: str | None = Query(
        default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> CostSummaryResponse:
    return get_llm_cost_summary(db, current_user.tenant_id, month)
