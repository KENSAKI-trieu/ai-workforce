"""Handing work out from the chat, and following it up.

A task goes to one real person or to nobody: a name that fits several people, or none, is
asked back rather than guessed. The assignment rules are the Tasks page's own (a Manager
assigns within their department, an Employee only to themselves), and the person named is
looked up within the directory the user may read.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.domains.executive.common import fold, local_date, local_today, tenant_zone
from app.domains.hr.hr_employee_tools import directory_scope
from app.domains.platform.notification_service import create_notification
from app.models.models import AuditLog, Task, TaskComment, User

TASKS_PAGE = "/tasks"
FINISHED = {"COMPLETED", "CANCELLED", "FAILED"}
STATUS_LABELS = {
    "DRAFT": "nháp",
    "PENDING": "chưa bắt đầu",
    "RUNNING": "đang làm",
    "WAITING_APPROVAL": "chờ duyệt",
    "COMPLETED": "đã xong",
    "FAILED": "không làm được",
    "CANCELLED": "đã huỷ",
    "OVERDUE": "quá hạn",
}
PRIORITY_LABELS = {"LOW": "thấp", "MEDIUM": "bình thường", "HIGH": "cao", "URGENT": "khẩn"}
# Forms of address a name is often written with; they say nothing about who it is.
_HONORIFICS = {"anh", "chi", "em", "ong", "ba", "co", "chu", "bac", "ban", "mr", "mrs", "ms", "sep"}
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")


def _describe(user: User) -> str:
    position = user.position.name if user.position is not None else None
    parts = [part for part in (position, f"phòng {user.department}" if user.department not in (None, "", "ALL") else None) if part]
    return f"{user.full_name} ({', '.join(parts)})" if parts else user.full_name


def find_people(db: Session, actor: User, named: str, *, email: str | None = None) -> list[User]:
    """Active colleagues the user may see in the directory whose name or email is ``named``."""
    scope, _ = directory_scope(db, actor)
    people = db.query(User).filter(
        User.tenant_id == actor.tenant_id,
        User.is_active.is_(True),
        User.id.in_(scope),
    ).all()
    address = (email or "").strip().lower() or (_EMAIL.search(named or "") or [None])[0]
    if address:
        return [person for person in people if person.email.lower() == str(address).lower()]
    words = [word for word in fold(named).split() if word not in _HONORIFICS]
    if not words:
        return []
    wanted = " ".join(words)
    exact = [person for person in people if fold(person.full_name) == wanted]
    if exact:
        return exact
    return [
        person for person in people
        if all(word in fold(person.full_name).split() for word in words)
    ]


def _assignment_refusal(actor: User, assignee: User) -> str | None:
    # The Tasks page's own rule (tasks._validate_relations), said in words.
    if actor.role == "Manager" and assignee.department != actor.department:
        return "Bạn chỉ giao được việc cho người cùng phòng ban."
    if actor.role in {"Employee", "Guest"} and assignee.id != actor.id:
        return "Tài khoản của bạn chỉ tự giao việc cho chính mình."
    return None


def assign_task(
    db: Session,
    actor: User,
    *,
    assignee: str,
    title: str,
    description: str | None = None,
    due_date: date | None = None,
    priority: str = "MEDIUM",
    email: str | None = None,
) -> dict[str, Any]:
    """Create the task for the one person named, or say why not. ``reply`` is the answer."""
    named = assignee.strip()
    people = find_people(db, actor, named, email=email)
    if not people:
        return {
            "created": False,
            "reply": (
                f"Mình không tìm thấy ai tên “{named}” trong danh bạ bạn được xem, nên chưa giao "
                "việc. Bạn cho mình họ tên đầy đủ hoặc email của người nhận nhé."
            ),
        }
    if len(people) > 1:
        options = "\n".join(f"- {_describe(person)}" for person in people[:8])
        return {
            "created": False,
            "reply": f"Có {len(people)} người khớp với “{named}”:\n{options}\nBạn muốn giao việc cho ai?",
        }
    person = people[0]
    refusal = _assignment_refusal(actor, person)
    if refusal:
        return {"created": False, "reply": f"Chưa giao việc cho {person.full_name}. {refusal}"}
    today = local_today(db, actor)
    if due_date is not None and due_date < today:
        return {
            "created": False,
            "reply": (
                f"Hạn {due_date.strftime('%d/%m/%Y')} đã qua (hôm nay là {today.strftime('%d/%m/%Y')}), "
                "nên mình chưa giao việc. Bạn cho mình hạn mới nhé."
            ),
        }
    due = (
        datetime.combine(due_date, time(23, 59), tzinfo=tenant_zone(db, actor)).astimezone(timezone.utc)
        if due_date else None
    )
    task = Task(
        tenant_id=actor.tenant_id,
        title=title.strip()[:255],
        description=(description or "").strip() or None,
        creator_id=actor.id,
        assignee_id=person.id,
        priority=priority,
        due_date=due,
        status="PENDING",
        attachments=[],
    )
    db.add(task)
    db.flush()
    changes = {"status": task.status, "assignee_id": str(person.id), "source": "CEO_ASSISTANT"}
    db.add(AuditLog(
        id=uuid.uuid4(),
        tenant_id=actor.tenant_id,
        actor_user_id=actor.id,
        actor_type="USER",
        agent_role="TASK",
        tool_name="task_created",
        action="task_created",
        resource_type="TASK",
        resource_id=str(task.id),
        input_parameters={"task_id": str(task.id), "actor_id": str(actor.id), "actor_name": actor.full_name},
        output_result=changes,
        after_data=changes,
        status="SUCCESS",
        execution_time_ms=0,
    ))
    deadline = f", hạn {due_date.strftime('%d/%m/%Y')}" if due_date else ""
    notified = False
    if person.id != actor.id:
        notified = create_notification(
            db,
            user=person,
            event_type="TASK_ASSIGNED",
            title="Bạn được giao việc mới",
            message=f"{actor.full_name} giao: {task.title}{deadline}.",
            entity_type="TASK",
            entity_id=str(task.id),
            dedup_key=f"task-assigned:{task.id}",
        ) is not None
    urgency = "" if priority == "MEDIUM" else f", ưu tiên {PRIORITY_LABELS.get(priority, priority.lower())}"
    reply = f"Đã giao việc “{task.title}” cho {_describe(person)}{deadline}{urgency}."
    if notified:
        reply += " Người nhận đã được thông báo."
    reply += f" Bạn theo dõi việc này ở trang Công việc ({TASKS_PAGE})."
    return {"created": True, "task_id": str(task.id), "reply": reply}


def _effective_status(task: Task, now: datetime) -> str:
    if task.due_date and task.due_date < now and task.status not in FINISHED:
        return "OVERDUE"
    return task.status


def list_tasks(
    db: Session,
    actor: User,
    *,
    scope: str = "ASSIGNED_BY_ME",
    status: str = "OPEN",
    limit: int = 15,
) -> dict[str, Any]:
    """The work the user handed out, or their own to-do, with each task's latest comment."""
    zone = tenant_zone(db, actor)
    today = local_today(db, actor)
    now = datetime.now(timezone.utc)
    query = db.query(Task).filter(Task.tenant_id == actor.tenant_id)
    if scope == "ASSIGNED_TO_ME":
        query = query.filter(Task.assignee_id == actor.id)
    else:
        query = query.filter(Task.creator_id == actor.id)
    tasks = query.order_by(Task.due_date.asc().nullslast(), Task.created_at.desc()).all()
    effective = [(task, _effective_status(task, now)) for task in tasks]
    counts = {
        "open": sum(1 for _, state in effective if state not in FINISHED),
        "overdue": sum(1 for _, state in effective if state == "OVERDUE"),
        "done": sum(1 for _, state in effective if state == "COMPLETED"),
    }
    if status == "OPEN":
        effective = [(task, state) for task, state in effective if state not in FINISHED]
    elif status == "OVERDUE":
        effective = [(task, state) for task, state in effective if state == "OVERDUE"]
    elif status == "DONE":
        effective = [(task, state) for task, state in effective if state == "COMPLETED"]
    items = []
    for task, state in effective[:limit]:
        comment = db.query(TaskComment).filter(TaskComment.task_id == task.id).order_by(
            TaskComment.created_at.desc()
        ).first()
        due = local_date(task.due_date, zone)
        items.append({
            "task_id": str(task.id),
            "title": task.title,
            "assignee": task.assignee.full_name if task.assignee else None,
            "assigned_by": task.creator.full_name if task.creator else None,
            "status": STATUS_LABELS.get(state, state.lower()),
            "priority": PRIORITY_LABELS.get(task.priority, str(task.priority).lower()),
            "due_date": due.isoformat() if due else None,
            "days_overdue": (today - due).days if due and state == "OVERDUE" else 0,
            "latest_comment": (
                {"by": comment.user.full_name if comment.user else None, "text": comment.content[:300]}
                if comment else None
            ),
        })
    return {
        "scope": scope,
        "status_filter": status,
        "counts": counts,
        "matching_count": len(effective),
        "items": items,
        "page": TASKS_PAGE,
        "source": "công việc giao cho bạn" if scope == "ASSIGNED_TO_ME" else "công việc bạn đã giao",
    }
