"""Legal tools."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.models.models import AgentWorkflow, ChatConversation, ChatMessage, WorkflowApproval
from app.agents.legal.perspective import read_represented_party
from app.agents.legal.replies import LEGAL_PERSPECTIVE_QUESTION
from app.agents.usage import _llm_usage_recorder
from app.domains.legal.chat_contract_review import review_reply, run_chat_contract_review
from app.domains.legal.contract_translation import ContractNotReviewable
from app.domains.legal.legal_document_generator import generate_legal_document
from app.domains.legal.legal_draft_storage import save_legal_artifact
from app.domains.legal.legal_documents.schemas import list_document_schemas
from app.tools.registry import ToolContext
from app.tools.schemas import ContractRiskReviewInput, GenerateLegalDocumentInput


def generate_legal_document_draft(
    context: ToolContext,
    request: GenerateLegalDocumentInput,
) -> dict[str, Any]:
    db = context.db
    actor = context.actor
    try:
        draft_content, filename, media_type = generate_legal_document(
            request.document_type, request.output_format, request.fields, approved=False
        )
        approved_content, _, _ = generate_legal_document(
            request.document_type, request.output_format, request.fields, approved=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    artifact_id = uuid.uuid4().hex
    draft_key = save_legal_artifact(
        tenant_id=actor.tenant_id,
        artifact_id=artifact_id,
        variant="draft",
        filename=filename,
        content=draft_content,
    )
    approved_key = save_legal_artifact(
        tenant_id=actor.tenant_id,
        artifact_id=artifact_id,
        variant="approved",
        filename=filename,
        content=approved_content,
    )
    template = next(
        (item for item in list_document_schemas() if item["id"] == request.document_type),
        None,
    )
    label = template["label"] if template else request.document_type
    workflow = AgentWorkflow(
        tenant_id=actor.tenant_id,
        initiator_id=actor.id,
        title=f"Legal document approval: {label}",
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={"agent_role": "LEGAL", "steps": ["DRAFT_CREATED", "EXECUTIVE_APPROVAL"]},
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        action_type="LEGAL_DOCUMENT_APPROVAL",
        risk_level="MEDIUM",
        payload={
            "artifact_id": artifact_id,
            "document_type": request.document_type,
            "document_type_label": label,
            "filename": filename,
            "media_type": media_type,
            "output_format": request.output_format,
            "draft_storage_key": draft_key,
            "approved_storage_key": approved_key,
            "requester_id": str(actor.id),
            "requester_name": actor.full_name,
            "reason": "AI-generated legal documents require human approval.",
            "data_sources": [label],
        },
        status="WAITING",
    )
    db.add(approval)
    db.flush()
    return {
        "artifact_id": artifact_id,
        "workflow_id": str(workflow.id),
        "approval_id": str(approval.id),
        "filename": filename,
        "status": "WAITING",
    }


# What the model is told about each finding. The full review, with evidence and suggested
# wording, is stored and shown to the user as a card; the model only needs enough to
# summarise it.
_FINDINGS_FOR_MODEL = 8


def _user_messages(context: ToolContext, request: ContractRiskReviewInput) -> list[str]:
    """The user's own messages in their own conversation, newest first."""
    conversation_id = request.audit.conversation_id
    if conversation_id is None:
        raise HTTPException(status_code=422, detail="A contract review needs a conversation")
    actor = context.actor
    # The conversation id arrives as trace metadata from the caller, so it only names a
    # conversation; ownership is checked here against the authenticated actor.
    conversation = context.db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.tenant_id == actor.tenant_id,
        ChatConversation.user_id == actor.id,
    ).first()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = context.db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.sender == "USER",
    ).order_by(ChatMessage.created_at.desc()).limit(request.from_user_message + 1).all()
    return [row.content or "" for row in rows]


def review_contract_risk(
    context: ToolContext,
    request: ContractRiskReviewInput,
) -> dict[str, Any]:
    messages = _user_messages(context, request)
    text = (
        messages[request.from_user_message].strip()
        if len(messages) > request.from_user_message
        else ""
    )
    if not text:
        return {
            "status": "NO_CONTRACT_TEXT",
            "reviewed": False,
            "reply": "Bạn hãy dán nội dung hợp đồng hoặc điều khoản cần rà soát.",
        }
    # The side is always the user's latest word on it: the current message, which is the
    # contract itself when both arrive together. It is read here rather than taken from
    # the model calling the tool, which filled in NEUTRAL for a user who had not said.
    party = read_represented_party(
        context.db,
        context.actor,
        messages[0],
        keyword_fallback=request.from_user_message != 0,
    )
    if party is None:
        # The same clause is high risk for whoever carries the obligation and low risk for
        # the other side, so a review without the user's side would score it for nobody.
        return {
            "status": "NEEDS_REPRESENTED_PARTY",
            "reviewed": False,
            "reply": LEGAL_PERSPECTIVE_QUESTION,
        }
    try:
        # With no scope from the model, it is read from the text the analyzer sees -- the
        # translation, for a contract that needed one.
        result, review = run_chat_contract_review(
            context.db,
            context.actor,
            text,
            represented_party=party,
            document_scope=request.document_scope,
            on_usage=_llm_usage_recorder(context.db, context.actor, "LEGAL"),
        )
    except ContractNotReviewable as exc:
        return {"status": "NOT_REVIEWABLE", "reviewed": False, "reply": exc.reply}
    return {
        "status": "REVIEWED",
        "reviewed": True,
        # The user-facing answer, in the same words as the deterministic chat. The graph
        # ends the turn on it (the tool is terminal), so no model has to summarise the
        # review -- left to, the model re-called the tool and opened extra approvals.
        "reply": review_reply(result),
        "review_id": str(review.id),
        "represented_party": result["represented_party_label"],
        "document_scope": result["document_scope"],
        "reviewed_characters": len(text),
        "risk_score": result["risk_score"],
        "risk_level": result["risk_level"],
        "total_findings": result["total_risks_found"],
        "missing_clauses": result["missing_clauses_count"],
        "approval_created": bool(result.get("approval_created")),
        "findings": [
            {
                "severity": finding["severity"],
                "category": finding["category"],
                "clause": finding["clause"],
                "issue": finding["issue"],
                "recommendation": finding.get("recommendation"),
            }
            for finding in result["findings"][:_FINDINGS_FOR_MODEL]
        ],
    }
