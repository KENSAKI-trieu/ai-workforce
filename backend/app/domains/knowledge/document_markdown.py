"""PDF and DOCX into Markdown, the text shape the contract pipeline and the knowledge index read.

``extract_file_text`` gives plain text, and lost the structure both need from real files:

* DOCX: every table was appended after the last paragraph, so a price table in Điều 3
  surfaced after Điều 12; and a clause numbered by Word ("Điều %1." in the numbering
  definitions) came out with no number at all, so the clause splitter found no articles.
* PDF: text came line by line as the page wraps it, running headers, footers and page
  numbers repeated between clauses, and a sentence broken over two lines or two pages
  was two sentences.

Here a DOCX is walked in body order, with Word's numbering rebuilt and tables written as
Markdown tables; a PDF is read with its layout, so its tables come back as tables, its
bullets as a list, its lines rejoined into paragraphs and its repeated page furniture
dropped. Both mark parts, chapters and articles as headings, so the structure is in the
text rather than in the layout. A PDF with no text layer (a scan) comes out empty.

Checked on real files: a five-page project brief in PDF (five tables, bullets, a running
footer) and the consolidated Labour Code in DOCX (220 articles, all numbered in order).
"""

from __future__ import annotations

import io
import re
from collections import Counter
from typing import Any

from app.domains.knowledge.document_parser import DocumentParseError, extract_file_text

# -------------------------------------------------------------------------- structure

_PART = re.compile(r"^(?:chương|phần|part|chapter)\s+[\dIVXLC]+\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:điều|article|clause|section)\s+\d+(?:\.\d+)*\b", re.IGNORECASE)
# Starts a new block even inside a paragraph: an article, a numbered point, a lettered
# point, a bullet, or the party block ("Bên A: ...").
_BLOCK_START = re.compile(
    r"^(?:(?:điều|chương|phần|mục|article|chapter|section|clause|part)\s+[\dIVXLC]+"
    r"|\d+(?:\.\d+)*[.)]\s|[a-zđ][.)]\s|[-•+*–]\s|(?:bên|party)\s+[a-zđ]\b)",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.:;!?…)\]\"”]$")
# A numbered section in capitals, "1. NHU CẦU", is a heading like a chapter.
_NUMBERED = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")


def _is_caps_title(line: str) -> bool:
    letters = [char for char in line if char.isalpha()]
    return bool(letters) and len(line.split()) <= 14 and all(char.isupper() for char in letters)


def _awaits_title(blocks: list[str]) -> bool:
    """Whether a capitalised line could still be the document's title: none yet, and near
    the top -- a statute opens with its issuing body and number in a table first."""
    return len(blocks) < 6 and not any(block.startswith("# ") for block in blocks)


def _heading(line: str, *, first: bool) -> str:
    """The line as a Markdown heading when it is one of the contract's structural lines."""
    if _PART.match(line) or (_NUMBERED.match(line) and _is_caps_title(line)):
        return f"## {line}"
    if _ARTICLE.match(line):
        return f"### {line}"
    if first and _is_caps_title(line):
        return f"# {line}"
    return line


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().replace("|", "\\|")


def _table(rows: list[list[str]]) -> str:
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


# -------------------------------------------------------------------------- DOCX

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _roman(number: int) -> str:
    values = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
              (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
    out = ""
    for value, letters in values:
        while number >= value:
            out, number = out + letters, number - value
    return out


def _letters(number: int) -> str:
    out = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def _format_number(value: int, fmt: str) -> str:
    return {
        "lowerLetter": _letters(value),
        "upperLetter": _letters(value).upper(),
        "lowerRoman": _roman(value),
        "upperRoman": _roman(value).upper(),
        "none": "",
    }.get(fmt, str(value))


class _Numbering:
    """Word's automatic numbering, counted in document order as Word would show it."""

    def __init__(self, document: Any) -> None:
        self.levels: dict[str, dict[int, dict[str, Any]]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        abstract: dict[str, dict[int, dict[str, Any]]] = {}
        for node in root.findall(f"{_W}abstractNum"):
            levels = {}
            for level in node.findall(f"{_W}lvl"):
                def value(tag: str, default: str) -> str:
                    child = level.find(f"{_W}{tag}")
                    return child.get(f"{_W}val", default) if child is not None else default
                levels[int(level.get(f"{_W}ilvl", "0"))] = {
                    "start": int(value("start", "1") or 1),
                    "fmt": value("numFmt", "decimal"),
                    "text": value("lvlText", ""),
                }
            abstract[node.get(f"{_W}abstractNumId", "")] = levels
        for node in root.findall(f"{_W}num"):
            reference = node.find(f"{_W}abstractNumId")
            if reference is not None:
                self.levels[node.get(f"{_W}numId", "")] = abstract.get(reference.get(f"{_W}val", ""), {})

    @staticmethod
    def _num_pr(paragraph: Any) -> tuple[str, int] | None:
        """The paragraph's numbering, from itself or else from its style chain."""
        sources = [paragraph._p.pPr]
        style = paragraph.style
        while style is not None:
            sources.append(style.element.pPr)
            style = style.base_style
        num_id = level = None
        for p_pr in sources:
            num_pr = p_pr.find(f"{_W}numPr") if p_pr is not None else None
            if num_pr is None:
                continue
            if num_id is None and num_pr.find(f"{_W}numId") is not None:
                num_id = num_pr.find(f"{_W}numId").get(f"{_W}val")
            if level is None and num_pr.find(f"{_W}ilvl") is not None:
                level = int(num_pr.find(f"{_W}ilvl").get(f"{_W}val", "0"))
        if num_id in (None, "0"):
            return None
        return num_id, level or 0

    def label(self, paragraph: Any) -> str:
        found = self._num_pr(paragraph)
        if found is None:
            return ""
        num_id, level = found
        levels = self.levels.get(num_id) or {}
        definition = levels.get(level)
        if definition is None:
            return ""
        counters = self.counters.setdefault(num_id, {})
        counters[level] = counters.get(level, definition["start"] - 1) + 1
        for deeper in [key for key in counters if key > level]:
            del counters[deeper]  # a new item at this level restarts every level under it
        if definition["fmt"] == "bullet":
            return "- "
        text = definition["text"]
        for index in range(level, -1, -1):
            own = levels.get(index) or {"start": 1, "fmt": "decimal"}
            value = counters.get(index, own["start"])
            text = text.replace(f"%{index + 1}", _format_number(value, own["fmt"]))
        return f"{text.strip()} " if text.strip() else ""


def _docx_heading_level(paragraph: Any) -> int | None:
    name = (getattr(paragraph.style, "name", "") or "").lower()
    if name == "title":
        return 1
    match = re.match(r"heading\s*(\d+)", name)
    return min(int(match.group(1)), 6) if match else None


def docx_to_markdown(data: bytes) -> str:
    try:
        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise DocumentParseError("DOCX parser is not installed") from exc
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, ValueError, KeyError) as exc:
        raise DocumentParseError("Invalid DOCX file") from exc
    numbering = _Numbering(document)
    blocks: list[str] = []
    for element in document.element.body.iterchildren():
        if element.tag == f"{_W}tbl":
            table = Table(element, document)
            rows: list[list[str]] = []
            for row in table.rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue  # a horizontally merged cell is listed once per column
                    seen.add(id(cell._tc))
                    cells.append(_cell(cell.text))
                rows.append(cells)
            if markdown := _table(rows):
                blocks.append(markdown)
            continue
        if element.tag != f"{_W}p":
            continue
        paragraph = Paragraph(element, document)
        text = paragraph.text.strip()
        if not text:
            continue
        text = numbering.label(paragraph) + text
        level = _docx_heading_level(paragraph)
        if level is not None:
            blocks.append(f"{'#' * level} {text}")
        else:
            # Soft line breaks stay lines: a party block is "Bên A: ...", then its address.
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            lines[0] = _heading(lines[0], first=_awaits_title(blocks))
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# -------------------------------------------------------------------------- PDF
#
# Read in pypdf's layout mode, which keeps each line's horizontal position: a table's
# columns stay apart as runs of spaces, rows sit between blank lines, and a bullet keeps
# its glyph. The plain mode ran every table cell together, one cell per line.

_PAGE_NUMBER = re.compile(r"^(?:[-–—]?\s*\d{1,4}\s*[-–—]?|(?:trang|page)\s+\d+(?:\s*(?:/|of|trên)\s*\d+)?|\d+\s*/\s*\d+)$", re.IGNORECASE)
_EDGE = 2  # lines at the top and bottom of a page that can be header or footer
# Bullet glyphs as PDF fonts deliver them; a Symbol-font bullet can arrive as DEL (0x7f)
# or in the private-use area.
_BULLETS = "\x7f•●▪■◦○∙·"
# Text runs on one line apart by four spaces or more are separate columns.
_SEGMENT = re.compile(r"\S+(?: {1,3}\S+)*")
_COLUMN_SLACK = 4


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line).strip())


def _is_bullet(stripped: str) -> bool:
    return bool(stripped) and stripped[0] in _BULLETS and (len(stripped) == 1 or stripped[1].isspace())


def _segments(line: str) -> list[tuple[int, str]]:
    return [(match.start(), match.group()) for match in _SEGMENT.finditer(line)]


_PAGE_BREAK = "\f"


def _page_lines(pages: list[str]) -> list[str]:
    """Every page's lines, headers, footers and page numbers dropped, pages apart by a break.

    The break is not a blank line: a sentence the bottom margin cut off goes on at the top
    of the next page, and the blank space around a header is not the end of a paragraph.
    """
    split = [[line.rstrip() for line in page.splitlines()] for page in pages]
    edges: list[list[int]] = []
    for lines in split:
        filled = [index for index, line in enumerate(lines) if line.strip()]
        edges.append(filled[:_EDGE] + filled[-_EDGE:])
    counts: Counter[str] = Counter()
    if len(split) >= 3:
        for lines, indexes in zip(split, edges):
            counts.update({_norm(lines[index]) for index in indexes})
    furniture = {line for line, count in counts.items() if count >= max(3, 0.6 * len(split))}
    out: list[str] = []
    for lines, indexes in zip(split, edges):
        for index in indexes:
            if _PAGE_NUMBER.match(lines[index].strip()) or _norm(lines[index]) in furniture:
                lines[index] = ""
        filled = [index for index, line in enumerate(lines) if line.strip()]
        if filled:
            out.extend(lines[filled[0]:filled[-1] + 1])
        out.append(_PAGE_BREAK)
    return out


def _tabular(line: str) -> list[tuple[int, str]] | None:
    segments = _segments(line)
    if len(segments) < 2 or _is_bullet(line.strip()):
        return None
    return segments


def _read_table(lines: list[str], start: int) -> tuple[list[list[str]], int] | None:
    """The table whose header is lines[start], and the index after it; None if it is none.

    A row starts after a blank line with text in two columns or more; a line of one
    column right under a row is that row's cell running on. Anything out of line with the
    header's columns ends the table.
    """
    columns = [offset for offset, _ in _tabular(lines[start]) or []]

    def column(offset: int) -> int | None:
        distance, index = min((abs(offset - column_start), index) for index, column_start in enumerate(columns))
        return index if distance <= _COLUMN_SLACK else None

    rows = [[""] * len(columns)]
    for offset, text in _segments(lines[start]):
        rows[0][column(offset)] = text
    index, blank_before, end = start + 1, False, start + 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            blank_before, index = True, index + 1
            continue
        segments = _segments(line)
        placed = [column(offset) for offset, _ in segments]
        if None in placed or _is_bullet(line.strip()):
            break
        if len(segments) >= 2:
            rows.append([""] * len(columns))
        elif blank_before:
            break  # one column after a gap is the text after the table
        for slot, (_, text) in zip(placed, segments):
            rows[-1][slot] = f"{rows[-1][slot]} {text}".strip()
        index, blank_before, end = index + 1, False, index + 1
    return (rows, end) if len(rows) >= 2 else None


def pdf_pages_to_markdown(pages: list[str], *, page_markers: bool = False) -> str:
    """Markdown from each page's text as pypdf's layout mode gives it.

    With ``page_markers`` a ``[[PAGE:n]]`` line goes before the first block of each page,
    so the knowledge index can still cite a page. A block the page break cut in two counts
    as the page it starts on.
    """
    lines = _page_lines(pages)
    # A wrapped line runs to the right margin; a heading or a paragraph's last line stops
    # short of it. Only a wrapped line continues onto the next.
    ends = sorted(len(line) for line in lines if line.strip() and not _tabular(line))
    if not ends:
        return ""
    margin = ends[int(0.9 * (len(ends) - 1))]
    blocks: list[str] = []
    block_pages: list[int] = []
    current: dict[str, Any] | None = None
    page = 1

    def flush() -> None:
        nonlocal current
        if current is not None:
            text = current["text"]
            blocks.append(text if current["bullet"] else _heading(text, first=_awaits_title(blocks)))
            block_pages.append(current["page"])
            current = None

    index, blank_before = 0, False
    while index < len(lines):
        line = lines[index]
        if line == _PAGE_BREAK:
            page, index = page + 1, index + 1
            continue
        stripped = line.strip()
        if not stripped:
            blank_before, index = True, index + 1
            continue
        if _tabular(line) and (table := _read_table(lines, index)):
            flush()
            rows, end = table
            blocks.append(_table([[_cell(text) for text in row] for row in rows]))
            block_pages.append(page)
            # A table can run over a page break; the rows read past it.
            page += lines[index:end].count(_PAGE_BREAK)
            index, blank_before = end, True
            continue
        indent = len(line) - len(stripped)
        bullet = _is_bullet(stripped)
        text = stripped[1:].strip() if bullet else re.sub(r" {2,}", " ", stripped)
        text_indent = len(line) - len(line.lstrip(_BULLETS + " ")) if bullet else indent
        joins = (
            current is not None
            and not blank_before
            and not bullet
            and not _BLOCK_START.match(text)
            and not _is_caps_title(text)
            and indent >= current["indent"] - 1
            and current["end"] >= 0.75 * margin
            and not _SENTENCE_END.search(current["last"])
        )
        if joins:
            if re.search(r"[A-Za-z]-$", current["text"]):
                current["text"] = current["text"][:-1] + text  # a word hyphenated at the margin
            else:
                current["text"] = f"{current['text']} {text}"
        else:
            flush()
            current = {
                "text": f"- {text}" if bullet else text,
                "bullet": bullet,
                "indent": text_indent,
                "page": page,
            }
        current["last"], current["end"] = text, len(line)
        blank_before, index = False, index + 1
    flush()
    out: list[str] = []
    shown_page: int | None = None
    for index, block in enumerate(blocks):
        if index:
            # Items of one list sit on consecutive lines; everything else is a block of its own.
            out.append("\n" if block.startswith("- ") and blocks[index - 1].startswith("- ") else "\n\n")
        if page_markers and block_pages[index] != shown_page:
            shown_page = block_pages[index]
            out.append(f"[[PAGE:{shown_page}]]\n")
        out.append(block)
    return "".join(out)


def pdf_to_markdown(data: bytes, *, page_markers: bool = False) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentParseError("PDF parser is not installed") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except Exception as exc:
        raise DocumentParseError("Invalid or encrypted PDF file") from exc
    return pdf_pages_to_markdown(pages, page_markers=page_markers)


def to_markdown(filename: str, data: bytes, *, page_markers: bool = False) -> str | None:
    """Markdown for a PDF or DOCX, or None for any other kind of file."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "pdf":
        return pdf_to_markdown(data, page_markers=page_markers)
    if extension == "docx":
        return docx_to_markdown(data)
    return None


def extract_knowledge_text(filename: str, data: bytes) -> str:
    """The text the knowledge index chunks: Markdown for a PDF or DOCX, plain otherwise.

    The chunker splits at Markdown headings and "Điều N" lines, so a DOCX whose articles
    Word numbered, or whose tables sat in the middle of an article, only chunks right
    once it is Markdown. A PDF keeps ``[[PAGE:n]]`` markers for the page in a citation;
    a scan with no text layer comes back empty.
    """
    markdown = to_markdown(filename, data, page_markers=True)
    return markdown if markdown is not None else extract_file_text(filename, data)
