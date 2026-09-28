"""Task tools."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.models import AIAgent, Task, User
from app.tools.registry import ToolContext
from app.tools.schemas import CreateTaskInput


def _validate_task_target(context: ToolContext, request: CreateTaskInput) -> None:
    db = context.db
    actor = context.actor
    if request.assignee_id:
        assignee = db.query(User).filter(
            User.id == request.assignee_id,
            User.tenant_id == actor.tenant_id,
            User.is_active.is_(True),
        ).first()
        if not assignee:
            raise HTTPException(status_code=422, detail="Assignee is unavailable")
        if actor.role == "Manager" and assignee.department != actor.department:
            raise HTTPException(status_code=403, detail="Manager can only assign within their department")
        if actor.role == "Employee" and assignee.id != actor.id:
            raise HTTPException(status_code=403, detail="Employee can only assign a task to themselves")
    if request.ai_agent_id:
        agent = db.query(AIAgent).filter(
            AIAgent.id == request.ai_agent_id,
            AIAgent.tenant_id == actor.tenant_id,
            AIAgent.is_active.is_(True),
        ).first()
        if not agent:
            raise HTTPException(status_code=422, detail="AI Employee is unavailable")


def create_task(context: ToolContext, request: CreateTaskInput) -> dict[str, Any]:
    _validate_task_target(context, request)
    actor = context.actor
    task = Task(
        tenant_id=actor.tenant_id,
        title=request.title.strip(),
        description=request.description,
        creator_id=actor.id,
        assignee_id=request.assignee_id,
        ai_agent_id=request.ai_agent_id,
        priority=request.priority,
        due_date=request.due_date,
        status="PENDING",
        attachments=[],
    )
    context.db.add(task)
    context.db.flush()
    return {"task_id": str(task.id), "status": task.status}
