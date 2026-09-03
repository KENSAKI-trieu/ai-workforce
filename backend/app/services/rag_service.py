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
from app.services.embedding_service import (
    build_embedding_text,
    calculate_content_hash,
    get_embedding_service,
)
from app.services.reranker_service import rerank_chunks
from app.services.ai_service_client import get_ai_service_client

logger = logging.getLogger(__name__)

CHUNK_MIN_TOKENS = settings.RAG_CHUNK_MIN_TOKENS
CHUNK_TARGET_TOKENS = settings.RAG_CHUNK_TARGET_TOKENS
CHUNK_SIZE_TOKENS = settings.RAG_CHUNK_MAX_TOKENS
CHUNK_OVERLAP_TOKENS = settings.RAG_CHUNK_OVERLAP_TOKENS
_TOKEN_PATTERN = re.compile(r"\S+")
_MARKDOWN_HEADER_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_PAGE_MARKER_PATTERN = re.compile(r"^\[\[PAGE:(\d+)\]\]$")
_BUSINESS_BOUNDARIES = (
    ("article", 10, re.compile(r"^(?:điều|article)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("clause", 11, re.compile(r"^(?:khoản|clause)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("step", 11, re.compile(r"^(?:bước|step)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("responsibility", 10, re.compile(r"^(?:mục\s+)?(?:trách nhiệm|responsibilit(?:y|ies))\b", re.IGNORECASE)),
    ("condition", 10, re.compile(r"^(?:mục\s+)?(?:điều kiện|conditions?)\b", re.IGNORECASE)),
    ("appendix", 10, re.compile(r"^(?:phụ\s*lục|appendix)\b", re.IGNORECASE)),
    ("section", 10, re.compile(r"^mục\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
)
_NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d+(?:\.\d+)+)[.)]?[ \t]+(?P<title>\S.*)$"
)
_TOP_LEVEL_NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d+)[.)][ \t]+(?P<title>[A-ZÀ-ỸĐ]\S*.*)$"
)


def clean_document_text(text_content: str) -> str:
    """Conservatively clean parser noise without deleting business structure."""
    cleaned = text_content.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r"(?<=\w)-\n(?=\w)", "", cleaned)
    cleaned = re.sub(
        r"(?im)^[ \t]*(?:trang[ \t]+\d+[ \t]*/[ \t]*\d+|page[ \t]+\d+[ \t]+of[ \t]+\d+)[ \t]*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _token_spans(text_content: str) -> list[tuple[int, int]]:
    """Return stable lexical-token spans without adding a tokenizer dependency."""
    return [match.span() for match in _TOKEN_PATTERN.finditer(text_content)]


def _split_token_windows(
    text_content: str,
    chunk_size: int,
    chunk_overlap: int,
    target_size: int | None = None,
) -> list[tuple[str, int, int, int]]:
    """Split text into bounded token windows while preserving original formatting."""
    spans = _token_spans(text_content)
    if not spans:
        return []

    windows: list[tuple[str, int, int, int]] = []
    target = min(target_size or chunk_size, chunk_size)
    start_token = 0

    while start_token < len(spans):
        hard_end = min(start_token + chunk_size, len(spans))
        end_token = hard_end
        desired_end = min(start_token + target, hard_end)
        if hard_end < len(spans):
            for candidate_end in range(desired_end, hard_end):
                separator = text_content[
                    spans[candidate_end - 1][1]:spans[candidate_end][0]
                ]
                previous_token = text_content[
                    spans[candidate_end - 1][0]:spans[candidate_end - 1][1]
                ]
                if "\n\n" in separator or previous_token.endswith((".", "!", "?", ":", ";")):
                    end_token = candidate_end
                    break
        start_char = spans[start_token][0]
        end_char = spans[end_token - 1][1]
        windows.append((
            text_content[start_char:end_char].strip(),
            end_token - start_token,
            start_char,
            end_char,
        ))

        if end_token == len(spans):
            break
        start_token = max(end_token - chunk_overlap, start_token + 1)

    return windows


def _classify_section_boundary(line: str) -> tuple[str, str, int] | None:
    """Classify headings and common policy/SOP business boundaries."""
    stripped = line.strip()
    if not stripped:
        return None

    markdown_header = _MARKDOWN_HEADER_PATTERN.match(stripped)
    if markdown_header:
        level = len(markdown_header.group(1))
        title = re.sub(r"[ \t]+#+[ \t]*$", "", markdown_header.group(2)).strip()
        return "heading", title, level

    numbered_heading = (
        _NUMBERED_HEADING_PATTERN.match(stripped)
        or _TOP_LEVEL_NUMBERED_HEADING_PATTERN.match(stripped)
    )
    if numbered_heading:
        number = numbered_heading.group("number")
        level = min(number.count(".") + 1, 6)
        return "numbered_heading", stripped, level

    normalized = stripped.strip("*_").strip()
    for section_type, depth, pattern in _BUSINESS_BOUNDARIES:
        if pattern.match(normalized):
            return section_type, normalized, depth
    return None


def _semantic_sections(content: str) -> list[dict[str, Any]]:
    """Split content at semantic business boundaries before applying token limits."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    sections: list[dict[str, Any]] = []
    path_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_line_pages: list[int | None] = []
    current_title = "Mở đầu"
    current_type = "preamble"
    current_level: int | None = None
    current_path: list[str] = []
    current_page: int | None = None

    def flush_section() -> None:
        first_content_line = next(
            (index for index, line in enumerate(current_lines) if line.strip()),
            len(current_lines),
        )
        selected_lines = current_lines[first_content_line:]
        selected_pages = current_line_pages[first_content_line:]
        while selected_lines and not selected_lines[-1].strip():
            selected_lines.pop()
            selected_pages.pop()
        section_content = "\n".join(selected_lines)
        if not section_content:
            return
        page_offsets: list[tuple[int, int]] = []
        char_offset = 0
        previous_page: int | None = None
        for line, line_page in zip(selected_lines, selected_pages):
            if line_page is not None and line_page != previous_page:
                page_offsets.append((char_offset, line_page))
                previous_page = line_page
            char_offset += len(line) + 1
        pages = sorted({page for _, page in page_offsets})
        sections.append({
            "content": section_content,
            "section_title": current_title,
            "section_type": current_type,
            "header_level": current_level,
            "header_path": current_path,
            "page": pages[0] if pages else None,
            "pages": pages,
            "page_offsets": page_offsets,
        })

    for line in normalized.split("\n"):
        page_marker = _PAGE_MARKER_PATTERN.match(line.strip())
        if page_marker:
            current_page = int(page_marker.group(1))
            continue

        boundary = _classify_section_boundary(line)
        if boundary:
            flush_section()
            current_lines = []
            current_line_pages = []
            section_type, title, depth = boundary
            path_stack = [item for item in path_stack if item[0] < depth]
            path_stack.append((depth, title))
            current_title = title
            current_type = section_type
            current_level = (
                depth if section_type in {"heading", "numbered_heading"} else None
            )
            current_path = [path_title for _, path_title in path_stack]

        current_lines.append(line)
        current_line_pages.append(current_page)

    flush_section()

    return sections


def chunk_document_content(
    content: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    chunk_overlap: int = CHUNK_OVERLAP_TOKENS,
    progress_callback: Callable[[dict[str, int]], None] | None = None,
    progress_stream_id: str | None = None,
    progress_completed_before: int = 0,
    progress_total_count: int | None = None,
    progress_chunks_before: int = 0,
) -> list[dict[str, Any]]:
    """
    Create header-aware chunks bounded by lexical-token count.

    Heading, article, clause, process-step, responsibility and condition sections
    are never merged. Only sections larger than ``chunk_size`` are split into
    sliding windows with ``chunk_overlap`` shared tokens.
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
            on_progress=progress_callback,
            **progress_options,
        )

    chunks: list[dict[str, Any]] = []
    cleaned_content = clean_document_text(content)
    sections = _semantic_sections(cleaned_content)
    planned_segments: list[tuple[int, dict[str, Any], int, tuple[str, int, int, int]]] = []
    for section_index, section in enumerate(sections):
        windows = _split_token_windows(
            section["content"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            target_size=min(CHUNK_TARGET_TOKENS, chunk_size),
        )
        planned_segments.extend(
            (section_index, section, section_chunk_index, window)
            for section_chunk_index, window in enumerate(windows)
        )

    total_segments = len(planned_segments)
    for segment_index, (
        section_index,
        section,
        section_chunk_index,
        window,
    ) in enumerate(planned_segments):
        chunk_content, token_count, start_char, end_char = window
        window_pages: list[int] = []
        page_offsets = section["page_offsets"]
        for offset_index, (page_start, page_number) in enumerate(page_offsets):
            page_end = (
                page_offsets[offset_index + 1][0]
                if offset_index + 1 < len(page_offsets)
                else len(section["content"])
            )
            if page_start < end_char and page_end > start_char:
                window_pages.append(page_number)
        chunks.append({
            "content": chunk_content,
            "section_title": section["section_title"],
            "section_type": section["section_type"],
            "section_index": section_index,
            "section_chunk_index": section_chunk_index,
            "header_level": section["header_level"],
            "header_path": section["header_path"],
            "page": window_pages[0] if window_pages else section["page"],
            "pages": window_pages or section["pages"],
            "token_count": token_count,
        })
        if progress_callback is not None:
            processed_segments = segment_index + 1
            progress_callback({
                "processed_segments": processed_segments,
                "total_segments": total_segments,
                "remaining_segments": total_segments - processed_segments,
                "chunks_created": len(chunks),
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
) -> List[Dict[str, Any]]:
    """
    Hybrid Search combining Dense Vector Cosine Similarity and Sparse Keyword Matching.
    Calculates RRF (Reciprocal Rank Fusion) scores and returns Top-K relevant document chunks with metadata.
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
    if agent_access is not None:
        selectors = {str(value).strip() for value in agent_access if str(value).strip()}
        if "none" in selectors or (not selectors):
            query = query.filter(false())
        elif "*" not in selectors:
            access_filters = []
            for selector in selectors:
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
    privileged_roles = {"owner", "admin", "ceo"}
    if normalized_role not in privileged_roles:
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
        section_title = (
            chunk.section_title
            or (
                chunk.metadata_.get("section_title")
                if chunk.metadata_
                else None
            )
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
    return rerank_chunks(query_text, scored_chunks[:30], top_k=top_k)


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
