"""Header-aware chunking for the knowledge index.

This file exists twice, byte for byte:

* ``apps/ai-service/app/services/chunking/rag_chunking.py`` is what production runs;
* ``backend/app/domains/knowledge/rag_chunking.py`` is the backend's fallback when no AI
  service is configured, which is also what every backend test exercises.

The two services ship as separate images, so neither can import the other. The copies
used to be two hand-written versions that drifted apart; now a test in each suite fails
as soon as they differ. Edit one and copy it over. It imports nothing from either
application: every size comes in as an argument.

Sizes are in estimated model tokens (see ``estimate_tokens``), not in words.
"""

from __future__ import annotations

import math
import re
from typing import Any

_WORD = re.compile(r"\S+")
_MARKDOWN_HEADER = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_PAGE_MARKER = re.compile(r"^\[\[PAGE:(\d+)\]\]$")
_NUMBERED_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+)+)[.)]?[ \t]+(?P<title>\S.*)$")
_TOP_LEVEL_NUMBERED_HEADING = re.compile(r"^(?P<number>\d+)[.)][ \t]+(?P<title>[A-ZÀ-ỸĐ]\S*.*)$")
# (section type, nesting depth, pattern). Markdown headings nest at depth 1-6.
_BUSINESS_BOUNDARIES = (
    ("article", 10, re.compile(r"^(?:điều|article)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("clause", 11, re.compile(r"^(?:khoản|clause)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("step", 11, re.compile(r"^(?:bước|step)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
    ("responsibility", 10, re.compile(r"^(?:mục\s+)?(?:trách nhiệm|responsibilit(?:y|ies))\b", re.IGNORECASE)),
    ("condition", 10, re.compile(r"^(?:mục\s+)?(?:điều kiện|conditions?)\b", re.IGNORECASE)),
    ("appendix", 10, re.compile(r"^(?:phụ\s*lục|appendix)\b", re.IGNORECASE)),
    ("section", 10, re.compile(r"^mục\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)),
)
# "1." and "3.1." nest below every heading and article: under "Điều 5" a "1. ..." is one
# of its points. At depth 1-2 it used to close the article and drop it from the path.
_NUMBERED_DEPTH = 20
_SENTENCE_END = (".", "!", "?", ":", ";")


def estimate_tokens(word: str) -> int:
    """The model tokens one whitespace-separated word costs, from its length.

    Measured on the Vietnamese knowledge base against Gemini's own count (2026-09-29):
    1.34-1.51 tokens per word, 3.2-3.4 characters per token. A token per three
    characters, rounded up for each word, lands 7-14% over: the safe side for a limit.
    Long English words come out higher than they are.
    """
    return max(1, math.ceil(len(word) / 3))


def estimate_text_tokens(text: str) -> int:
    return sum(estimate_tokens(word) for word in _WORD.findall(text))


def clean_document_text(text: str) -> str:
    """Remove parser noise while preserving headings and business structure."""
    cleaned = text.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
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


def _split_windows(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    target_size: int,
) -> list[tuple[str, int, int, int]]:
    """(text, tokens, start char, end char) windows of at most ``chunk_size`` tokens.

    A window grows to ``target_size`` and then ends at the first paragraph or sentence
    end before ``chunk_size``. The next one starts ``chunk_overlap`` tokens back. A single
    word longer than ``chunk_size`` still makes a window of its own.
    """
    spans = [match.span() for match in _WORD.finditer(text)]
    if not spans:
        return []
    costs = [estimate_tokens(text[start:end]) for start, end in spans]
    windows: list[tuple[str, int, int, int]] = []
    start = 0
    while start < len(spans):
        total, hard_end, desired_end = 0, start, None
        while hard_end < len(spans) and (hard_end == start or total + costs[hard_end] <= chunk_size):
            total += costs[hard_end]
            hard_end += 1
            if desired_end is None and total >= target_size:
                desired_end = hard_end
        end = hard_end
        if hard_end < len(spans):
            for candidate in range(desired_end or hard_end, hard_end):
                separator = text[spans[candidate - 1][1]:spans[candidate][0]]
                previous = text[spans[candidate - 1][0]:spans[candidate - 1][1]]
                if "\n\n" in separator or previous.endswith(_SENTENCE_END):
                    end = candidate
                    break
        start_char, end_char = spans[start][0], spans[end - 1][1]
        windows.append((text[start_char:end_char].strip(), sum(costs[start:end]), start_char, end_char))
        if end == len(spans):
            break
        next_start, overlap = end, 0
        while next_start > start + 1 and overlap + costs[next_start - 1] <= chunk_overlap:
            next_start -= 1
            overlap += costs[next_start]
        start = next_start
    return windows


def _classify_boundary(line: str) -> tuple[str, str, int, int | None] | None:
    """(section type, title, nesting depth, heading level) when the line opens a section."""
    stripped = line.strip()
    if not stripped:
        return None
    markdown = _MARKDOWN_HEADER.match(stripped)
    if markdown:
        level = len(markdown.group(1))
        title = re.sub(r"[ \t]+#+[ \t]*$", "", markdown.group(2)).strip()
        return "heading", title, level, level
    numbered = _NUMBERED_HEADING.match(stripped) or _TOP_LEVEL_NUMBERED_HEADING.match(stripped)
    if numbered:
        level = min(numbered.group("number").count(".") + 1, 6)
        return "numbered_heading", stripped, _NUMBERED_DEPTH + level, level
    normalized = stripped.strip("*_").strip()
    for section_type, depth, pattern in _BUSINESS_BOUNDARIES:
        if pattern.match(normalized):
            return section_type, normalized, depth, None
    return None


def _semantic_sections(content: str) -> list[dict[str, Any]]:
    """Split at headings and business boundaries, tracking the heading path and pages."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    sections: list[dict[str, Any]] = []
    # (depth, title, section type, heading level) of every open heading, outermost first.
    stack: list[tuple[int, str, str, int | None]] = []
    current_lines: list[str] = []
    current_pages: list[int | None] = []
    current_nodes: list[tuple[str, str, int | None]] = []
    current_title, current_type, current_level = "Mở đầu", "preamble", None
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
            "header_path": [title for title, _, _ in current_nodes],
            "path_nodes": list(current_nodes),
            "page": pages[0] if pages else None,
            "pages": pages,
            "page_offsets": page_offsets,
        })

    for line in normalized.split("\n"):
        page_marker = _PAGE_MARKER.match(line.strip())
        if page_marker:
            current_page = int(page_marker.group(1))
            continue
        boundary = _classify_boundary(line)
        if boundary:
            flush()
            current_lines, current_pages = [], []
            section_type, title, depth, level = boundary
            stack = [item for item in stack if item[0] < depth]
            stack.append((depth, title, section_type, level))
            current_title, current_type, current_level = title, section_type, level
            current_nodes = [(item[1], item[2], item[3]) for item in stack]
        current_lines.append(line)
        current_pages.append(current_page)
    flush()
    return sections


def _within(nodes: list[tuple[str, str, int | None]], scope: list[tuple[str, str, int | None]]) -> bool:
    """Whether a heading path lies under ``scope``: deeper, or the scope heading itself."""
    return bool(scope) and len(nodes) >= len(scope) and nodes[:len(scope)] == scope


def _joins(group: dict[str, Any], section: dict[str, Any], *, min_size: int, max_size: int) -> bool:
    """Whether ``section`` goes into the chunk ``group`` is building.

    Only while the group is under ``min_size``, when both fit in ``max_size`` together, and
    when the section sits under the group's heading or is the next item under the same
    parent: a bare article title with its points, or two short points of one article.
    Never across the top level, so the text before the first heading stays apart.
    """
    if group["tokens"] >= min_size or group["tokens"] + section["tokens"] > max_size:
        return False
    scope, nodes = group["scope"], section["path_nodes"]
    if len(nodes) > len(scope) and _within(nodes, scope):
        return True
    return len(scope) >= 2 and len(nodes) == len(scope) and nodes[:-1] == scope[:-1]


def _append(group: dict[str, Any], part: dict[str, Any], scope: list[tuple[str, str, int | None]]) -> None:
    """Put ``part`` at the end of ``group``, which now spans ``scope``."""
    offset = len(group["content"]) + 2
    group["content"] = f"{group['content']}\n\n{part['content']}"
    for page_offset, page in part["page_offsets"]:
        if not group["page_offsets"] or group["page_offsets"][-1][1] != page:
            group["page_offsets"].append((offset + page_offset, page))
    group["pages"] = sorted(set(group["pages"]) | set(part["pages"]))
    group["page"] = group["page"] if group["page"] is not None else part["page"]
    group["tokens"] += part["tokens"]
    group["members"] += part["members"]
    group["scope"] = scope


def _title_merged(group: dict[str, Any]) -> None:
    """Name a merged chunk: its heading when it opens with that heading, else its parts.

    "Điều 5" followed by its points is still "Điều 5". Two points of one article, or two
    numbered sections of a document, are named after both ("3. Nghỉ phép năm; 4. Thời
    hạn gửi yêu cầu") rather than after the parent they share, which a citation would
    show as just the document title.
    """
    scope, members = group["scope"], group["members"]
    if len(members) == 1:
        return
    if members[0] == scope:
        title, section_type, level = scope[-1]
    else:
        parts = list(dict.fromkeys(nodes[len(scope)] for nodes in members if len(nodes) > len(scope)))
        title = "; ".join(part[0] for part in parts)
        _, section_type, level = parts[0]
    group.update(
        section_title=title,
        section_type=section_type,
        header_level=level,
        header_path=[node[0] for node in scope],
    )


def _merge_small_sections(sections: list[dict[str, Any]], *, min_size: int, max_size: int) -> list[dict[str, Any]]:
    """Fold sections under ``min_size`` into their neighbours under the same heading.

    One chunk per "Khoản" or "Bước" left chunks of a line or two, too little for a search
    to match and too little context for an answer. A chunk takes in the sections after it
    only until it reaches ``min_size``, so short points pair up rather than piling into one
    chunk of many topics; a last short group then folds back into the chunk before it
    when that chunk's heading holds it. A merged chunk is titled after the heading all its
    parts share.
    """
    if min_size <= 0:
        return sections
    grouped: list[dict[str, Any]] = []
    for section in sections:
        section = {
            **section,
            "page_offsets": list(section["page_offsets"]),
            "tokens": estimate_text_tokens(section["content"]),
            "scope": list(section["path_nodes"]),
            "members": [list(section["path_nodes"])],
        }
        if not grouped or not _joins(grouped[-1], section, min_size=min_size, max_size=max_size):
            grouped.append(section)
            continue
        scope = grouped[-1]["scope"]
        nodes = section["path_nodes"]
        # A sibling rather than a subsection: the chunk now spans their parent.
        _append(grouped[-1], section, scope if len(nodes) > len(scope) and _within(nodes, scope) else scope[:-1])

    merged: list[dict[str, Any]] = []
    for group in grouped:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and group["tokens"] < min_size
            and previous["tokens"] + group["tokens"] <= max_size
            and _within(group["scope"], previous["scope"])
        ):
            _append(previous, group, previous["scope"])
            continue
        merged.append(group)
    for group in merged:
        _title_merged(group)
    return merged


def chunk_text(
    content: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    target_size: int,
    min_size: int,
) -> list[dict[str, Any]]:
    """Chunks of ``content`` in document order, each with its heading path and pages.

    Sections are cut at headings and business boundaries first. Sections under
    ``min_size`` tokens are merged with their neighbours under the same heading, up to
    ``target_size``; sections over ``chunk_size`` are cut into windows that overlap by
    ``chunk_overlap``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be between zero and chunk_size - 1")
    target = max(1, min(target_size, chunk_size))
    sections = _merge_small_sections(
        _semantic_sections(clean_document_text(content)),
        min_size=min(min_size, target),
        max_size=target,
    )
    chunks: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        windows = _split_windows(
            section["content"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            target_size=target,
        )
        page_offsets = section["page_offsets"]
        for section_chunk_index, (text, token_count, start_char, end_char) in enumerate(windows):
            window_pages: list[int] = []
            for offset_index, (page_start, page_number) in enumerate(page_offsets):
                page_end = (
                    page_offsets[offset_index + 1][0]
                    if offset_index + 1 < len(page_offsets)
                    else len(section["content"])
                )
                if page_start < end_char and page_end > start_char:
                    window_pages.append(page_number)
            chunks.append({
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
            })
    return chunks
