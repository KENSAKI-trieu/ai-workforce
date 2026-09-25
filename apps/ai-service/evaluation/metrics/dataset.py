from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvaluationCase:
    query: str
    relevant_chunk_ids: set[str] = field(default_factory=set)
    expected_answer_terms: set[str] = field(default_factory=set)
