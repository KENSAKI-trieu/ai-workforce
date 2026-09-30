"""Generate a legal document and put it in front of an approver.

One path for both ways in: the Legal page's drafting form and the Legal agent's
`generate_legal_document` tool. The tool used to carry its own copy, which drifted -- no
approver was notified, and the approval had no preview link -- so a draft requested in chat
sat unseen.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import AgentWorkflow, User, WorkflowApproval
from app.domains.legal.legal_document_generator import generate_legal_document
from app.domains.legal.legal_documents import DOCUMENT_SCHEMAS
from app.domains.legal.legal_draft_storage import save_legal_artifact
from app.domains.platform.notification_service import create_notification
from app.domains.platform.position_service import has_permission, users_with_permission

# Ticked in org-structure as "Duyệt văn bản pháp lý"; it replaced the Owner/Admin/CEO
# role strings, which no box on a position could grant or take away.
LEGAL_DOCUMENT_APPROVE_PERMISSION = "legal.document.approve"
LEGAL_DOCUMENT_GENERATE_PERMISSION = "legal.document.generate"


def can_approve_legal_documents(user: User) -> bool:
    return has_permission(None, user, LEGAL_DOCUMENT_APPROVE_PERMISSION)


def document_type_label(document_type: str) -> str:
    schema = DOCUMENT_SCHEMAS.get(document_type.upper())
    return schema["label"] if schema else document_type


def submit_legal_document(
    db: Session,
    user: User,
    *,
    document_type: str,
    output_format: str,
    fields: dict[str, Any],
) -> WorkflowApproval:
    """Store the draft and approved variants, open the approval and notify approvers.

    Raises ValueError, before anything is stored, for an unknown template, a missing
    required field or an unsupported format. Flushes only; the caller commits.
    """
    normalized_type = document_type.upper()
    normalized_format = output_format.lower()
    draft_content, filename, media_type = generate_legal_document(
        normalized_type, normalized_format, fields, approved=False
    )
    approved_content, _, _ = generate_legal_document(
        normalized_type, normalized_format, fields, approved=True
    )
    artifact_id = uuid.uuid4().hex
    draft_key = save_legal_artifact(
        tenant_id=user.tenant_id,
        artifact_id=artifact_id,
        variant="draft",
        filename=filename,
        content=draft_content,
    )
    approved_key = save_legal_artifact(
        tenant_id=user.tenant_id,
        artifact_id=artifact_id,
        variant="approved",
        filename=filename,
        content=approved_content,
    )
    label = document_type_label(normalized_type)
    workflow = AgentWorkflow(
        tenant_id=user.tenant_id,
        initiator_id=user.id,
        title=f"Phê duyệt văn bản: {label}",
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={
            "agent_role": "LEGAL",
            "steps": ["DRAFT_CREATED", "EXECUTIVE_APPROVAL", "CREATOR_DOWNLOAD"],
        },
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type="LEGAL_DOCUMENT_APPROVAL",
        risk_level="MEDIUM",
        payload={
            "artifact_id": artifact_id,
            "document_type": normalized_type,
            "document_type_label": label,
            "filename": filename,
            "media_type": media_type,
            "output_format": normalized_format,
            "draft_storage_key": draft_key,
            "approved_storage_key": approved_key,
            "requester_id": str(user.id),
            "requester_name": user.full_name,
            "reason": "Văn bản do Legal Agent tạo cần CEO, Admin hoặc Owner phê duyệt trước khi người tạo tải xuống.",
            "data_sources": [label],
            "preview_url": f"/api/v1/legal/document-drafts/{artifact_id}/preview",
            "review_download_url": f"/api/v1/legal/document-drafts/{artifact_id}/download?variant=draft",
        },
        status="WAITING",
    )
    db.add(approval)
    db.flush()
    for approver in users_with_permission(db, user.tenant_id, LEGAL_DOCUMENT_APPROVE_PERMISSION):
        create_notification(
            db,
            user=approver,
            event_type="APPROVAL_REQUIRED",
            title="Văn bản pháp lý chờ phê duyệt",
            message=f"{user.full_name} đã gửi {label}.",
            severity="WARNING",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"legal-document-approval:{approval.id}:{approver.id}",
        )
    return approval
