"""Approvals for what the Finance agent drafts: who signs how much, and what signing does.

A draft (journal entry, payment voucher, reminder) waits as a WorkflowApproval whose
`action_type` starts with FINANCE_. The server writes the amount and the permission the
amount requires into the payload when it opens the approval; nothing the model or the
requester sends can set them, and the requester can never sign their own draft.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.permissions import PERMISSIONS
from app.domains.finance.money import plain
from app.domains.finance.settings import get_settings, permissions_that_sign, required_permission
from app.models.models import AgentWorkflow, User, WorkflowApproval

FINANCE_PREFIX = "FINANCE_"
JOURNAL_APPROVAL = "FINANCE_JOURNAL_APPROVAL"
PAYMENT_VOUCHER = "FINANCE_PAYMENT_VOUCHER"
REMINDER_SEND = "FINANCE_REMINDER_SEND"
# Payload keys only the server writes. The generic approval tool refuses them too.
SERVER_KEYS = frozenset({"required_permission", "amount", "finance_record_id"})

_LABELS = {item.code: item.label for item in PERMISSIONS}


def is_finance_approval(approval: WorkflowApproval) -> bool:
    return str(approval.action_type or "").startswith(FINANCE_PREFIX)


def open_finance_approval(
    db: Session,
    actor: User,
    *,
    action_type: str,
    title: str,
    amount: Decimal,
    record_id: str,
    payload: dict[str, Any],
    fixed_permission: str | None = None,
) -> WorkflowApproval:
    """A WAITING approval for a finance draft, signed by whoever the amount requires.

    `fixed_permission` is for drafts whose risk is not their amount (a reminder email):
    they go to the first signing level whatever the figure in them.
    """
    settings = get_settings(db, actor.tenant_id)
    if fixed_permission:
        permission, risk = fixed_permission, "MEDIUM"
    else:
        permission, risk = required_permission(settings, amount)
    workflow = AgentWorkflow(
        tenant_id=actor.tenant_id,
        initiator_id=actor.id,
        title=title[:255],
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={"agent_role": "FINANCE", "steps": ["DRAFTED", "HUMAN_APPROVAL"]},
    )
    db.add(workflow)
    db.flush()
    body = {key: value for key, value in payload.items() if key not in SERVER_KEYS}
    body.update({
        "requester_id": str(actor.id),
        "requester_name": actor.full_name,
        "required_permission": permission,
        "required_permission_label": _LABELS.get(permission, permission),
        "amount": plain(amount),
        "finance_record_id": record_id,
    })
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type=action_type,
        risk_level=risk,
        payload=body,
        status="WAITING",
    )
    db.add(approval)
    db.flush()
    return approval


def can_approve_finance(db: Session | None, user: User, approval: WorkflowApproval) -> bool:
    from app.domains.platform.position_service import user_permissions

    payload = approval.payload or {}
    if str(payload.get("requester_id") or "") == str(user.id):
        return False
    if approval.workflow is not None and approval.workflow.initiator_id == user.id:
        return False
    required = str(payload.get("required_permission") or "approvals.sign_critical")
    return bool(user_permissions(db, user) & permissions_that_sign(required))


def no_finance_approver_warning(approval: WorkflowApproval) -> str:
    from app.domains.platform.approval_access import NO_APPROVER_WARNING

    payload = approval.payload or {}
    permission = str(payload.get("required_permission") or "approvals.sign_critical")
    return NO_APPROVER_WARNING.format(permission=_LABELS.get(permission, permission))


# action_type -> (validate an approver's edit, apply the decision). Registered by the
# modules that own each kind of draft, so this module never imports them.
EditValidator = Callable[[Session, WorkflowApproval, dict[str, Any]], dict[str, Any]]
Finalizer = Callable[[Session, WorkflowApproval, User, bool], None]
_HANDLERS: dict[str, tuple[EditValidator | None, Finalizer]] = {}


def register_handler(action_type: str, finalize: Finalizer, edit: EditValidator | None = None) -> None:
    _HANDLERS[action_type] = (edit, finalize)


def _handlers(action_type: str) -> tuple[EditValidator | None, Finalizer]:
    # The handler modules register on import; importing them here keeps that one place.
    from app.domains.finance import handlers  # noqa: F401

    handler = _HANDLERS.get(action_type)
    if handler is None:
        raise HTTPException(status_code=422, detail=f"Unknown finance approval: {action_type}")
    return handler


def apply_finance_edit(db: Session, approval: WorkflowApproval, edited: dict[str, Any]) -> dict[str, Any]:
    """The payload after an approver's edit: only what the handler accepts changes."""
    edit, _ = _handlers(approval.action_type)
    if edit is None:
        raise HTTPException(status_code=422, detail="This finance draft is approved or rejected as it is")
    return edit(db, approval, edited)


def finalize_finance_approval(db: Session, approval: WorkflowApproval, approver: User, approved: bool) -> None:
    _, finalize = _handlers(approval.action_type)
    finalize(db, approval, approver, approved)


# Who may take back someone else's draft: the top signing level, which could have
# rejected it anyway. Anyone with a lower level just rejects what they may sign.
WITHDRAW_ANY = "finance.journal.approve_high"


def can_withdraw_finance(db: Session | None, user: User, approval: WorkflowApproval) -> bool:
    from app.domains.platform.position_service import user_permissions

    payload = approval.payload or {}
    if str(payload.get("requester_id") or "") == str(user.id):
        return True
    if approval.workflow is not None and approval.workflow.initiator_id == user.id:
        return True
    return WITHDRAW_ANY in user_permissions(db, user)


def withdraw_finance_approval(
    db: Session, approval: WorkflowApproval, actor: User, reason: str | None = None
) -> None:
    """Take back a draft nobody has decided, undoing what it holds.

    A waiting draft locks what it is about -- an invoice in a voucher cannot go in another,
    a customer with a reminder waiting gets no second one -- and finance approvals do not
    expire. Without this, a draft no one is entitled to sign locked them for good.
    Withdrawing undoes it as a rejection would: the voucher's payments are cancelled, the
    journal entry is set aside, the letter is never sent.
    """
    if approval.status != "WAITING":
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}")
    if not can_withdraw_finance(db, actor, approval):
        raise HTTPException(status_code=403, detail="Only the requester or a top-level finance approver can withdraw this draft")
    finalize_finance_approval(db, approval, actor, False)
    approval.status = "WITHDRAWN"
    approval.comments = (reason or "").strip()[:1000] or None
    if approval.workflow is not None:
        approval.workflow.status = "CANCELLED"
        approval.workflow.completed_at = datetime.now(timezone.utc)
    db.flush()
