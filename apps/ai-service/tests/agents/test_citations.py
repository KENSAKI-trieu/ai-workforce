"""What the graph is allowed to say when it has nothing to stand on.

The retrieval-failed path used to skip both checks: `validate_grounded_output` only looked
at empty answers when context existed, and citation verification returned early the moment
context was empty -- so an answer that invented its sources passed straight through.
"""

from __future__ import annotations

import uuid

import pytest

from app.governance.guardrails import validate_grounded_output
from app.governance.middleware.context import AgentRuntimeContext
from app.orchestration.decision import GraphDecision
from app.orchestration.engine import LangGraphEngine, OrchestrationRuntimeContext


class SingleDecision:
    def __init__(self, decision: GraphDecision) -> None:
        self.decision = decision

    def decide(self, state):
        return self.decision


def _security() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="Employee",
        department="ALL",
        agent_role="KNOWLEDGE",
        correlation_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        workflow_id=None,
        allowed_tools=frozenset(),
    )


def _state(security: AgentRuntimeContext, message: str) -> dict:
    return {
        "tenant_id": str(security.tenant_id),
        "user_id": str(security.user_id),
        "role": security.role,
        "department": security.department,
        "conversation_id": str(security.conversation_id),
        "workflow_id": None,
        "messages": [{"role": "user", "content": message}],
        "intent": "",
        "selected_agent": "",
        "retrieved_context": [],
        "tool_calls": [],
        "citations": [],
        "approval_id": None,
        "final_answer": None,
        "errors": [],
        "requested_agent": "KNOWLEDGE",
    }


def _run(decision: GraphDecision) -> dict:
    security = _security()
    return LangGraphEngine().invoke(
        _state(security, "Công ty cho nghỉ phép bao nhiêu ngày?"),
        context=OrchestrationRuntimeContext(
            security=security,
            decision_provider=SingleDecision(decision),
            tools={},
        ),
        thread_id=f"guard-{uuid.uuid4()}",
    )


def test_empty_answers_are_rejected_with_or_without_context() -> None:
    assert validate_grounded_output("  Câu trả lời  ", has_context=False) == "Câu trả lời"
    with pytest.raises(ValueError):
        validate_grounded_output("   ", has_context=True)
    with pytest.raises(ValueError):
        validate_grounded_output("   ", has_context=False)


def test_a_citation_invented_without_context_is_withheld() -> None:
    result = _run(GraphDecision(
        final_answer="Nhân viên được nghỉ 12 ngày. [Citation: Chính sách nghỉ phép 2025]",
        reason="Answer from parametric memory while retrieval returned nothing.",
    ))
    assert "withheld" in result["final_answer"]
    assert result["citations"] == []
    assert any(item.get("error") == "CITATION_WITHOUT_CONTEXT" for item in result["errors"])


def test_a_structured_citation_invented_without_context_is_withheld() -> None:
    result = _run(GraphDecision(
        final_answer="Nhân viên được nghỉ 12 ngày.",
        citations=[{"source": "Sổ tay nhân sự", "chunk_id": "khong-co-that"}],
        reason="Structured citation with nothing behind it.",
    ))
    assert "withheld" in result["final_answer"]
    assert any(item.get("error") == "CITATION_WITHOUT_CONTEXT" for item in result["errors"])


def test_an_honest_answer_without_context_still_reaches_the_user() -> None:
    # A governed domain has to be able to answer without pretending it read a document.
    answer = "Tôi không tìm thấy tài liệu nào trong kho tri thức cho câu hỏi này."
    result = _run(GraphDecision(final_answer=answer, reason="Say so instead of inventing a source."))
    assert result["final_answer"] == answer
    assert result["errors"] == []
    trace = {item["node"]: item["status"] for item in result["execution_trace"]}
    assert trace["citation_verification"] == "NO_CONTEXT"


LEAVE_CHUNK = "050d54c9-d6fc-46ab-863b-b1f905c0ed37"
TRAVEL_CHUNK = "c95b77f9-82d4-4070-99f9-36dec27327b8"
# Shaped like the backend's rag_search results, which carry a ready-made tag.
RETRIEVED = [
    {
        "id": LEAVE_CHUNK,
        "document_id": "Chinh_sach_Nghi_phep_2025.md",
        "document_title": "Chinh_sach_Nghi_phep_2025.md",
        "content": "Mỗi nhân viên chính thức có 12 ngày nghỉ phép hưởng nguyên lương mỗi năm.",
        "citation_tag": (
            "[Citation: Chinh_sach_Nghi_phep_2025.md, v1.0, 1. Quyền Lợi & Thời Gian Báo Trước; "
            f"chunk={LEAVE_CHUNK}]"
        ),
    },
    {
        "id": TRAVEL_CHUNK,
        "document_id": "Quy_dinh_Cong_tac_phi_2025.md",
        "document_title": "Quy_dinh_Cong_tac_phi_2025.md",
        "content": "Hạn mức công tác phí.",
    },
]


class FailingDecision:
    def decide(self, state):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


def _run_with_context(decision_provider) -> dict:
    security = _security().model_copy(update={"allowed_tools": frozenset({"rag_search"})})
    rag = type("Rag", (), {
        "name": "rag_search",
        "metadata": {"action": "READ_ONLY", "allowed_roles": ["*"], "allowed_departments": ["*"]},
        "invoke": lambda self, payload: RETRIEVED,
    })()
    return LangGraphEngine().invoke(
        _state(security, "Nhân viên chính thức được bao nhiêu ngày phép?"),
        context=OrchestrationRuntimeContext(
            security=security,
            decision_provider=decision_provider,
            tools={"rag_search": rag},
        ),
        thread_id=f"guard-{uuid.uuid4()}",
    )


def test_a_failed_model_call_is_reported_as_such_not_as_a_citation_problem() -> None:
    # With context retrieved, the engine's own failure notice -- which carries no
    # citation -- used to be withheld as UNVERIFIED_CITATION, hiding the outage.
    result = _run_with_context(FailingDecision())
    assert result["retrieved_context"]
    assert result["final_answer"] == "The decision model could not produce a validated result."
    assert [item["error"] for item in result["errors"]] == ["RuntimeError"]
    trace = {item["node"]: item["status"] for item in result["execution_trace"]}
    assert trace["citation_verification"] == "SKIPPED"


@pytest.mark.parametrize("decision", [
    # The model repeats the tag it was handed, verbatim, in the text ...
    GraphDecision(
        final_answer=f"12 ngày mỗi năm. {RETRIEVED[0]['citation_tag']}",
        reason="Quote the retrieved tag.",
    ),
    # ... or as a structured source ...
    GraphDecision(
        final_answer="12 ngày mỗi năm.",
        citations=[{"source": RETRIEVED[0]["citation_tag"]}],
        reason="Structured copy of the tag.",
    ),
    # ... or shortens it, keeping the document and dropping or rewording the rest.
    GraphDecision(
        final_answer="12 ngày mỗi năm. [Citation: Chinh_sach_Nghi_phep_2025.md, mục 1]",
        reason="Shortened tag.",
    ),
], ids=["tag-in-text", "tag-as-structured-source", "shortened-tag"])
def test_the_retrieval_tag_is_accepted_as_a_citation(decision: GraphDecision) -> None:
    # Only a bare title or id used to count, so a correct answer quoting the tag the
    # backend itself supplied was withheld as UNVERIFIED_CITATION.
    result = _run_with_context(SingleDecision(decision))
    assert result["final_answer"] == decision.final_answer
    assert result["errors"] == []


@pytest.mark.parametrize("citation", [
    # A retrieved chunk cannot vouch for a document that was never retrieved.
    f"Chinh_sach_Luong_2025.md, v1.0, 1. Lương; chunk={LEAVE_CHUNK}",
    # Nor can a retrieved document borrow another document's chunk.
    f"Chinh_sach_Nghi_phep_2025.md, v1.0, 1. Quyền Lợi; chunk={TRAVEL_CHUNK}",
    # A chunk id nobody retrieved.
    "Chinh_sach_Nghi_phep_2025.md, v1.0; chunk=00000000-0000-0000-0000-000000000000",
    # A document nobody retrieved, in the tag's shape.
    "Chinh_sach_Luong_2025.md, v1.0, 1. Lương",
])
def test_a_tag_shaped_citation_to_something_not_retrieved_is_withheld(citation: str) -> None:
    result = _run_with_context(SingleDecision(GraphDecision(
        final_answer=f"12 ngày mỗi năm. [Citation: {citation}]",
        reason="Cite a source outside the retrieved context.",
    )))
    assert "withheld" in result["final_answer"]
    assert any(item.get("error") == "UNVERIFIED_CITATION" for item in result["errors"])
