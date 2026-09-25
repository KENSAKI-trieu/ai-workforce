"""Hosted Jina AI reranker provider."""

import math
from typing import Any

import httpx

from app.core.config import settings
from app.services.reranking.base import BaseReranker


class JinaAPIReranker(BaseReranker):
    """Score documents through Jina's hosted reranker API."""

    backend = "jina"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        url: str | None = None,
        model_name: str | None = None,
        timeout: float | None = None,
        score_normalization: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.JINA_API_KEY
        self.url = (url or settings.JINA_RERANK_URL).rstrip("/")
        self.model_name = model_name or settings.JINA_RERANK_MODEL
        self.timeout = timeout or settings.JINA_RERANK_TIMEOUT_SECONDS
        self.score_normalization = (
            score_normalization or settings.JINA_RERANK_SCORE_NORMALIZATION
        )

    def _normalize_score(self, score: float) -> float:
        normalization = self.score_normalization
        if normalization == "auto":
            normalization = "sigmoid" if self.model_name.startswith("jina-reranker-v3") else "none"
        if normalization == "sigmoid":
            if score >= 0:
                return 1.0 / (1.0 + math.exp(-score))
            exp_score = math.exp(score)
            return exp_score / (1.0 + exp_score)
        if normalization == "none" and 0.0 <= score <= 1.0:
            return score
        raise RuntimeError("Jina reranker returned a score outside the normalized range")

    def score(
        self,
        query: str,
        documents: list[str],
        candidates: list[dict[str, Any]],
    ) -> list[float]:
        if not self.api_key:
            raise RuntimeError("JINA_API_KEY is required for Jina reranking")
        if len(documents) != len(candidates):
            raise ValueError("Jina document and candidate counts do not match")
        if not documents:
            return []

        try:
            response = httpx.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "model": self.model_name,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Jina reranker request failed") from exc

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise RuntimeError("Jina reranker returned an invalid response")

        scores: list[float | None] = [None] * len(documents)
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("Jina reranker returned an invalid result")
            index = result.get("index")
            relevance_score = result.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(documents)
                or scores[index] is not None
                or not isinstance(relevance_score, (int, float))
                or isinstance(relevance_score, bool)
            ):
                raise RuntimeError("Jina reranker returned an invalid result")
            scores[index] = self._normalize_score(float(relevance_score))

        if any(score is None for score in scores):
            raise RuntimeError("Jina reranker returned incomplete scores")
        return [float(score) for score in scores]
