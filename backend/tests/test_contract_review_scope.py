"""An excerpt is reviewed for what it says, a whole contract also for what it lacks."""

import pytest

from app.services.contract_review.analyzer import review_contract

SNIPPET = (
    "Căn cứ Bộ luật Dân sự, các bên thỏa thuận mức phạt vi phạm "
    "là 30% giá trị hợp đồng đã ký."
)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Pure analysis: these tests never reach the database."""
    yield


def test_an_excerpt_is_not_scored_on_clauses_it_was_never_sent_with():
    full = review_contract(SNIPPET, "x", "PARTY_B")
    excerpt = review_contract(SNIPPET, "x", "PARTY_B", document_scope="EXCERPT")

    assert full["missing_clauses_count"] > 0
    assert excerpt["missing_clauses_count"] == 0
    assert not [f for f in excerpt["findings"] if f["finding_type"] == "MISSING_CLAUSE"]
    assert excerpt["document_scope"] == "EXCERPT"
    assert "đoạn trích" in excerpt["summary"]
    # The clause it does contain is judged exactly as before.
    penalty = [f for f in excerpt["findings"] if f["category"] == "PENALTY"]
    assert penalty == [f for f in full["findings"] if f["category"] == "PENALTY"]


def test_whole_contracts_are_reviewed_as_before_by_default():
    assert review_contract(SNIPPET, "x", "PARTY_B")["document_scope"] == "FULL"
