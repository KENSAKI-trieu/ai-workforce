"""
Hybrid RAG Engine Service for AI Workforce.
Combines Dense Vector Search (pgvector) + Sparse BM25/FTS Keyword Search
with Reciprocal Rank Fusion (RRF) and Inline Citation Tag generation.
"""

import logging
import math
import uuid
import re
from collections import Counter
from datetime import date
from collections.abc import Callable
from typing import List, Dict, Any
from sqlalchemy import and_, false, func, or_
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.models import DocumentChunk, KnowledgeDocument
from app.domains.knowledge.embedding_service import (
    build_embedding_text,
    calculate_content_hash,
    get_embedding_service,
)
from app.domains.knowledge.rag_chunking import chunk_text
from app.domains.knowledge.reranker_service import rerank_chunks
from app.clients.ai_service_client import get_ai_service_client

logger = logging.getLogger(__name__)

CHUNK_MIN_TOKENS = settings.RAG_CHUNK_MIN_TOKENS
CHUNK_TARGET_TOKENS = settings.RAG_CHUNK_TARGET_TOKENS
CHUNK_SIZE_TOKENS = settings.RAG_CHUNK_MAX_TOKENS
CHUNK_OVERLAP_TOKENS = settings.RAG_CHUNK_OVERLAP_TOKENS


def chunk_document_content(
    content: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    chunk_overlap: int = CHUNK_OVERLAP_TOKENS,
    progress_callback: Callable[[dict[str, int]], None] | None = None,
    progress_stream_id: str | None = None,
    progress_completed_before: int = 0,
    progress_total_count: int | None = None,
    progress_chunks_before: int = 0,
    min_chunk_size: int = CHUNK_MIN_TOKENS,
) -> list[dict[str, Any]]:
    """
    Create header-aware chunks bounded by estimated model tokens.

    The AI service chunks when it is configured; otherwise the same ``rag_chunking``
    module runs here. Sections under ``min_chunk_size`` are merged with their
    neighbours under the same heading; sections larger than ``chunk_size`` are split
    into sliding windows with ``chunk_overlap`` shared tokens.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be between zero and chunk_size - 1")

    ai_client = get_ai_service_client()
    if ai_client.enabled:
        progress_options: dict[str, Any] = {}
        if progress_stream_id:
            progress_options = {
                "progress_stream_id": progress_stream_id,
                "progress_completed_before": progress_completed_before,
                "progress_total_count": progress_total_count,
                "progress_chunks_before": progress_chunks_before,
            }
        return ai_client.chunk_document(
            content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            min_chunk_size=min_chunk_size,
            on_progress=progress_callback,
            **progress_options,
        )

    chunks = chunk_text(
        content,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        target_size=CHUNK_TARGET_TOKENS,
        min_size=min_chunk_size,
    )
    if progress_callback is not None:
        for index in range(len(chunks)):
            progress_callback({
                "processed_segments": index + 1,
                "total_segments": len(chunks),
                "remaining_segments": len(chunks) - index - 1,
                "chunks_created": index + 1,
            })
    return chunks


def build_configured_chunks(
    content: str,
    *,
    mode: str = "standard",
    chunk_size: int = CHUNK_SIZE_TOKENS,
    chunk_overlap: int = CHUNK_OVERLAP_TOKENS,
    parent_chunk_size: int = 1024,
    progress_callback: Callable[[dict[str, int]], None] | None = None,
    progress_stream_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build standard or parent-child chunks using the user's saved settings."""
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"standard", "parent_child"}:
        raise ValueError("mode must be either 'standard' or 'parent_child'")
    if parent_chunk_size <= 0:
        raise ValueError("parent_chunk_size must be greater than zero")
    if normalized_mode == "parent_child" and parent_chunk_size < chunk_size:
        raise ValueError("parent_chunk_size must be greater than or equal to chunk_size")

    if normalized_mode == "standard":
        return [
            {**chunk, "chunking_mode": "standard"}
            for chunk in chunk_document_content(
                content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                progress_callback=progress_callback,
                progress_stream_id=progress_stream_id,
            )
        ]

    parent_chunks = chunk_document_content(
        content,
        chunk_size=parent_chunk_size,
        chunk_overlap=0,
    )
    children: list[dict[str, Any]] = []
    for parent_index, parent in enumerate(parent_chunks):
        def report_child_progress(
            update: dict[str, int],
            *,
            current_parent_index: int = parent_index,
        ) -> None:
            if progress_callback is None:
                return
            parent_completed = update["remaining_segments"] == 0
            processed_parents = current_parent_index + int(parent_completed)
            progress_callback({
                "processed_segments": processed_parents,
                "total_segments": len(parent_chunks),
                "remaining_segments": len(parent_chunks) - processed_parents,
                "chunks_created": len(children) + update["chunks_created"],
            })

        child_chunks = chunk_document_content(
            parent["content"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            progress_callback=report_child_progress if progress_callback else None,
            progress_stream_id=progress_stream_id,
            progress_completed_before=parent_index,
            progress_total_count=len(parent_chunks),
            progress_chunks_before=len(children),
        )
        for child_index, child in enumerate(child_chunks):
            children.append({
                **child,
                "chunking_mode": "parent_child",
                "parent_chunk_index": parent_index,
                "parent_content": parent["content"],
                "child_chunk_index": child_index,
                "page": child.get("page") or parent.get("page"),
                "pages": child.get("pages") or parent.get("pages", []),
            })
    return children


def generate_embedding(text_content: str, dim: int = 1536) -> list[float]:
    """
    Generates embedding vector (1536 dim). Uses OpenAI/Gemini if API key configured,
    or a normalized deterministic hash embedding fallback.
    """
    import hashlib
    vec = []
    text_bytes = text_content.encode("utf-8")
    for i in range(dim):
        h = hashlib.sha256(text_bytes + str(i).encode()).digest()
        val = (int.from_bytes(h[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
        vec.append(val)
    norm = math.sqrt(sum(x*x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _rank_sparse_bm25(
    query_text: str,
    chunks: list[DocumentChunk],
    *,
    limit: int = 30,
) -> list[tuple[DocumentChunk, float]]:
    """Rank authorized chunks with a small in-process BM25 fallback."""
    query_tokens = re.findall(r"\w+", query_text.casefold())
    if not query_tokens or not chunks:
        return []

    corpus_tokens: list[list[str]] = []
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        searchable = " ".join(filter(None, (
            chunk.document_title,
            chunk.document_name,
            chunk.section_title,
            chunk.content,
        )))
        tokens = re.findall(r"\w+", searchable.casefold())
        corpus_tokens.append(tokens)
        document_frequency.update(set(tokens))

    average_length = sum(map(len, corpus_tokens)) / max(len(corpus_tokens), 1)
    corpus_size = len(corpus_tokens)
    k1 = 1.5
    b = 0.75
    scores: list[tuple[DocumentChunk, float]] = []
    for chunk, tokens in zip(chunks, corpus_tokens):
        frequencies = Counter(tokens)
        length_ratio = len(tokens) / max(average_length, 1.0)
        score = 0.0
        for term in query_tokens:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(
                1.0 + (corpus_size - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            score += inverse_frequency * (
                frequency * (k1 + 1.0)
                / (frequency + k1 * (1.0 - b + b * length_ratio))
            )
        if score > 0:
            scores.append((chunk, score))
    return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]


def _with_parent_context(
    results: list[dict[str, Any]],
    chunk_by_id: dict[uuid.UUID, DocumentChunk],
) -> list[dict[str, Any]]:
    """Hand a parent_child match to the reader as its parent section, once per parent.

    Children are small so the search can match them precisely; the parent is the text a
    reader needs to make sense of the match. Several children of one parent keep only the
    best-scoring one, so the same section is not sent twice. The child's position inside
    the parent stays in ``matched_span`` for a caller that must cut the parent down.
    """
    families: dict[tuple[Any, ...], list[DocumentChunk]] | None = None
    seen_parents: set[tuple[Any, ...]] = set()
    expanded_results: list[dict[str, Any]] = []
    for item in results:
        parent_index = item.get("parent_chunk_index")
        chunk = chunk_by_id.get(uuid.UUID(item["id"]))
        parent_content = (chunk.metadata_ or {}).get("parent_content") if chunk else None
        if parent_index is None or not parent_content:
            expanded_results.append(item)
            continue
        key = (item["document_id"], item["version"], parent_index)
        if key in seen_parents:
            continue
        seen_parents.add(key)
        if families is None:
            families = {}
            for candidate in chunk_by_id.values():
                candidate_parent = (candidate.metadata_ or {}).get("parent_chunk_index")
                if candidate_parent is None:
                    continue
                families.setdefault((
                    candidate.document_id or candidate.document_name,
                    candidate.version,
                    candidate_parent,
                ), []).append(candidate)
        child_content = item["content"]
        start = parent_content.find(child_content.strip()[:80])
        family = families.get(key, [])
        starts = [c.page_start or c.page for c in family if (c.page_start or c.page) is not None]
        ends = [
            c.page_end or c.page_start or c.page
            for c in family
            if (c.page_end or c.page_start or c.page) is not None
        ]
        expanded_results.append({
            **item,
            "content": parent_content,
            "matched_span": (
                [start, min(start + len(child_content), len(parent_content))]
                if start >= 0
                else None
            ),
            "page_start": min(starts) if starts else item.get("page_start"),
            "page_end": max(ends) if ends else item.get("page_end"),
        })
    return expanded_results


def in_reading_order(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder search results for a model to read: by document, each in its text order.

    Search ranks by score, so two pieces of one article can arrive back to front with
    another document between them. Documents keep the order of their best result, so
    the strongest source still comes first.
    """
    by_document: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for item in results:
        by_document.setdefault((item.get("document_id"), item.get("version")), []).append(item)
    return [
        item
        for group in by_document.values()
        for item in sorted(
            group,
            key=lambda entry: (
                entry["chunk_index"] if entry.get("chunk_index") is not None else math.inf
            ),
        )
    ]


def user_search_scope(db: Session, user: Any) -> Dict[str, Any]:
    """How far a person's knowledge search reaches, from the permissions of their position.

    Every agent used to decide this with ``user.role in {"Owner", "Admin", "CEO"}``, so the
    org-structure boxes "Tra cứu tri thức mọi phòng ban" and "Xem tài liệu hạn chế" changed
    nothing. Spread into ``hybrid_search_documents``; a caller pinning its own department
    overrides ``department`` after the spread.
    """
    from app.domains.platform.position_service import user_permissions

    granted = user_permissions(db, user)
    return {
        "department": "*" if "knowledge.scope.company" in granted else user.department,
        "can_read_restricted": "knowledge.view_restricted" in granted,
        "user_role": user.role,
        "user_department": user.department,
    }


def hybrid_search_documents(
    db: Session,
    tenant_id: uuid.UUID,
    query_text: str,
    department: str = "ALL",
    top_k: int = 5,
    collections: list[str] | None = None,
    agent_access: list[str] | None = None,
    user_role: str | None = None,
    user_department: str | None = None,
    as_of: date | None = None,
    can_read_restricted: bool | None = None,
) -> List[Dict[str, Any]]:
    """
    Hybrid Search combining Dense Vector Cosine Similarity and Sparse Keyword Matching.
    Calculates RRF (Reciprocal Rank Fusion) scores and returns Top-K relevant document chunks with metadata.

    ``can_read_restricted`` lifts the per-document role list and the "restricted" label; a
    person's callers pass it from ``user_search_scope``. Left None, it falls back to the
    old role-string check, for callers searching as a system principal.
    """
    effective_on = as_of or date.today()
    query = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == tenant_id,
        DocumentChunk.status == "active",
        or_(
            DocumentChunk.effective_date.is_(None),
            DocumentChunk.effective_date <= effective_on,
        ),
        or_(
            DocumentChunk.expiration_date.is_(None),
            DocumentChunk.expiration_date >= effective_on,
        ),
    )
    if department != "*":
        query = query.filter(
            (DocumentChunk.department_access == "ALL")
            | (DocumentChunk.department_access == department)
        )
    if collections:
        query = query.filter(DocumentChunk.collection_name.in_(collections))
    # An agent reads exactly what is ticked for it and nothing else: an empty scope finds
    # nothing, and there is no "everything" wildcard any more ("*" matches no selector).
    # None is for searches made by a person rather than an agent.
    if agent_access is not None:
        selectors = {str(value).strip() for value in agent_access if str(value).strip()}
        access_filters = []
        for selector in selectors - {"*", "none"}:
            prefix, separator, value = selector.partition(":")
            if not separator:
                access_filters.append(DocumentChunk.collection_name == selector)
            elif prefix == "collection" and value:
                access_filters.append(DocumentChunk.collection_name == value)
            elif prefix == "document" and value:
                access_filters.append(or_(
                    DocumentChunk.document_id == value,
                    DocumentChunk.document_name == value,
                ))
            elif prefix == "chunk" and value:
                try:
                    access_filters.append(DocumentChunk.id == uuid.UUID(value))
                except ValueError:
                    continue
        query = query.filter(or_(*access_filters) if access_filters else false())
    normalized_role = user_role.strip().lower() if user_role else None
    normalized_department = (
        user_department.strip().lower() if user_department else None
    )
    principals = {value for value in (normalized_role, normalized_department) if value}
    if can_read_restricted is None:
        can_read_restricted = normalized_role in {"owner", "admin", "ceo"}
    if not can_read_restricted:
        explicit_role_matches = [
            DocumentChunk.allowed_roles.contains([principal])
            for principal in sorted(principals)
        ]
        explicit_role_match = (
            or_(*explicit_role_matches) if explicit_role_matches else false()
        )
        query = query.filter(
            or_(DocumentChunk.allowed_roles == [], explicit_role_match),
            or_(
                DocumentChunk.confidentiality != "restricted",
                and_(DocumentChunk.allowed_roles != [], explicit_role_match),
            ),
        )
    chunks = query.all()

    if not chunks:
        return []

    chunk_by_id = {chunk.id: chunk for chunk in chunks}
    authorized_ids = list(chunk_by_id)
    embedding_service = get_embedding_service()
    dense_ranked: list[tuple[DocumentChunk, float]] = []
    sparse_ranked: list[tuple[DocumentChunk, float]] = []
    try:
        query_embedding = embedding_service.embed_query(query_text)
        distance = DocumentChunk.embedding.cosine_distance(query_embedding).label(
            "distance"
        )
        dense_ranked.extend(
            db.query(DocumentChunk, distance)
            .filter(
                DocumentChunk.id.in_(authorized_ids),
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == embedding_service.model_name,
                DocumentChunk.embedding_version == embedding_service.version,
            )
            .order_by(distance.asc())
            .limit(30)
            .all()
        )

        legacy_query_embedding = generate_embedding(query_text)
        legacy_distance = DocumentChunk.dense_embedding.cosine_distance(
            legacy_query_embedding
        ).label("legacy_distance")
        dense_ranked.extend(
            db.query(DocumentChunk, legacy_distance)
            .filter(
                DocumentChunk.id.in_(authorized_ids),
                DocumentChunk.embedding.is_(None),
                DocumentChunk.dense_embedding.is_not(None),
            )
            .order_by(legacy_distance.asc())
            .limit(30)
            .all()
        )
        dense_ranked.sort(key=lambda item: float(item[1]))
        dense_ranked = dense_ranked[:30]

        text_vector = func.to_tsvector("simple", DocumentChunk.content)
        text_query = func.plainto_tsquery("simple", query_text)
        text_rank = func.ts_rank_cd(text_vector, text_query).label("text_rank")
        sparse_ranked = (
            db.query(DocumentChunk, text_rank)
            .filter(
                DocumentChunk.id.in_(authorized_ids),
                text_vector.op("@@")(text_query),
            )
            .order_by(text_rank.desc())
            .limit(30)
            .all()
        )
    except Exception as exc:
        logger.warning("Indexed hybrid retrieval failed; using in-process fallback: %s", exc)
        db.rollback()

    if not dense_ranked:
        query_embedding = embedding_service.embed_query(query_text)
        legacy_query_embedding = generate_embedding(query_text)
        fallback_dense: list[tuple[DocumentChunk, float]] = []
        for chunk in chunks:
            vector = chunk.embedding
            active_query_vector = query_embedding
            if vector is None:
                vector = chunk.dense_embedding
                active_query_vector = legacy_query_embedding
            if vector is None:
                continue
            dot = sum(
                float(left) * float(right)
                for left, right in zip(active_query_vector, vector)
            )
            fallback_dense.append((chunk, 1.0 - dot))
        dense_ranked = sorted(fallback_dense, key=lambda item: item[1])[:30]

    if not sparse_ranked:
        sparse_ranked = _rank_sparse_bm25(query_text, chunks)

    dense_scores: dict[uuid.UUID, float] = {}
    for chunk, distance_value in dense_ranked:
        similarity = 1.0 - float(distance_value)
        dense_scores[chunk.id] = max(dense_scores.get(chunk.id, -1.0), similarity)

    sparse_scores = {
        chunk.id: float(rank_value) for chunk, rank_value in sparse_ranked
    }
    max_sparse_score = max(sparse_scores.values(), default=0.0)

    rrf_scores: dict[uuid.UUID, float] = {}
    for ranked_results in (dense_ranked, sparse_ranked):
        for rank, (chunk, _) in enumerate(ranked_results, start=1):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (60 + rank)
    if not rrf_scores:
        return []
    max_rrf = max(rrf_scores.values())
    scored_chunks: list[dict[str, Any]] = []
    for chunk_id, rrf_score in rrf_scores.items():
        chunk = chunk_by_id[chunk_id]
        chunk_metadata = chunk.metadata_ or {}
        section_title = (
            chunk.section_title
            or chunk_metadata.get("section_title")
            or f"Chunk {chunk.chunk_index}"
        )
        document_title = chunk.document_title or chunk.document_name
        source_parts = [document_title]
        if chunk.version:
            source_parts.append(f"v{chunk.version}")
        source_parts.append(section_title)
        if chunk.page is not None:
            source_parts.append(f"p. {chunk.page}")

        scored_chunks.append({
            "id": str(chunk.id),
            "tenant_id": str(chunk.tenant_id),
            "department": chunk.department_access,
            "document_type": chunk.document_type,
            "document_id": chunk.document_id or chunk.document_name,
            "document_title": document_title,
            "document_name": chunk.document_name,
            "section_title": section_title,
            "chunk_index": chunk.chunk_index,
            "section_index": chunk_metadata.get("section_index"),
            "header_path": chunk_metadata.get("header_path") or [],
            "parent_chunk_index": chunk_metadata.get("parent_chunk_index"),
            "content": chunk.content,
            "version": chunk.version,
            "effective_date": (
                chunk.effective_date.isoformat() if chunk.effective_date else None
            ),
            "expiration_date": (
                chunk.expiration_date.isoformat() if chunk.expiration_date else None
            ),
            "status": chunk.status,
            "confidentiality": chunk.confidentiality,
            "allowed_roles": chunk.allowed_roles or [],
            "source_file": chunk.source_file or chunk.document_name,
            "page": chunk.page,
            "page_start": chunk.page_start or chunk.page,
            "page_end": chunk.page_end or chunk.page,
            "content_hash": chunk.content_hash,
            "embedding_model": chunk.embedding_model or "legacy-hash-1536",
            "embedding_version": chunk.embedding_version or "legacy-v1",
            "score": round(rrf_score / max_rrf, 4),
            "_rrf_score": rrf_score / max_rrf,
            "_dense_score": dense_scores.get(chunk_id, -1.0),
            "_sparse_score": (
                sparse_scores.get(chunk_id, 0.0) / max_sparse_score
                if max_sparse_score > 0
                else 0.0
            ),
            "citation_tag": f"[Citation: {', '.join(source_parts)}; chunk={chunk.id}]",
        })

    scored_chunks.sort(key=lambda x: x["score"], reverse=True)
    # Reranking scores the child a parent_child search matched, not its parent. Every
    # candidate is kept through it because folding siblings into one parent can leave
    # fewer than top_k results.
    candidates = scored_chunks[:30]
    reranked = rerank_chunks(query_text, candidates, top_k=len(candidates))
    return _with_parent_context(reranked, chunk_by_id)[:top_k]


def ingest_document(
    db: Session,
    tenant_id: uuid.UUID,
    document_name: str,
    content: str,
    department_access: str = "ALL",
    collection_name: str = "General Knowledge",
    source_metadata: dict[str, Any] | None = None,
    document_id: str | None = None,
    document_title: str | None = None,
    document_type: str = "knowledge",
    version: str = "1.0",
    effective_date: date | None = None,
    expiration_date: date | None = None,
    status: str = "active",
    confidentiality: str = "internal",
    allowed_roles: list[str] | None = None,
    source_file: str | None = None,
    storage_key: str | None = None,
    source_hash: str | None = None,
    source_url: str | None = None,
    created_by_id: uuid.UUID | None = None,
) -> List[DocumentChunk]:
    """
    Ingests raw document text, performs header-aware semantic chunking, computes vector embeddings,
    and saves to PostgreSQL `document_chunks` table.
    """
    resolved_document_id = document_id or document_name
    resolved_document_title = document_title or document_name
    resolved_source_file = source_file or document_name
    normalized_roles = sorted({
        role.strip().lower() for role in (allowed_roles or []) if role.strip()
    })
    embedding_service = get_embedding_service()
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == tenant_id,
        KnowledgeDocument.document_id == resolved_document_id,
        KnowledgeDocument.version == version,
    ).first()
    if not record:
        record = KnowledgeDocument(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            document_id=resolved_document_id,
            file_name=resolved_source_file,
            document_title=resolved_document_title,
            department=department_access,
            document_type=document_type,
            version=version,
            created_by_id=created_by_id,
        )
        db.add(record)

    record.file_name = resolved_source_file
    record.document_title = resolved_document_title
    record.collection_name = collection_name
    record.department = department_access
    record.document_type = document_type
    record.status = status
    record.processing_status = "chunking"
    record.processing_progress = 0
    record.confidentiality = confidentiality
    record.allowed_roles = normalized_roles
    record.effective_date = effective_date
    record.expiration_date = expiration_date
    record.storage_key = storage_key or record.storage_key
    record.source_url = source_url
    record.source_hash = source_hash or calculate_content_hash(content)
    record.embedding_model = embedding_service.model_name
    record.embedding_version = embedding_service.version
    record.error_message = None
    db.commit()

    try:
        previous_chunks = db.query(DocumentChunk).filter(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id == resolved_document_id,
            DocumentChunk.embedding_model == embedding_service.model_name,
            DocumentChunk.embedding_version == embedding_service.version,
            DocumentChunk.embedding.is_not(None),
        ).all()
        reusable_vectors = {
            (chunk.content_hash, chunk.embedding_text): list(chunk.embedding)
            for chunk in previous_chunks
            if chunk.content_hash and chunk.embedding_text and chunk.embedding is not None
        }

        document_chunks = chunk_document_content(content)
        prepared_chunks: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for chunk_data in document_chunks:
            content_hash = calculate_content_hash(chunk_data["content"])
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            embedding_text = build_embedding_text({
                "department": department_access,
                "document_type": document_type,
                "document_title": resolved_document_title,
                "section_title": chunk_data["section_title"],
                "content": chunk_data["content"],
            })
            prepared_chunks.append({
                **chunk_data,
                "content_hash": content_hash,
                "embedding_text": embedding_text,
                "embedding_token_count": 0,
                "embedding": reusable_vectors.get((content_hash, embedding_text)),
            })

        if not prepared_chunks:
            raise ValueError("No readable text found in the document")

        token_counts = embedding_service.count_tokens_batch([
            chunk["embedding_text"] for chunk in prepared_chunks
        ])
        max_input_tokens = embedding_service.max_input_tokens
        for chunk_data, embedding_token_count in zip(prepared_chunks, token_counts):
            if embedding_token_count > max_input_tokens:
                raise ValueError(
                    f"Embedding input exceeds model limit: {embedding_token_count} > "
                    f"{max_input_tokens}"
                )
            chunk_data["embedding_token_count"] = embedding_token_count

        record.processing_progress = 100
        db.commit()
        record.processing_status = "embedding"
        record.processing_progress = 0
        db.commit()
        pending = [chunk for chunk in prepared_chunks if chunk["embedding"] is None]
        total_pending = len(pending)
        if total_pending == 0:
            record.processing_progress = 100
            db.commit()
        for batch_start in range(0, len(pending), embedding_service.batch_size):
            batch = pending[batch_start:batch_start + embedding_service.batch_size]
            vectors = embedding_service.embed_texts([
                chunk["embedding_text"] for chunk in batch
            ])
            for chunk_data, vector in zip(batch, vectors):
                chunk_data["embedding"] = vector
            embedded_count = min(batch_start + len(batch), total_pending)
            record.processing_progress = round((embedded_count / total_pending) * 100)
            db.commit()

        record.processing_status = "indexing"
        record.processing_progress = 0
        db.commit()

        # Serialize the short replacement transaction for this document version.
        # Embedding is deliberately completed before taking the row lock so other
        # requests and RAG reads are not blocked by model inference. If two uploads
        # race, the second transaction replaces the first batch instead of appending
        # another set of chunks.
        record = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.id == record.id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        record.file_name = resolved_source_file
        record.document_title = resolved_document_title
        record.collection_name = collection_name
        record.department = department_access
        record.document_type = document_type
        record.status = status
        record.processing_status = "indexing"
        record.processing_progress = 0
        record.confidentiality = confidentiality
        record.allowed_roles = normalized_roles
        record.effective_date = effective_date
        record.expiration_date = expiration_date
        record.storage_key = storage_key or record.storage_key
        record.source_url = source_url
        record.source_hash = source_hash or calculate_content_hash(content)
        record.embedding_model = embedding_service.model_name
        record.embedding_version = embedding_service.version
        record.error_message = None

        if status == "active":
            db.query(KnowledgeDocument).filter(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.document_id == resolved_document_id,
                KnowledgeDocument.version != version,
                KnowledgeDocument.status == "active",
            ).update({KnowledgeDocument.status: "inactive"}, synchronize_session=False)
            db.query(DocumentChunk).filter(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == resolved_document_id,
                DocumentChunk.version != version,
                DocumentChunk.status == "active",
            ).update({DocumentChunk.status: "inactive"}, synchronize_session=False)

        db.query(DocumentChunk).filter(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id == resolved_document_id,
            DocumentChunk.version == version,
        ).delete(synchronize_session=False)
        db.flush()

        chunks_created: list[DocumentChunk] = []
        for idx, chunk_data in enumerate(prepared_chunks):
            page_start = chunk_data["page"]
            page_end = max(chunk_data["pages"]) if chunk_data["pages"] else page_start
            chunk = DocumentChunk(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                knowledge_document_id=record.id,
                document_name=document_name,
                document_id=resolved_document_id,
                document_title=resolved_document_title,
                document_type=document_type,
                version=version,
                effective_date=effective_date,
                expiration_date=expiration_date,
                status=status,
                confidentiality=confidentiality,
                allowed_roles=normalized_roles,
                source_file=resolved_source_file,
                collection_name=collection_name,
                department_access=department_access,
                chunk_index=idx,
                section_title=chunk_data["section_title"],
                page=page_start,
                page_start=page_start,
                page_end=page_end,
                content=chunk_data["content"],
                embedding_text=chunk_data["embedding_text"],
                content_hash=chunk_data["content_hash"],
                embedding_model=embedding_service.model_name,
                embedding_version=embedding_service.version,
                embedding_status="embedded",
                embedding=chunk_data["embedding"],
                metadata_={
                    **(source_metadata or {}),
                    "document_id": resolved_document_id,
                    "document_title": resolved_document_title,
                    "document_type": document_type,
                    "version": version,
                    "effective_date": effective_date.isoformat() if effective_date else None,
                    "expiration_date": expiration_date.isoformat() if expiration_date else None,
                    "status": status,
                    "confidentiality": confidentiality,
                    "allowed_roles": normalized_roles,
                    "source_file": resolved_source_file,
                    "section_title": chunk_data["section_title"],
                    "section_type": chunk_data["section_type"],
                    "document_name": document_name,
                    "section_index": chunk_data["section_index"],
                    "section_chunk_index": chunk_data["section_chunk_index"],
                    "header_level": chunk_data["header_level"],
                    "header_path": chunk_data["header_path"],
                    "page_start": page_start,
                    "page_end": page_end,
                    "pages": chunk_data["pages"],
                    "token_count": chunk_data["token_count"],
                    "embedding_token_count": chunk_data["embedding_token_count"],
                    "content_hash": chunk_data["content_hash"],
                    "embedding_model": embedding_service.model_name,
                    "embedding_version": embedding_service.version,
                },
            )
            db.add(chunk)
            chunks_created.append(chunk)

        record.chunk_count = len(chunks_created)
        record.processing_checkpoint = "ready"
        record.processing_status = "ready"
        record.processing_progress = 100
        record.parsed_text = None
        db.commit()
        return chunks_created
    except Exception as exc:
        db.rollback()
        failed_record = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == record.id,
            KnowledgeDocument.tenant_id == tenant_id,
        ).first()
        if failed_record:
            failed_record.processing_status = "failed"
            failed_record.error_message = str(exc)[:2000]
            db.commit()
        raise
