import re
from typing import Any

from app.core.config import settings
from app.services.reranking.base import BaseReranker


_QUERY_STOPWORDS = {
    "ai", "bao", "bị", "có", "của", "đã", "được", "gì", "hiện", "khi",
    "là", "nào", "như", "phải", "thế", "thì", "tôi", "trong", "và", "về",
}


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class LexicalReranker(BaseReranker):
    """Cheap deterministic fallback used by tests and degraded operation."""

    backend = "lexical"
    model_name = "lexical-fusion-v1"

    def score(
        self,
        query: str,
        documents: list[str],
        candidates: list[dict[str, Any]],
    ) -> list[float]:
        query_words = set(re.findall(r"\w+", query.lower()))
        query_terms = query_words - _QUERY_STOPWORDS or query_words
        scores: list[float] = []
        for document, candidate in zip(documents, candidates):
            content_words = set(re.findall(r"\w+", document.lower()))
            lexical = len(query_terms.intersection(content_words)) / max(len(query_terms), 1)
            dense = max(-1.0, min(1.0, float(candidate.get("_dense_score", -1.0))))
            rrf = _clip(candidate.get("_rrf_score", candidate.get("score", 0.0)))
            sparse = _clip(candidate.get("_sparse_score", 0.0))
            if dense < settings.RAG_MIN_DENSE_SCORE and lexical < 0.40:
                scores.append(0.0)
                continue
            if settings.EMBEDDING_BACKEND == "deterministic":
                score = (lexical * 0.70) + (sparse * 0.20) + (rrf * 0.10)
            else:
                score = (max(dense, 0.0) * 0.70) + (lexical * 0.20) + (rrf * 0.05) + (sparse * 0.05)
            scores.append(_clip(score))
        return scores
