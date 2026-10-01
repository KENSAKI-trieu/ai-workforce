from collections.abc import Iterator
from typing import Any

from app.core.config import settings
from app.services.chunking.rag_chunking import chunk_text


def iter_chunk_document(
    content: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    min_chunk_size: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield chunks one at a time with exact segment counters."""
    chunks = chunk_text(
        content,
        chunk_size=chunk_size or settings.RAG_CHUNK_MAX_TOKENS,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP_TOKENS if chunk_overlap is None else chunk_overlap,
        target_size=settings.RAG_CHUNK_TARGET_TOKENS,
        min_size=settings.RAG_CHUNK_MIN_TOKENS if min_chunk_size is None else min_chunk_size,
    )
    for index, chunk in enumerate(chunks):
        processed_segments = index + 1
        yield {
            "processed_segments": processed_segments,
            "total_segments": len(chunks),
            "remaining_segments": len(chunks) - processed_segments,
            "chunks_created": processed_segments,
            "chunks": [chunk],
        }


def chunk_document(
    content: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    min_chunk_size: int | None = None,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for progress in iter_chunk_document(
        content,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        min_chunk_size=min_chunk_size,
    ):
        chunks.extend(progress["chunks"])
    return chunks
