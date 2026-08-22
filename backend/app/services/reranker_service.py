"""Lightweight relevance gate for fused dense and sparse RAG candidates."""

import logging
import re
from typing import List, Dict, Any

from app.core.config import settings
from app.services.ai_service_client import get_ai_service_client

logger = logging.getLogger(__name__)

_QUERY_STOPWORDS = {
    "ai", "bao", "bị", "có", "của", "đã", "được", "gì", "hiện", "khi",
    "là", "nào", "như", "phải", "thế", "thì", "tôi", "trong", "và", "về",
}


def rerank_chunks(query_text: str, candidate_chunks: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Combine absolute dense similarity, lexical coverage, sparse rank and RRF.

    The absolute dense floor prevents RRF from always returning a top result for
    out-of-domain questions. Strong lexical matches remain eligible for legacy
    and deterministic embeddings.
    """
    if not candidate_chunks:
        return []

    ai_client = get_ai_service_client()
    if ai_client.enabled:
        return ai_client.rerank(query_text, candidate_chunks, top_k=top_k)

    query_words = set(re.findall(r'\w+', query_text.lower()))
    query_terms = query_words - _QUERY_STOPWORDS or query_words
    reranked = []

    for chunk in candidate_chunks:
        content_words = set(re.findall(r'\w+', chunk["content"].lower()))
        overlap = len(query_terms.intersection(content_words))
        lexical_score = overlap / max(len(query_terms), 1)
        dense_score = max(-1.0, min(1.0, float(chunk.get("_dense_score", -1.0))))
        rrf_score = float(chunk.get("_rrf_score", chunk.get("score", 0.0)))
        sparse_score = float(chunk.get("_sparse_score", 0.0))

        # Reject out-of-domain dense neighbors. Exact/specific lexical matches can
        # still pass when using the deterministic test backend or legacy vectors.
        if (
            dense_score < settings.RAG_MIN_DENSE_SCORE
            and lexical_score < 0.40
        ):
            continue

        if settings.EMBEDDING_BACKEND == "deterministic":
            refined_score = round(
                (lexical_score * 0.70)
                + (sparse_score * 0.20)
                + (rrf_score * 0.10),
                4,
            )
        else:
            refined_score = round(
                (max(dense_score, 0.0) * 0.70)
                + (lexical_score * 0.20)
                + (rrf_score * 0.05)
                + (sparse_score * 0.05),
                4,
            )
        if refined_score < settings.RAG_MIN_RELEVANCE_SCORE:
            continue

        chunk_copy = dict(chunk)
        chunk_copy["score"] = refined_score
        chunk_copy["rerank_score"] = refined_score
        for internal_key in ("_rrf_score", "_dense_score", "_sparse_score"):
            chunk_copy.pop(internal_key, None)
        reranked.append(chunk_copy)

    # Sort descending by refined rerank score
    reranked.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    return reranked[:top_k]