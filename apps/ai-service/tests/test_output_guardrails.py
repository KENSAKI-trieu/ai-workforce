"""What the graph is allowed to say when it has nothing to stand on.

The retrieval-failed path used to skip both checks: `validate_grounded_output` only looked
at empty answers when context existed, and citation verification returned early the moment
context was empty -- so an answer that invented its sources passed straight through.
"""

from __future__ import annotations

import uuid

import pytest

from app.guardrails.output_guard import validate_grounded_output
from app.middleware.context import AgentRuntimeContext
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
