from functools import lru_cache

from app.core.config import settings
from app.services.reranking.base import BaseReranker
from app.services.reranking.bge import BGEReranker
from app.services.reranking.jina import JinaAPIReranker
from app.services.reranking.lexical import LexicalReranker


@lru_cache(maxsize=1)
def get_rerank_provider() -> BaseReranker:
    backend = settings.RERANK_BACKEND.strip().lower()
    if backend in {"bge", "bge-reranker", "bge_reranker", "bge_cross_encoder"}:
        return BGEReranker()
    if backend in {"jina", "jina_api", "jina-ai"}:
        return JinaAPIReranker()
    if backend in {"lexical", "deterministic"}:
        return LexicalReranker()
    raise ValueError(f"Unsupported rerank backend: {settings.RERANK_BACKEND}")

