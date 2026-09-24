"""Server-side implementations for governed tools.

These functions receive a `ToolContext` holding an authenticated database user and, when the
caller is an AI Employee, that agent's row. They never accept role, department, or actor
identity from tool input.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.models.models import (
    AIAgent,
    AgentWorkflow,
    ChatConversation,
    ChatMessage,
    Task,
    User,
    WorkflowApproval,
)
from app.plugins.resolver import resolve_prompt_overlay
from app.agents.legal.intent import _parse_represented_party
from app.agents.legal.replies import LEGAL_PERSPECTIVE_QUESTION
from app.agents.usage import _llm_usage_recorder
from app.agents.legal.llm_flow import extract_represented_party
from app.domains.legal.chat_contract_review import (
    document_scope_from_structure,
    review_reply,
    run_chat_contract_review,
)
from app.domains.platform.audit_service import (
    get_cost_by_agent,
    get_cost_by_department,
    get_cost_by_employee,
    get_cost_by_workflow,
    get_llm_cost_summary,
)
from app.domains.hr.hr_employee_tools import get_employee_sections
from app.domains.hr.hr_service import query_leave_balance
from app.agents.langgraph.approvals import GRAPH_APPROVER_ROLES
from app.domains.legal.legal_document_generator import generate_legal_document
from app.domains.legal.legal_draft_storage import save_legal_artifact
from app.domains.legal.legal_documents.schemas import list_document_schemas
from app.domains.knowledge.rag_service import hybrid_search_documents
from app.tools.registry import ToolContext
from app.tools.schemas import (
    ContractRiskReviewInput,
    CreateTaskInput,
    EmployeeLookupInput,
    ExpenseLookupInput,
    GenerateLegalDocumentInput,
    LeaveLookupInput,
    RAGSearchInput,
    SubmitApprovalInput,
)

# Keys in a caller-supplied approval payload that the approval routes read as control data.
# `kind` selects which branch of `_can_approve` applies and whether the approve route tries
# to resume a LangGraph thread, so accepting it from a tool argument would let a requester
# choose the rule that governs their own request.
RESERVED_APPROVAL_PAYLOAD_KEYS = frozenset({"kind", "requester_id", "requester_name"})


def search_rag(context: ToolContext, request: RAGSearchInput) -> list[dict[str, Any]]:
    actor = context.actor
    agent = context.agent
    return hybrid_search_documents(
        db=context.db,
        tenant_id=actor.tenant_id,
        query_text=request.query,
        department="*" if actor.role in {"Owner", "Admin", "CEO"} else actor.department,
        top_k=request.top_k,
        collections=request.collections,
        # The AI Employee's configured knowledge scope. Omitting it here let a governed
        # agent read every document its user could reach, ignoring the scope an operator
        # had set for it -- the deterministic executors have always passed this.
        agent_access=(agent.knowledge_access or None) if agent else None,
        user_role=actor.role,
        user_department=actor.department,
    )


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


def _validate_approver(context: ToolContext, request: SubmitApprovalInput) -> User | None:
    """Reject approvers who would turn the gate into a formality."""
    if not request.approver_id:
        return None
    actor = context.actor
    if request.approver_id == actor.id:
        # `_can_approve` lets the named approver act on their own request, so a requester
        # naming themselves approves their own external action.
        raise HTTPException(
            status_code=422,
            detail="The requester cannot be the approver of their own request",
        )
    approver = context.db.query(User).filter(
        User.id == request.approver_id,
        User.tenant_id == actor.tenant_id,
        User.is_active.is_(True),
    ).first()
    if not approver:
        raise HTTPException(status_code=422, detail="Approver is unavailable")
    if approver.role not in GRAPH_APPROVER_ROLES:
        raise HTTPException(
            status_code=422,
            detail="Approver must hold an approver role (Owner, Admin, CEO or Manager)",
        )
    return approver


def submit_approval_request(
    context: ToolContext,
    request: SubmitApprovalInput,
) -> dict[str, Any]:
    db = context.db
    actor = context.actor
    reserved = RESERVED_APPROVAL_PAYLOAD_KEYS & set(request.payload)
    if reserved:
        raise HTTPException(
            status_code=422,
            detail=f"Approval payload cannot set reserved keys: {', '.join(sorted(reserved))}",
        )
    _validate_approver(context, request)
    workflow = AgentWorkflow(
        tenant_id=actor.tenant_id,
        initiator_id=actor.id,
        title=request.title.strip(),
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={"agent_role": "SYSTEM", "steps": ["REQUESTED", "HUMAN_APPROVAL"]},
    )
    db.add(workflow)
    db.flush()
    payload = dict(request.payload)
    payload.update({"requester_id": str(actor.id), "requester_name": actor.full_name})
    approval = WorkflowApproval(
        workflow_id=workflow.id,
        approver_id=request.approver_id,
        action_type=request.action_type,
        risk_level=request.risk_level,
        payload=payload,
        status="WAITING",
        expires_at=request.expires_at,
    )
    db.add(approval)
    db.flush()
    return {
        "workflow_id": str(workflow.id),
        "approval_id": str(approval.id),
        "status": approval.status,
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


def _represented_party(context: ToolContext, message: str, *, holds_contract: bool) -> str | None:
    """The side the user said they act for, read from their message by the Legal reader.

    Read here rather than taken from the model, which filled in NEUTRAL for a user who had
    not said. When the same message is the contract, only the model reading counts: the
    keyword fallback would take "Bên A" in the contract's own text as the user's answer.
    """
    actor = context.actor
    perspective = extract_represented_party(
        message,
        fallback_party=None if holds_contract else _parse_represented_party(message),
        fallback_cancel=False,
        on_usage=_llm_usage_recorder(context.db, actor, "LEGAL"),
        prompts=resolve_prompt_overlay(context.db, actor.tenant_id, "LEGAL"),
    )
    return perspective.represented_party if perspective.decision == "ANSWER" else None


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
    # contract itself when both arrive together.
    party = _represented_party(
        context, messages[0], holds_contract=request.from_user_message == 0
    )
    if party is None:
        # The same clause is high risk for whoever carries the obligation and low risk for
        # the other side, so a review without the user's side would score it for nobody.
        return {
            "status": "NEEDS_REPRESENTED_PARTY",
            "reviewed": False,
            "reply": LEGAL_PERSPECTIVE_QUESTION,
        }
    scope = request.document_scope or document_scope_from_structure(text)
    result, review = run_chat_contract_review(
        context.db,
        context.actor,
        text,
        represented_party=party,
        document_scope=scope,
    )
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
