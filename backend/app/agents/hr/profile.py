"""Employee profile payloads and replies for the HR agent."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from app.models.models import User
from app.domains.hr.hr_employee_tools import get_employee_sections


def _employee_profile_payload(
    db: Session,
    actor: User,
    employee: User,
    *,
    requested_sections: list[str],
    purpose: str,
    tool_name: str,
) -> dict[str, Any]:
    access = get_employee_sections(
        db,
        actor=actor,
        employee_id=employee.id,
        requested_sections=requested_sections,
        purpose=purpose,
        tool_name=tool_name,
    )
    basic = access["data"].get("basic", {})
    return {
        "type": "EMPLOYEE_PROFILE",
        "employee": basic,
        "leave_balance": access["data"].get("leave"),
        "private": access["data"].get("private"),
        "contracts": access["data"].get("contract"),
        "compensation": access["data"].get("compensation"),
        "access": {
            "request_id": access["request_id"],
            "purpose": access["purpose"],
            "scope": access["scope"],
            "allowed_sections": access["allowed_sections"],
            "denied_sections": access["denied_sections"],
            "masked_fields": access["masked_fields"],
        },
    }


def _access_result(profile_payload: dict[str, Any]) -> dict[str, Any]:
    """What the policy engine actually released, for the tools_executed trace.

    Every profile branch reports this instead of a per-branch constant string, so the
    trace records which sections were allowed, denied and masked rather than restating
    the tool name.
    """
    access = profile_payload["access"]
    return {
        "allowed_sections": access["allowed_sections"],
        "denied_sections": access["denied_sections"],
        "masked_fields": access["masked_fields"],
    }


def _sql_directory_item(employee: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "type": "EMPLOYEE_PROFILE",
        "employee": employee,
        "leave_balance": None,
        "access": {
            "scope": scope,
            "purpose": "DIRECTORY_LOOKUP",
            "allowed_sections": ["BASIC"],
        },
    }


def _employee_profile_reply(payload: dict[str, Any]) -> str:
    employee = payload["employee"]
    balance = payload.get("leave_balance")
    reply = (
        f"Hồ sơ nhân sự của **{employee['name']}**:\n"
        f"- Email: **{employee['email']}**\n"
        f"- Vai trò: **{employee['role']}**\n"
        f"- Phòng ban: **{employee['department']}**\n"
        f"- Chức danh: **{employee['job_title'] or 'Chưa cập nhật'}**\n"
        f"- Trạng thái: **{employee['employment_status']}**\n"
        f"- Quản lý trực tiếp: **{employee['manager_name'] or 'Chưa thiết lập'}**"
    )
    if balance:
        reply += f"\n- Phép còn lại: **{balance['remaining_days']} ngày**"
    private = payload.get("private") or {}
    if private:
        reply += (
            f"\n- Điện thoại: **{private.get('phone') or 'Chưa cập nhật'}**"
            f"\n- Khu vực: **{private.get('city') or 'Chưa cập nhật'}"
            f"{', ' + private['country'] if private.get('country') else ''}**"
        )
    compensation = payload.get("compensation") or {}
    if compensation:
        salary = compensation.get("monthly_salary")
        salary_text = f"{salary:,.0f}" if isinstance(salary, (int, float)) else "Chưa cập nhật"
        reply += (
            f"\n- Lương tháng: **{salary_text} {compensation.get('salary_currency') or 'VND'}**"
        )
    contracts = payload.get("contracts")
    if contracts is not None:
        reply += f"\n- Hợp đồng được phép xem: **{len(contracts)}**"
    access = payload.get("access") or {}
    if access:
        reply += (
            f"\n\nPhạm vi dữ liệu: **{', '.join(access.get('allowed_sections') or [])}** · "
            f"Mục đích: **{access.get('purpose')}**."
        )
    return reply
