"""Who may decide an approval, and whether anyone can.

Lives outside the approvals router so the pages that open approvals can ask the same
question: a Legal review escalated to CRITICAL by the only person holding the critical
signing permission used to sit WAITING with no one able to see it, and its requester was
told it had been sent.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.langgraph.approvals import GRAPH_APPROVAL_KIND
from app.domains.hr.hr_service import can_approve_hr_request
from app.domains.legal.legal_document_submission import can_approve_legal_documents
from app.domains.platform.position_service import can_sign_approvals, can_sign_critical
from app.models.models import User, WorkflowApproval


def can_approve(db: Session, current_user: User, approval: WorkflowApproval) -> bool:
    """Who may decide an approval, from the permissions ticked on their position.

    "Phê duyệt yêu cầu" (approvals.sign) signs within the signer's own management scope
    and below CRITICAL; "... tối quan trọng" (approvals.sign_critical) signs anything. They
    replace the Manager and Owner/Admin/CEO role sets, which no box could change.
    """
    payload = approval.payload or {}
    critical_signer = can_sign_critical(db, current_user)
    signer = critical_signer or can_sign_approvals(db, current_user)
    if payload.get("kind") == GRAPH_APPROVAL_KIND:
        if critical_signer:
            return True
        if not signer or approval.risk_level == "CRITICAL":
            return False
        initiator = approval.workflow.initiator
        if initiator.id == current_user.id:
            return False
        return (
            initiator.manager_id == current_user.id
            or initiator.department == current_user.department
        )
    if approval.action_type == "LEAVE_REQUEST":
        # The HR scope says whose leave; the approval permission says whether to sign at all.
        if approval.approver_id != current_user.id and not signer:
            return False
        return can_approve_hr_request(db, current_user, approval)
    if approval.action_type == "LEGAL_DOCUMENT_APPROVAL":
        return can_approve_legal_documents(current_user)
    if str(payload.get("requester_id") or "") == str(current_user.id):
        # Blocking a requester who named themselves approver was only half of it: with
        # `approver_id` left empty this branch fell through to `return True` for anyone
        # holding an approver role, so a Manager could open a gate and walk through it.
        # `requester_id` is written by the server, never by the caller, so it is the one
        # field that reliably says whose request this is. The graph branch above already
        # refuses the initiator this way.
        return False
    if not signer:
        return approval.approver_id == current_user.id
    if approval.approver_id and approval.approver_id != current_user.id:
        return critical_signer
    if approval.risk_level == "CRITICAL":
        # What the permission says it is for; a Manager used to sign these here while the
        # graph branch above already refused them.
        return critical_signer or approval.approver_id == current_user.id
    return True


def eligible_approvers(db: Session, approval: WorkflowApproval) -> list[User]:
    """Active users of the approval's tenant who could decide it right now."""
    users = db.query(User).filter(
        User.tenant_id == approval.workflow.tenant_id,
        User.is_active.is_(True),
    ).all()
    return [user for user in users if can_approve(db, user, approval)]


NO_APPROVER_WARNING = (
    "Chưa có ai đủ quyền duyệt yêu cầu này: người gửi không được tự duyệt, và không có "
    "chức vụ nào khác được tick quyền “{permission}”. Admin cần cấp quyền đó cho một chức "
    "vụ khác thì yêu cầu mới hiện ở Trung tâm phê duyệt của người duyệt."
)


def no_approver_warning(approval: WorkflowApproval) -> str:
    permission = (
        "Phê duyệt yêu cầu tối quan trọng" if approval.risk_level == "CRITICAL" else "Phê duyệt yêu cầu"
    )
    return NO_APPROVER_WARNING.format(permission=permission)
