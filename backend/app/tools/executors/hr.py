"""HR tools."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.models import User
from app.domains.hr.hr_employee_tools import get_employee_sections
from app.domains.hr.hr_service import query_leave_balance
from app.tools.registry import ToolContext
from app.tools.schemas import EmployeeLookupInput, LeaveLookupInput


def lookup_employee(context: ToolContext, request: EmployeeLookupInput) -> dict[str, Any]:
    return get_employee_sections(
        context.db,
        actor=context.actor,
        employee_id=request.employee_id,
        requested_sections=request.sections,
        purpose=request.purpose,
        tool_name="employee_lookup",
    )


def lookup_leave(context: ToolContext, request: LeaveLookupInput) -> dict[str, Any]:
    db = context.db
    actor = context.actor
    employee_id = request.employee_id or actor.id
    access = get_employee_sections(
        db,
        actor=actor,
        employee_id=employee_id,
        requested_sections=["LEAVE"],
        purpose="SELF_SERVICE" if employee_id == actor.id else "LEAVE_MANAGEMENT",
        tool_name="leave_lookup",
    )
    employee = db.query(User).filter(
        User.id == employee_id,
        User.tenant_id == actor.tenant_id,
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "employee_id": str(employee.id),
        "request_id": access["request_id"],
        "scope": access["scope"],
        "balance": query_leave_balance(db, employee, request.year),
    }
