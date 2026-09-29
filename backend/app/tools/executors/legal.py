"""Legal tools."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.models import ChatConversation, ChatMessage
from app.agents.legal.perspective import read_represented_party
from app.agents.legal.replies import LEGAL_PERSPECTIVE_QUESTION
from app.agents.legal.review import _contract_fingerprint
from app.agents.usage import _llm_usage_recorder
from app.domains.legal.chat_contract_review import (
    document_scope_from_structure,
    review_reply,
    run_chat_contract_review,
)
from app.domains.legal.contract_translation import ContractNotReviewable
from app.domains.legal.legal_document_submission import submit_legal_document
from app.domains.legal.legal_documents import DOCUMENT_SCHEMAS, validate_document_fields
from app.tools.registry import ToolContext
from app.tools.schemas import ContractRiskReviewInput, GenerateLegalDocumentInput


# How much of each value the confirmation repeats back. Enough to spot a wrong party or
# date; the approver reads the whole draft.
_FIELD_ECHO_CHARS = 120


def _template_list() -> str:
    return "\n".join(
        f"- **{schema['label']}** (`{document_type}`)"
        for document_type, schema in DOCUMENT_SCHEMAS.items()
    )


def _echo(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _FIELD_ECHO_CHARS else text[: _FIELD_ECHO_CHARS - 1].rstrip() + "…"


def generate_legal_document_draft(
    context: ToolContext,
    request: GenerateLegalDocumentInput,
) -> dict[str, Any]:
    """Draft a document from a template and send it for approval -- or say what is missing.

    Terminal and approval-opening: the result's `reply` is the answer to the user, and the
    only approval is the one the draft itself raises. The graph used to stop for an approval
    of the tool call first, so a draft needed approving twice, and the fields were checked
    only after that first approval -- where a model guessing field names failed every time.
    Nothing is stored or escalated until every required field is present.
    """
    schema = DOCUMENT_SCHEMAS.get(request.document_type)
    if schema is None:
        return {
            "status": "UNKNOWN_DOCUMENT_TYPE",
            "created": False,
            "reply": (
                "Tôi chỉ soạn được văn bản theo các mẫu có sẵn sau:\n\n"
                f"{_template_list()}\n\nBạn cần soạn loại nào?"
            ),
        }
    label = schema["label"]
    validation = validate_document_fields(request.document_type, request.fields)
    if not validation["valid"]:
        missing = validation["missing_fields"]
        return {
            "status": "NEEDS_FIELDS",
            "created": False,
            "document_type": request.document_type,
            "missing_fields": [item["name"] for item in missing],
            "reply": (
                f"Để soạn **{label}**, tôi còn thiếu các thông tin sau:\n\n"
                + "\n".join(f"- {item['label']}" for item in missing)
                + "\n\nBạn cung cấp giúp tôi, rồi tôi sẽ tạo bản nháp và gửi đi phê duyệt."
            ),
        }
    try:
        approval = submit_legal_document(
            context.db,
            context.actor,
            document_type=request.document_type,
            output_format=request.output_format,
            fields=request.fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = approval.payload or {}
    used = "\n".join(
        f"- {field['label']}: {_echo(request.fields[field['name']])}"
        for field in schema["fields"]
        if request.fields.get(field["name"]) not in (None, "")
    )
    reply = (
        f"Tôi đã tạo bản nháp **{label}** (`{payload.get('filename')}`) và gửi đi phê duyệt. "
        "Văn bản do AI tạo cần Owner, Admin hoặc CEO duyệt; sau khi được duyệt, bạn tải bản "
        "chính thức ở trang Legal Agent, mục văn bản của tôi.\n\n"
        f"Thông tin đã dùng:\n{used}"
    )
    warnings = [warning for warning in validation["warnings"] if warning.get("title")]
    if warnings:
        reply += "\n\nĐiểm cần lưu ý:\n" + "\n".join(
            f"- {warning['title']}: {warning['recommendation']}" for warning in warnings
        )
    return {
        "status": "SUBMITTED",
        "created": True,
        "artifact_id": payload.get("artifact_id"),
        "workflow_id": str(approval.workflow_id),
        "approval_id": str(approval.id),
        "filename": payload.get("filename"),
        "reply": reply,
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
        # The contract's fingerprint lets the backend leave the same open question the
        # deterministic chat leaves; without it, a next turn that fell back to that chat
        # read "bên B" as a legal question and the contract was never reviewed.
        return {
            "status": "NEEDS_REPRESENTED_PARTY",
            "reviewed": False,
            "reply": LEGAL_PERSPECTIVE_QUESTION,
            "contract_fingerprint": _contract_fingerprint(text),
            "contract_char_count": len(text),
            "contract_excerpt": text[:200],
            "document_scope": request.document_scope or document_scope_from_structure(text),
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
