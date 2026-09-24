"""One Legal turn: review a contract, ask for the user's side, or answer from documents."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Any

from sqlalchemy.orm import Session
from app.models.models import AIAgent, User
from app.domains.knowledge.rag_service import hybrid_search_documents
from app.domains.platform.audit_service import log_audit_action
from app.plugins.resolver import resolve_prompt_overlay
from app.domains.legal.chat_contract_review import review_reply, run_chat_contract_review
from app.agents.legal.llm_flow import (
    LegalIntentClassification,
    LegalPerspective,
    answer_from_legal_evidence,
    classify_legal_request,
    extract_represented_party,
)
from app.agents.access import _can_use_tool
from app.agents.legal.intent import (
    _classify_legal_contract_intent,
    _is_legal_review_cancel,
    _legal_pending_fallback,
    _parse_represented_party,
)
from app.agents.legal.replies import (
    LEGAL_APPROVAL_REMINDER,
    LEGAL_NOT_FOUND_REPLY,
    LEGAL_NOT_REVIEWED_NOTICE,
    LEGAL_NO_TOOLS_REPLY,
    LEGAL_PERSPECTIVE_QUESTION,
    LEGAL_REVIEW_TOOL_OFF_REPLY,
    LEGAL_SEARCH_TOOL_OFF_REPLY,
)
from app.agents.legal.review import (
    _contract_fingerprint,
    _legal_document_scope,
    _legal_review_card,
    _load_legal_review_draft,
    _log_legal_overrule,
    _log_legal_routing,
)
from app.agents.text import _normalize_intent_text
from app.agents.usage import _llm_usage_recorder


def run_legal_turn(
    *,
    db: Session,
    user: User,
    agent: AIAgent,
    role_code_upper: str,
    message: str,
    thread_id: str | None,
    response_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Review a contract, collect the user's side, or answer from Legal documents."""
    can_review = _can_use_tool(agent, "audit_contract_risk")
    can_search = _can_use_tool(agent, "rag_search")
    if not can_review and not can_search:
        response_data["reply"] = LEGAL_NO_TOOLS_REPLY
        return response_data
    normalized_legal_intent = _normalize_intent_text(message)
    legal_prompt_overlay = resolve_prompt_overlay(db, user.tenant_id, role_code_upper)
    record_legal_usage = _llm_usage_recorder(db, user, "LEGAL")

    pending_review = _load_legal_review_draft(db, user, thread_id)
    if pending_review and not can_review:
        # The review tool was revoked while a review was waiting on the user. Close
        # it rather than leave it to capture a later message once the tool is back;
        # this turn is then read on its own terms.
        draft, _ = pending_review
        response_data["legal_risk_card"] = _legal_review_card(
            status="DISMISSED",
            contract_fingerprint=str(draft.get("contract_fingerprint") or ""),
            contract_char_count=int(draft.get("contract_char_count") or 0),
        )
        pending_review = None
    contract_text: str | None = None
    represented_party: str | None = None
    # The scorer still runs on every turn: its signals are reported on the UNSURE
    # card, its clause structure stops a pasted contract from being read as a bare
    # "yes" and scopes a review no model judged, and it is what the router falls
    # back to.
    keyword_intent, detection_signals = _classify_legal_contract_intent(message)
    # Set when the pending-question branch has already routed this turn, so the
    # same message is not billed to the router a second time.
    routed: LegalIntentClassification | None = None
    document_scope: str | None = None

    if pending_review:
        draft, pending_text = pending_review
        document_scope = draft.get("document_scope")
        pending_status = str(draft.get("status") or "")
        perspective: LegalPerspective | None = None
        if pending_status == "COLLECTING":
            # One call answers both questions this state can face: did they name a
            # side, or are they calling the review off? The cancel keywords are only
            # its fallback: matched ahead of the model, "bỏ qua" in "tôi là bên A, bỏ
            # qua phần bảo mật" called off a review the user had just answered.
            perspective = extract_represented_party(
                message,
                fallback_party=_parse_represented_party(message),
                fallback_cancel=_is_legal_review_cancel(normalized_legal_intent),
                on_usage=record_legal_usage,
                prompts=legal_prompt_overlay,
            )
        if perspective is not None and perspective.decision == "CANCEL":
            response_data["reply"] = (
                "Tôi đã hủy yêu cầu rà soát. Chưa có nội dung nào được phân tích hay lưu lại."
            )
            response_data["legal_risk_card"] = _legal_review_card(
                status="CANCELLED",
                contract_fingerprint=str(draft.get("contract_fingerprint") or ""),
                contract_char_count=int(draft.get("contract_char_count") or 0),
            )
            return response_data
        if pending_status == "AWAITING_INTENT":
            pending_classification = classify_legal_request(
                message,
                pending_state="AWAITING_INTENT",
                fallback_intent=_legal_pending_fallback(
                    message, keyword_intent, detection_signals
                ),
                on_usage=record_legal_usage,
                prompts=legal_prompt_overlay,
            )
            _log_legal_routing(
                pending_state="AWAITING_INTENT",
                classification=pending_classification,
                keyword_intent=keyword_intent,
            )
            if (
                pending_classification.intent == "CONFIRM_REVIEW"
                and detection_signals.get("clause_structure")
            ):
                # "Here is the full version, review it: <contract>" reads as a yes,
                # but a yes reviews the text the question was asked about. The
                # document in this message is the one the user wants examined.
                pending_classification = replace(pending_classification, intent="REVIEW")
                _log_legal_overrule("CONFIRM_REVIEW", "REVIEW", "clause_structure")
            pending_intent = pending_classification.intent
            if pending_intent == "CONFIRM_REVIEW":
                response_data["reply"] = LEGAL_PERSPECTIVE_QUESTION
                response_data["legal_risk_card"] = _legal_review_card(
                    status="COLLECTING",
                    contract_fingerprint=_contract_fingerprint(pending_text),
                    contract_char_count=len(pending_text),
                    excerpt=pending_text,
                    document_scope=document_scope,
                )
                return response_data
            # Not a yes: close the open question here rather than leaving it for a
            # later message to answer by accident. A REVIEW or UNSURE card produced
            # below simply replaces this one, since only the newest card counts.
            response_data["legal_risk_card"] = _legal_review_card(
                status="DISMISSED",
                contract_fingerprint=str(draft.get("contract_fingerprint") or ""),
                contract_char_count=int(draft.get("contract_char_count") or 0),
            )
            if pending_intent == "DECLINE_REVIEW":
                # A bare "no" is an answer, not a query: searching the knowledge
                # base for "không cần đâu" can only report that nothing matched.
                response_data["reply"] = (
                    "Được, tôi sẽ không rà soát nội dung đó. Bạn cứ đặt câu hỏi pháp "
                    "lý, hoặc gửi hợp đồng khác khi cần rà soát."
                )
                return response_data
            routed = pending_classification
        else:
            # perspective is always set here: COLLECTING is the only other state a
            # loaded draft can be in, and that branch above computed it.
            represented_party = (
                perspective.represented_party if perspective else None
            )
            if represented_party is None:
                response_data["reply"] = (
                    "Tôi chưa xác định được bạn đại diện bên nào.\n\n"
                    + LEGAL_PERSPECTIVE_QUESTION
                )
                response_data["legal_risk_card"] = _legal_review_card(
                    status="COLLECTING",
                    contract_fingerprint=_contract_fingerprint(pending_text),
                    contract_char_count=len(pending_text),
                    excerpt=pending_text,
                    document_scope=document_scope,
                )
                return response_data
            contract_text = pending_text

    if contract_text is None:
        classification = routed
        if classification is None:
            classification = classify_legal_request(
                message,
                pending_state="NONE",
                fallback_intent=keyword_intent,
                on_usage=record_legal_usage,
                prompts=legal_prompt_overlay,
            )
            _log_legal_routing(
                pending_state="NONE",
                classification=classification,
                keyword_intent=keyword_intent,
            )
        intent = classification.intent
        if not can_review and intent in {"REVIEW", "UNSURE"}:
            if intent == "REVIEW":
                response_data["reply"] = LEGAL_REVIEW_TOOL_OFF_REPLY
                return response_data
            # Asking "review it, or were you asking?" offers a review that cannot
            # run; the one thing left to do with the turn is answer it.
            intent = "QUESTION"
        if intent == "REVIEW":
            response_data["reply"] = LEGAL_PERSPECTIVE_QUESTION
            response_data["legal_risk_card"] = _legal_review_card(
                status="COLLECTING",
                contract_fingerprint=_contract_fingerprint(message),
                contract_char_count=len(message),
                excerpt=message,
                document_scope=_legal_document_scope(classification, detection_signals),
            )
            return response_data
        if intent == "UNSURE":
            # Never fall silently through to retrieval here: the user would be
            # told no document matched and conclude their contract was reviewed.
            response_data["reply"] = (
                "Tôi chưa chắc bạn muốn tôi **rà soát rủi ro một hợp đồng** hay "
                "**trả lời một câu hỏi pháp lý**. Nội dung bạn gửi có "
                f"{detection_signals.get('numbered_clauses', 0)} điều khoản nhận diện được.\n\n"
                "- Trả lời **\"rà soát\"** để tôi phân tích rủi ro nội dung này.\n"
                "- Hoặc đặt lại câu hỏi để tôi tra cứu trong Kho tri thức."
            )
            response_data["legal_risk_card"] = _legal_review_card(
                status="AWAITING_INTENT",
                contract_fingerprint=_contract_fingerprint(message),
                contract_char_count=len(message),
                excerpt=message,
                document_scope=_legal_document_scope(classification, detection_signals),
            )
            return response_data

    if contract_text is not None and represented_party is not None:
        # Drafts stored before the scope existed were reviewed as whole contracts.
        document_scope = document_scope or "FULL"
        audit_res, review = run_chat_contract_review(
            db,
            user,
            contract_text,
            represented_party=represented_party,
            document_scope=document_scope,
        )
        response_data["tools_executed"].append({
            "tool_name": "audit_contract_risk",
            "input": {
                "text_length": len(contract_text),
                "represented_party": represented_party,
                "document_scope": document_scope,
            },
            "risks_found": audit_res["total_risks_found"],
            "risk_score": audit_res["risk_score"],
        })
        log_audit_action(
            db,
            user.tenant_id,
            "LEGAL",
            "audit_contract_risk",
            {
                "text_length": len(contract_text),
                "represented_party": represented_party,
                "review_id": str(review.id),
            },
            {"risks": audit_res["total_risks_found"], "risk_score": audit_res["risk_score"]},
        )
        response_data["reply"] = review_reply(audit_res)
        response_data["legal_risk_card"] = audit_res
        return response_data

    if not can_search:
        response_data["reply"] = LEGAL_SEARCH_TOOL_OFF_REPLY + LEGAL_NOT_REVIEWED_NOTICE
        return response_data
    search_results = hybrid_search_documents(
        db,
        user.tenant_id,
        message,
        department="*" if user.role in {"Owner", "Admin", "CEO"} else user.department,
        collections=None,
        agent_access=agent.knowledge_access if agent.knowledge_access else None,
        user_role=user.role,
        user_department=user.department,
    )
    response_data["tools_executed"].append({
        "tool_name": "rag_search",
        "input": {"query": message, "department": user.department, "acl_applied": True},
        "result_count": len(search_results),
    })
    log_audit_action(
        db,
        user.tenant_id,
        "LEGAL",
        "rag_search",
        {"query": message, "acl_applied": True},
        {"count": len(search_results)},
    )
    # Retrieval returns its nearest excerpts whether or not any is about the question,
    # so the model decides which, if any, answer it. The four hardcoded glossary
    # definitions this replaces answered any question containing "bồi thường" with
    # the same text, whatever was asked.
    grounded = answer_from_legal_evidence(
        message,
        search_results,
        on_usage=record_legal_usage,
        prompts=legal_prompt_overlay,
    )
    if grounded is not None and grounded.answerable:
        used = [search_results[index] for index in grounded.used_evidence]
        response_data["citations"] = used
        response_data["reply"] = (
            f"{grounded.answer}\n\n"
            f"**Nguồn:** {' '.join(item['citation_tag'] for item in used)}\n\n"
            + LEGAL_APPROVAL_REMINDER
            + LEGAL_NOT_REVIEWED_NOTICE
        )
        return response_data
    if grounded is None and search_results:
        # No model could be used, so nothing can judge whether these excerpts answer
        # the question. They are shown as the nearest text found, not as the answer:
        # "Theo văn bản pháp luật..." asserted that they were.
        response_data["citations"] = search_results
        excerpts = "\n\n".join(
            f"{item['content']}\n{item['citation_tag']}"
            for item in search_results[:2]
        )
        response_data["reply"] = (
            "Tôi chưa thể tổng hợp câu trả lời lúc này. Dưới đây là các đoạn gần nhất "
            "tìm được trong tài liệu bạn được phép truy cập; hãy kiểm tra xem chúng có "
            "trả lời câu hỏi của bạn không:\n\n"
            f"{excerpts}\n\n"
            + LEGAL_APPROVAL_REMINDER
            + LEGAL_NOT_REVIEWED_NOTICE
        )
        return response_data
    # Nothing retrieved, or nothing retrieved that answers the question: citing the
    # nearest unrelated excerpt would present it as the law on the point.
    response_data["reply"] = LEGAL_NOT_FOUND_REPLY + LEGAL_NOT_REVIEWED_NOTICE
    return response_data
