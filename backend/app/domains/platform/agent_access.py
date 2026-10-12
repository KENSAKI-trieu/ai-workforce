"""Who may talk to an AI Employee.

Most agents are open to every member of the workspace: what a turn may read or do is
decided tool by tool, by the asker's own permissions. A few are for some positions only.
The box that opens each of those is listed here; an agent not listed needs none.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domains.platform.position_service import has_permission
from app.models.models import User

# Ticked in org-structure as "Dùng Trợ lý CEO".
AGENT_USE_PERMISSIONS: dict[str, str] = {"CEO": "executive.briefing.view"}

AGENT_USE_REFUSED = (
    "Chức vụ của bạn chưa được cấp quyền dùng agent này. "
    "Hãy nhờ quản trị viên tích quyền ở trang Cơ cấu tổ chức."
)


def can_use_agent(db: Session | None, user: User, role_code: str | None) -> bool:
    permission = AGENT_USE_PERMISSIONS.get(str(role_code or "").strip().upper())
    return permission is None or has_permission(db, user, permission)


def require_agent_use(db: Session | None, user: User, role_code: str | None) -> None:
    if not can_use_agent(db, user, role_code):
        raise HTTPException(status_code=403, detail=AGENT_USE_REFUSED)
