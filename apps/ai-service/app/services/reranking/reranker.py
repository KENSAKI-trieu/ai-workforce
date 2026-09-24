import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.reranking.base import BaseReranker
from app.services.reranking.factory import get_rerank_provider
from app.services.reranking.lexical import LexicalReranker


logger = logging.getLogger(__name__)
_INTERNAL_SCORE_KEYS = ("_rrf_score", "_dense_score", "_sparse_score")


@dataclass(frozen=True)
class RerankPipelineResult:
    results: list[dict[str, Any]]
    backend: str
    model: str
    fallback_used: bool
    candidates_scored: int
    latency_ms: float


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _candidate_key(candidate: dict[str, Any]) -> str:
    stable_id = candidate.get("id") or candidate.get("content_hash")
    if stable_id:
        return str(stable_id)
    normalized = " ".join(str(candidate.get("content", "")).split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _prepare_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not str(candidate.get("content", "")).strip():
            continue
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= settings.RERANK_CANDIDATE_LIMIT:
            break
    return unique


def _document_for_model(candidate: dict[str, Any]) -> str:
    metadata = [
        ("Tài liệu", candidate.get("document_title") or candidate.get("document_name")),
        ("Mục", candidate.get("section_title")),
        ("Phòng ban", candidate.get("department")),
        ("Loại", candidate.get("document_type")),
    ]
    lines = [f"{label}: {value}" for label, value in metadata if value]
    lines.extend(("Nội dung:", str(candidate.get("content", "")).strip()))
    return "\n".join(lines)


def _retrieval_prior(candidate: dict[str, Any]) -> float:
    dense = max(0.0, min(1.0, float(candidate.get("_dense_score", 0.0))))
    sparse = _clip(candidate.get("_sparse_score", 0.0))
    rrf = _clip(candidate.get("_rrf_score", candidate.get("score", 0.0)))
    return (dense * 0.65) + (sparse * 0.20) + (rrf * 0.15)


class RerankPipeline:
    def __init__(
        self,
        provider: BaseReranker | None = None,
        fallback_provider: BaseReranker | None = None,
    ) -> None:
        self.provider = provider or get_rerank_provider()
        self.fallback_provider = fallback_provider or LexicalReranker()

    def run(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 5,
    ) -> RerankPipelineResult:
        started = time.perf_counter()
        prepared = _prepare_candidates(candidates)
        if not prepared:
            return RerankPipelineResult([], self.provider.backend, self.provider.model_name, False, 0, 0.0)

        documents = [_document_for_model(candidate) for candidate in prepared]
        provider = self.provider
        fallback_used = False
        try:
            model_scores = provider.score(query, documents, prepared)
        except Exception:
            if not settings.RERANK_FALLBACK_ENABLED or provider.backend == "lexical":
                raise
            logger.exception(
                "Rerank provider %s failed; falling back to lexical scoring",
                provider.model_name,
            )
            provider = self.fallback_provider
            fallback_used = True
            model_scores = provider.score(query, documents, prepared)

        if len(model_scores) != len(prepared):
            raise RuntimeError("Rerank score count does not match candidate count")

        ranked: list[dict[str, Any]] = []
        for candidate, model_score in zip(prepared, model_scores):
            normalized_model_score = _clip(model_score)
            retrieval_score = _retrieval_prior(candidate)
            if provider.backend == "lexical":
                final_score = normalized_model_score
                threshold = settings.RAG_MIN_RELEVANCE_SCORE
            else:
                weight = settings.RERANK_MODEL_WEIGHT
                final_score = (normalized_model_score * weight) + (retrieval_score * (1.0 - weight))
                threshold = settings.RERANK_MIN_MODEL_SCORE
            if normalized_model_score < threshold:
                continue

            result = dict(candidate)
            result.update({
                "score": round(final_score, 6),
                "rerank_score": round(final_score, 6),
                "rerank_model_score": round(normalized_model_score, 6),
                "retrieval_score": round(retrieval_score, 6),
                "rerank_model": provider.model_name,
            })
            for key in _INTERNAL_SCORE_KEYS:
                result.pop(key, None)
            ranked.append(result)

        ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RerankPipelineResult(
            results=ranked[:top_k],
            backend=provider.backend,
            model=provider.model_name,
            fallback_used=fallback_used,
            candidates_scored=len(prepared),
            latency_ms=round(elapsed_ms, 3),
        )


def rerank_with_metadata(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 5,
) -> RerankPipelineResult:
    return RerankPipeline().run(query, candidates, top_k=top_k)


def rerank_chunks(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    return rerank_with_metadata(query, candidates, top_k).results
