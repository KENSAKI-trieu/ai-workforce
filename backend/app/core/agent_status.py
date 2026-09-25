"""Which AI Employees are open for chat, and which are still being built.

IT, Finance, Sales and CEO run on placeholder logic kept in ``app/domains/incubating``:
a fixed PO amount, a fixed product, a Jira key that no Jira ever issued, a CEO plan
reported as done when nothing was done. They stay listed so the product shows where it
is heading, but no chat turn and no API call reaches that logic until it is real.
"""

from __future__ import annotations

from fastapi import HTTPException

UNDER_DEVELOPMENT_ROLES: frozenset[str] = frozenset({"IT", "FINANCE", "SALES", "CEO"})

UNDER_DEVELOPMENT_REPLY = (
    "AI Employee này đang được phát triển (Under development) nên chưa nhận yêu cầu. "
    "Các chức năng của nó sẽ được mở khi hoàn thiện; hiện bạn có thể dùng HR, Legal "
    "hoặc Knowledge."
)


def is_under_development(role_code: str | None) -> bool:
    return str(role_code or "").strip().upper() in UNDER_DEVELOPMENT_ROLES


def refuse_under_development(role_code: str) -> None:
    """Endpoint guard: the direct APIs of an unfinished agent refuse like its chat does."""
    raise HTTPException(
        status_code=503,
        detail=f"{role_code.upper()} AI Employee is under development",
    )
