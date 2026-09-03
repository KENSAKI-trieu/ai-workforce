import re
from collections.abc import Iterator
from typing import Any

from app.config import settings
from app.rag.ingestion.cleaner import clean_document_text


_TOKEN_PATTERN = re.compile(r"\S+")
_MARKDOWN_HEADER_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_PAGE_MARKER_PATTERN = re.compile(r"^\[\[PAGE:(\d+)\]\]$")
_NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d+(?:\.\d+)+)[.)]?[ \t]+(?P<title>\S.*)$"
)
_TOP_LEVEL_NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d+)[.)][ \t]+(?P<title>[A-ZÀ-ỸĐ]\S*.*)$"
)
_BUSINESS_BOUNDARIES = (
    ("article", 10, re.compile(r"^(?:điều|article)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("clause", 11, re.compile(r"^(?:khoản|clause)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("step", 11, re.compile(r"^(?:bước|step)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("responsibility", 10, re.compile(r"^(?:mục\s+)?(?:trách nhiệm|responsibilit(?:y|ies))\b", re.IGNORECASE)),
    ("condition", 10, re.compile(r"^(?:mục\s+)?(?:điều kiện|conditions?)\b", re.IGNORECASE)),
    ("appendix", 10, re.compile(r"^(?:phụ\s*lục|appendix)\b", re.IGNORECASE)),
    ("section", 10, re.compile(r"^mục\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
)


def _token_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _TOKEN_PATTERN.finditer(text)]


def _split_token_windows(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    target_size: int,
) -> list[tuple[str, int, int, int]]:
    spans = _token_spans(text)
    if not spans:
        return []
    windows: list[tuple[str, int, int, int]] = []
    target = min(target_size, chunk_size)
    start_token = 0
    while start_token < len(spans):
        hard_end = min(start_token + chunk_size, len(spans))
        end_token = hard_end
        desired_end = min(start_token + target, hard_end)
        if hard_end < len(spans):
            for candidate_end in range(desired_end, hard_end):
                separator = text[spans[candidate_end - 1][1]:spans[candidate_end][0]]
                previous = text[spans[candidate_end - 1][0]:spans[candidate_end - 1][1]]
                if "\n\n" in separator or previous.endswith((".", "!", "?", ":", ";")):
                    end_token = candidate_end
                    break
        start_char = spans[start_token][0]
        end_char = spans[end_token - 1][1]
        windows.append((text[start_char:end_char].strip(), end_token - start_token, start_char, end_char))
        if end_token == len(spans):
            break
        start_token = max(end_token - chunk_overlap, start_token + 1)
    return windows


def _classify_boundary(line: str) -> tuple[str, str, int] | None:
    stripped = line.strip()
    if not stripped:
        return None
    markdown = _MARKDOWN_HEADER_PATTERN.match(stripped)
    if markdown:
        level = len(markdown.group(1))
        title = re.sub(r"[ \t]+#+[ \t]*$", "", markdown.group(2)).strip()
        return "heading", title, level
    numbered = _NUMBERED_HEADING_PATTERN.match(stripped) or _TOP_LEVEL_NUMBERED_HEADING_PATTERN.match(stripped)
    if numbered:
        level = min(numbered.group("number").count(".") + 1, 6)
        return "numbered_heading", stripped, level
    normalized = stripped.strip("*_").strip()
    for section_type, depth, pattern in _BUSINESS_BOUNDARIES:
        if pattern.match(normalized):
            return section_type, normalized, depth
    return None


def _semantic_sections(content: str) -> list[dict[str, Any]]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    sections: list[dict[str, Any]] = []
    path_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_pages: list[int | None] = []
    current_title = "Mở đầu"
    current_type = "preamble"
    current_level: int | None = None
    current_path: list[str] = []
    current_page: int | None = None

    def flush() -> None:
        first = next((index for index, line in enumerate(current_lines) if line.strip()), len(current_lines))
        selected_lines = current_lines[first:]
        selected_pages = current_pages[first:]
        while selected_lines and not selected_lines[-1].strip():
            selected_lines.pop()
            selected_pages.pop()
        section_content = "\n".join(selected_lines)
        if not section_content:
            return
        page_offsets: list[tuple[int, int]] = []
        offset = 0
        previous_page: int | None = None
        for line, page in zip(selected_lines, selected_pages):
            if page is not None and page != previous_page:
                page_offsets.append((offset, page))
                previous_page = page
            offset += len(line) + 1
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
        boundary = _classify_boundary(line)
        if boundary:
            flush()
            current_lines = []
            current_pages = []
            section_type, title, depth = boundary
            path_stack = [item for item in path_stack if item[0] < depth]
            path_stack.append((depth, title))
            current_title = title
            current_type = section_type
            current_level = depth if section_type in {"heading", "numbered_heading"} else None
            current_path = [path_title for _, path_title in path_stack]
        current_lines.append(line)
        current_pages.append(current_page)
    flush()
    return sections


def iter_chunk_document(
    content: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield chunks and exact section counters after every processed section."""
    size = chunk_size or settings.RAG_CHUNK_MAX_TOKENS
    overlap = settings.RAG_CHUNK_OVERLAP_TOKENS if chunk_overlap is None else chunk_overlap
    if size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be between zero and chunk_size - 1")

    planned_segments: list[tuple[int, dict[str, Any], int, tuple[str, int, int, int]]] = []
    sections = _semantic_sections(clean_document_text(content))
    for section_index, section in enumerate(sections):
        windows = _split_token_windows(
            section["content"],
            chunk_size=size,
            chunk_overlap=overlap,
            target_size=min(settings.RAG_CHUNK_TARGET_TOKENS, size),
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
        text, token_count, start_char, end_char = window
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
        chunk = {
            "content": text,
            "section_title": section["section_title"],
            "section_type": section["section_type"],
            "section_index": section_index,
            "section_chunk_index": section_chunk_index,
            "header_level": section["header_level"],
            "header_path": section["header_path"],
            "page": window_pages[0] if window_pages else section["page"],
            "pages": window_pages or section["pages"],
            "token_count": token_count,
        }
        processed_segments = segment_index + 1
        yield {
            "processed_segments": processed_segments,
            "total_segments": total_segments,
            "remaining_segments": total_segments - processed_segments,
            "chunks_created": processed_segments,
            "chunks": [chunk],
        }


def chunk_document(
    content: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for progress in iter_chunk_document(
        content,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    ):
        chunks.extend(progress["chunks"])
    return chunks
