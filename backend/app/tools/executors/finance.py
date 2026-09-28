"""Finance tools."""

from __future__ import annotations

from typing import Any

from app.domains.platform.audit_service import (
    get_cost_by_agent,
    get_cost_by_department,
    get_cost_by_employee,
    get_cost_by_workflow,
    get_llm_cost_summary,
)
from app.tools.registry import ToolContext
from app.tools.schemas import ExpenseLookupInput


def lookup_expenses(
    context: ToolContext,
    request: ExpenseLookupInput,
) -> dict[str, Any] | list[dict[str, Any]]:
    handlers = {
        "SUMMARY": get_llm_cost_summary,
        "AGENT": get_cost_by_agent,
        "EMPLOYEE": get_cost_by_employee,
        "DEPARTMENT": get_cost_by_department,
        "WORKFLOW": get_cost_by_workflow,
    }
    return handlers[request.breakdown](context.db, context.actor.tenant_id, request.month)
