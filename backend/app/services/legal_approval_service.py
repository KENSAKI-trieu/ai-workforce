"""Escalation workflow for Legal Agent findings that need a human decision.

Lives in the service layer rather than in the API module because both entry points
raise the same escalation: uploads through /legal/review-document and contracts
pasted into chat. Chat used to skip this entirely, so a CRITICAL contract reviewed
in chat notified nobody while the identical file uploaded to the Legal page did.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.models import AgentWorkflow, User, WorkflowApproval


REASON_BY_ACTION = {
    "LEGAL_CONTRACT_APPROVAL": "Legal Agent detected high-risk contract terms.",
    "LEGAL_PRIVACY_APPROVAL": "Sensitive or restricted personal data was detected.",
    "LEGAL_LICENSE_APPROVAL": "A reciprocal open-source license requires commercial-use review.",
}


def create_legal_approval(
    db: Session,
    current_user: User,
    result: dict[str, Any],
    action_type: str = "LEGAL_CONTRACT_APPROVAL",
    *,
    contract_review_id: str | None = None,
) -> str | None:
    """Open an approval when the result asks for one; return the workflow id."""
    if not result.get(
        "requires_legal_approval",
        result.get("risk_level") in {"HIGH", "CRITICAL"},
    ):
        return None
    document_name = result.get("document_name") or result.get("manifest") or "Legal review"
    findings = result.get("risks") or result.get("findings") or []
    workflow = AgentWorkflow(
        tenant_id=current_user.tenant_id,
        initiator_id=current_user.id,
        title=f"Legal review: {document_name}",
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={
            "agent_role": "LEGAL",
            "steps": ["EMPLOYEE_SUBMISSION", "MANAGER_REVIEW", "LEGAL_APPROVAL"],
        },
    )
    db.add(workflow)
    db.flush()
    payload: dict[str, Any] = {
        "document_name": document_name,
        "risk_score": result.get("risk_score"),
        "findings": findings,
        "reason": REASON_BY_ACTION.get(action_type, "Legal review is required."),
        "requester_name": current_user.full_name,
        "data_sources": [document_name],
    }
    if contract_review_id:
        # Lets the approvals screen open the saved review behind the escalation.
        payload["contract_review_id"] = contract_review_id
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type=action_type,
        risk_level=result.get("risk_level", "HIGH"),
        payload=payload,
        status="WAITING",
    )
    db.add(approval)
    db.commit()
    return str(workflow.id)
