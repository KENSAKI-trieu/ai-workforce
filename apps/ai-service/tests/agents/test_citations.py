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
from app.agents.base import notices
from app.agents.base.decision import GraphDecision
from app.agents.base.nodes import OrchestrationRuntimeContext
from app.agents.registry import LangGraphEngine


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
    assert result["final_answer"] == notices.CITATION_UNVERIFIED
    assert result["citations"] == []
    assert any(item.get("error") == "CITATION_WITHOUT_CONTEXT" for item in result["errors"])


def test_a_structured_citation_invented_without_context_is_withheld() -> None:
    result = _run(GraphDecision(
        final_answer="Nhân viên được nghỉ 12 ngày.",
        citations=[{"source": "Sổ tay nhân sự", "chunk_id": "khong-co-that"}],
        reason="Structured citation with nothing behind it.",
    ))
    assert result["final_answer"] == notices.CITATION_UNVERIFIED
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
    assert result["final_answer"] == notices.DECISION_FAILED
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
    assert result["final_answer"] == notices.CITATION_UNVERIFIED
    assert any(item.get("error") == "UNVERIFIED_CITATION" for item in result["errors"])


def _run_with_disabled_review(decision: GraphDecision) -> dict:
    security = _security().model_copy(update={"allowed_tools": frozenset({"rag_search"})})
    rag = type("Rag", (), {
        "name": "rag_search",
        "metadata": {"action": "READ_ONLY", "allowed_roles": ["*"], "allowed_departments": ["*"]},
        "invoke": lambda self, payload: RETRIEVED,
    })()
    return LangGraphEngine().invoke(
        _state(security, "Rà soát rủi ro hợp đồng này giúp tôi"),
        context=OrchestrationRuntimeContext(
            security=security,
            decision_provider=SingleDecision(decision),
            tools={"rag_search": rag},
            disabled_tools={"audit_contract_risk": "Rà soát rủi ro hợp đồng"},
        ),
        thread_id=f"guard-{uuid.uuid4()}",
    )


def test_a_request_for_a_tool_the_organisation_turned_off_says_so() -> None:
    # Context was retrieved, and the notice carries no citation. Before, the model answered
    # the review itself and the user read "citations could not be verified" in English.
    result = _run_with_disabled_review(GraphDecision(
        tool_name="audit_contract_risk", reason="The request is a contract review."
    ))
    assert result["retrieved_context"]
    assert result["final_answer"] == notices.tool_disabled("Rà soát rủi ro hợp đồng")
    assert result["tool_calls"] == []
    # Not filed under model_decision: the backend reads that as an outage and would rerun
    # the turn through its deterministic flow.
    assert [(item["node"], item["error"]) for item in result["errors"]] == [
        ("tool_policy", "TOOL_DISABLED_BY_TENANT")
    ]
    trace = {item["node"]: item["status"] for item in result["execution_trace"]}
    assert trace["citation_verification"] == "SKIPPED"


def test_a_tool_that_is_neither_bound_nor_disabled_is_still_refused_generically() -> None:
    result = _run_with_disabled_review(GraphDecision(
        tool_name="create_jira_ticket", reason="Invented tool."
    ))
    assert result["final_answer"] == notices.TOOL_NOT_AVAILABLE
    assert result["errors"][-1]["error"] == "TOOL_NOT_ALLOWED"


def test_a_request_for_a_tool_the_users_role_may_not_use_says_so() -> None:
    # The gateway leaves such a tool out of the contracts, so the model never saw it: asked
    # for an NDA, it cited the company's legal rules for an approval they never mention.
    security = _security().model_copy(update={"allowed_tools": frozenset({"rag_search"})})
    rag = type("Rag", (), {
        "name": "rag_search",
        "metadata": {"action": "READ_ONLY", "allowed_roles": ["*"], "allowed_departments": ["*"]},
        "invoke": lambda self, payload: RETRIEVED,
    })()
    result = LangGraphEngine().invoke(
        _state(security, "Soạn giúp tôi một thỏa thuận bảo mật"),
        context=OrchestrationRuntimeContext(
            security=security,
            decision_provider=SingleDecision(GraphDecision(
                tool_name="generate_legal_document", reason="The request is a draft."
            )),
            tools={"rag_search": rag},
            restricted_tools={"generate_legal_document": "soạn văn bản pháp lý"},
        ),
        thread_id=f"guard-{uuid.uuid4()}",
    )
    assert result["final_answer"] == notices.tool_restricted("soạn văn bản pháp lý")
    assert result["citations"] == []
    assert [(item["node"], item["error"]) for item in result["errors"]] == [
        ("tool_policy", "TOOL_RESTRICTED_BY_ROLE")
    ]


def test_an_unanswerable_request_gets_the_graphs_notice_not_a_withheld_answer() -> None:
    # Context was retrieved but says nothing on the point. The model's own "not found"
    # carried no citation, so the check withheld it as unverified.
    result = _run_with_context(SingleDecision(GraphDecision(
        answerable=False, reason="The excerpts are about leave, not trademarks."
    )))
    assert result["retrieved_context"]
    assert result["final_answer"] == notices.NOT_IN_DOCUMENTS
    assert result["citations"] == []
    assert result["errors"] == []
    trace = {item["node"]: item["status"] for item in result["execution_trace"]}
    assert trace["citation_verification"] == "SKIPPED"


class SearchUntilTold:
    """Searches again on every turn it is allowed to, like the live model did."""

    def __init__(self, *, obeys_final_step: bool) -> None:
        self.obeys_final_step = obeys_final_step
        self.final_steps: list[bool] = []

    def decide(self, state):
        self.final_steps.append(bool(state.get("final_step")))
        if state.get("final_step") and self.obeys_final_step:
            return GraphDecision(answerable=False, reason="Nothing found.")
        return GraphDecision(
            tool_name="rag_search",
            tool_args={"query": f"thử lần {len(self.final_steps)}"},
            citations=[{"source": "Chinh_sach_Nghi_phep_2025.md"}],
            reason="Search again.",
        )


def test_the_last_decision_must_answer_instead_of_searching_again() -> None:
    provider = SearchUntilTold(obeys_final_step=True)
    result = _run_with_context(provider)
    assert provider.final_steps == [False, False, False, True]
    assert result["final_answer"] == notices.NOT_IN_DOCUMENTS
    assert result["citations"] == []


def test_a_model_that_ignores_the_last_step_ends_on_the_limit_without_stray_citations() -> None:
    result = _run_with_context(SearchUntilTold(obeys_final_step=False))
    assert result["final_answer"] == notices.ITERATION_LIMIT
    # The searches it cited did not answer; they used to be shown under the notice.
    assert result["citations"] == []
    assert len(result["tool_calls"]) == 3


SECOND_SEARCH = [{
    "id": "9f3c2e1a-b7d4-4c8e-a5f1-3e6d9b2c7a4f",
    "document_id": "Quy_che_phap_che.md",
    "document_title": "Quy_che_phap_che.md",
    "content": "Mức phạt không vượt quá 8% giá trị phần nghĩa vụ bị vi phạm.",
}]


def _run_after_second_search(answer: GraphDecision) -> dict:
    security = _security().model_copy(update={"allowed_tools": frozenset({"rag_search"})})
    rag = type("Rag", (), {
        "name": "rag_search",
        "metadata": {"action": "READ_ONLY", "allowed_roles": ["*"], "allowed_departments": ["*"]},
        # The graph's own search first, then the one the model asks for.
        "responses": [RETRIEVED, SECOND_SEARCH],
        "invoke": lambda self, payload: self.responses.pop(0),
    })()
    provider = type("Seq", (), {
        "steps": [
            GraphDecision(tool_name="rag_search", tool_args={"query": "mức phạt"}, reason="Look again."),
            answer,
        ],
        "decide": lambda self, state: self.steps.pop(0),
    })()
    return LangGraphEngine().invoke(
        _state(security, "Mức phạt tối đa là bao nhiêu?"),
        context=OrchestrationRuntimeContext(
            security=security, decision_provider=provider, tools={"rag_search": rag}
        ),
        thread_id=f"guard-{uuid.uuid4()}",
    )


def test_an_answer_written_after_a_second_search_is_still_checked() -> None:
    # Any tool call used to skip the check, so this invented source reached the user.
    result = _run_after_second_search(GraphDecision(
        final_answer="Tối đa 8%. [Citation: Luat_Thuong_mai_2005.md]", reason="Answer."
    ))
    assert result["final_answer"] == notices.CITATION_UNVERIFIED
    assert any(item.get("error") == "UNVERIFIED_CITATION" for item in result["errors"])


def test_a_source_found_by_the_second_search_counts_as_retrieved() -> None:
    answer = GraphDecision(
        final_answer=f"Tối đa 8%. [Citation: Quy_che_phap_che.md; chunk={SECOND_SEARCH[0]['id']}]",
        reason="Answer from the second search.",
    )
    result = _run_after_second_search(answer)
    assert result["final_answer"] == answer.final_answer
    assert result["errors"] == []
