"""Write accepted revisions into an uploaded PDF without disturbing its form.

A PDF has no paragraphs to rewrite: every line is text drawn at a fixed position. So a
clause body is edited the way a typesetter would patch a plate --

1. its lines are found from the form recorded at upload (``capture_pdf_form``: every line
   with its position, font and size, the page furniture, and where the page draws
   graphics), which is taken before the file is parsed into text;
2. the text-showing operators of exactly those lines are removed from the page's content
   stream -- the old wording is gone, not painted over, so it cannot be selected, copied
   or searched -- while every other operator (graphics, other text, clipping) stays;
3. the revision is set into the space the body occupied, in a font of the same family and
   the same size, leading, margins and alignment, and merged onto the page as real text.

Nothing is reflowed. When a revision needs more lines than the body had, or the body sits
among table borders or other graphics, it is not squeezed in: the clause body is replaced
by a one-line reference and the full wording goes into an amendment annex appended after
the last page -- the usual way to amend a signed-format contract. A missing clause goes
into that annex too. Everything outside the edited lines is checked against the recorded
form afterwards; a mismatch refuses the edit.

Only pypdf and reportlab are used (both permissively licensed).
"""

from __future__ import annotations

import io
import math
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from app.domains.legal.contract_document.common import (
    ContractEditError,
    Edit,
    insert_heading,
    locate_clauses,
    revision_lines,
    squash,
)

_Y_TOLERANCE = 1.2
_PAGE_NUMBER = re.compile(r"^(?:[-–—]?\s*\d{1,4}\s*[-–—]?|(?:trang|page)\s+\d+(?:\s*(?:/|of|trên)\s*\d+)?|\d+\s*/\s*\d+)$", re.IGNORECASE)
_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}
_PAINT_OPS = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}


# --------------------------------------------------------------------------- reading


def _reader(data: bytes) -> Any:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError, OSError) as exc:
        raise ContractEditError("File PDF không hợp lệ hoặc đã bị hỏng.") from exc
    if reader.is_encrypted:
        raise ContractEditError("File PDF đang được đặt mật khẩu/khoá chỉnh sửa nên không thể sửa.")
    return reader


def _mult(m: list[float], n: list[float]) -> list[float]:
    return [
        m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3],
        m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
        m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5],
    ]


def _apply(m: list[float], x: float, y: float) -> tuple[float, float]:
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _numbers(operands: list[Any]) -> list[float]:
    return [float(value) for value in operands]


def _walk(operations: list[tuple[list[Any], bytes]]) -> list[dict[str, Any]]:
    """Every operator's effect that matters here: where text is shown and where graphics are painted.

    Returns one entry per operator index that shows text (``kind`` "text", its baseline y
    and fill colour) or paints a path, image or form (``kind`` "paint"/"xobject", bbox).
    """
    identity = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    ctm = identity[:]
    fill: tuple[str, list[float]] = ("g", [0.0])
    stack: list[tuple[list[float], tuple[str, list[float]]]] = []
    tm = tlm = identity[:]
    leading = rise = 0.0
    path: list[tuple[float, float]] = []
    events: list[dict[str, Any]] = []

    def line_move(tx: float, ty: float) -> None:
        nonlocal tm, tlm
        tlm = _mult([1.0, 0.0, 0.0, 1.0, tx, ty], tlm)
        tm = tlm[:]

    for index, (operands, operator) in enumerate(operations):
        try:
            if operator == b"q":
                stack.append((ctm[:], fill))
            elif operator == b"Q":
                if stack:
                    ctm, fill = stack.pop()
            elif operator == b"cm":
                ctm = _mult(_numbers(operands), ctm)
            elif operator in (b"g", b"rg", b"k"):
                fill = (operator.decode(), _numbers(operands))
            elif operator == b"BT":
                tm = tlm = identity[:]
            elif operator == b"Tm":
                tm = tlm = _numbers(operands)
            elif operator == b"Td":
                line_move(*_numbers(operands))
            elif operator == b"TD":
                tx, ty = _numbers(operands)
                leading = -ty
                line_move(tx, ty)
            elif operator == b"TL":
                leading = float(operands[0])
            elif operator == b"Ts":
                rise = float(operands[0])
            elif operator == b"T*":
                line_move(0.0, -leading)
            elif operator in _SHOW_OPS:
                if operator in (b"'", b'"'):
                    line_move(0.0, -leading)
                x, y = _apply(_mult(tm, ctm), 0.0, rise)
                events.append({"index": index, "kind": "text", "x": x, "y": y, "fill": fill})
            elif operator == b"re":
                x, y, w, h = _numbers(operands)
                path.extend(_apply(ctm, px, py) for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))
            elif operator in (b"m", b"l"):
                path.append(_apply(ctm, *_numbers(operands[-2:])))
            elif operator in (b"c", b"v", b"y"):
                values = _numbers(operands)
                path.extend(_apply(ctm, values[i], values[i + 1]) for i in range(0, len(values), 2))
            elif operator in _PAINT_OPS or operator == b"n":
                if operator != b"n" and path:
                    xs, ys = [p[0] for p in path], [p[1] for p in path]
                    events.append({"index": index, "kind": "paint", "bbox": [min(xs), min(ys), max(xs), max(ys)]})
                path = []
            elif operator in (b"Do", b"INLINE IMAGE", b"sh"):
                corners = [_apply(ctm, px, py) for px, py in ((0, 0), (1, 0), (0, 1), (1, 1))]
                xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                events.append({"index": index, "kind": "xobject", "bbox": [min(xs), min(ys), max(xs), max(ys)]})
        except (TypeError, ValueError, IndexError):
            continue  # a malformed operator is left as it is; it is not one this edit touches
    return events


def _font_traits(base_font: str) -> dict[str, Any]:
    name = (base_font or "").lstrip("/")
    name = name.split("+", 1)[-1]
    lowered = name.lower()
    return {
        "font": name,
        "bold": any(token in lowered for token in ("bold", "black", "heavy", "semibold")),
        "italic": any(token in lowered for token in ("italic", "oblique")),
        "serif": any(token in lowered for token in ("times", "serif", "roman", "georgia", "cambria", "garamond", "book")),
    }


def _page_lines(page: Any) -> list[dict[str, Any]]:
    """The page's text as lines: baseline y, left x, text, and the dominant font and size."""
    chunks: list[dict[str, Any]] = []

    def visit(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        text = (text or "").split("\n")[0]
        if not text.strip():
            return
        matrix = _mult([float(v) for v in tm], [float(v) for v in cm])
        scale = math.hypot(matrix[2], matrix[3]) or 1.0
        base = str((font or {}).get("/BaseFont", "")) if hasattr(font, "get") else ""
        chunks.append({
            "x": matrix[4], "y": matrix[5], "text": unicodedata.normalize("NFC", text),
            "size": float(size or 0) * scale, "font": base,
        })

    try:
        page.extract_text(visitor_text=visit)
    except Exception as exc:  # pypdf raises a wide range on damaged content
        raise ContractEditError("Không đọc được nội dung trang PDF.") from exc
    chunks.sort(key=lambda chunk: (-round(chunk["y"], 0), chunk["x"]))
    lines: list[dict[str, Any]] = []
    for chunk in chunks:
        line = next((item for item in lines if abs(item["y"] - chunk["y"]) <= _Y_TOLERANCE), None)
        if line is None:
            line = {"y": chunk["y"], "chunks": []}
            lines.append(line)
        line["chunks"].append(chunk)
    out = []
    for line in lines:
        parts = sorted(line["chunks"], key=lambda chunk: chunk["x"])
        text = ""
        for position, chunk in enumerate(parts):
            if position and not text.endswith(" ") and not chunk["text"].startswith(" "):
                previous = parts[position - 1]
                # Without glyph widths a gap is estimated: past half an em per character,
                # the space was made by position, not by a space glyph.
                expected = previous["x"] + 0.42 * previous["size"] * len(previous["text"])
                if chunk["x"] - expected > 0.6 * previous["size"]:
                    text += " "
            text += chunk["text"]
        weights: Counter[tuple[str, float]] = Counter()
        for chunk in parts:
            weights[(chunk["font"], round(chunk["size"], 2))] += len(chunk["text"].strip())
        (font, size), _ = weights.most_common(1)[0]
        out.append({
            "y": round(line["y"], 2), "x": round(parts[0]["x"], 2), "text": text.strip(),
            "size": size, **_font_traits(font),
        })
    out.sort(key=lambda item: (-item["y"], item["x"]))
    return out


def capture_pdf_form(data: bytes) -> dict[str, Any]:
    """The form of a PDF as uploaded, recorded before it is parsed into text."""
    from pypdf.generic import ContentStream

    reader = _reader(data)
    pages = []
    for page in reader.pages:
        box = page.mediabox
        content = page.get_contents()
        events = _walk(ContentStream(content, reader).operations) if content is not None else []
        pages.append({
            "box": [float(box.left), float(box.bottom), float(box.right), float(box.top)],
            "rotate": int(page.get("/Rotate") or 0),
            "lines": _page_lines(page),
            "graphics": [event["bbox"] for event in events if event["kind"] != "text"],
            "text_ops": sum(event["kind"] == "text" for event in events),
        })
    _mark_furniture(pages)
    return {"format": "pdf", "pages": pages}


def _mark_furniture(pages: list[dict[str, Any]]) -> None:
    """Flag running headers, footers and page numbers: never part of a clause."""
    counts: Counter[str] = Counter()
    for page in pages:
        height = page["box"][3] - page["box"][1]
        for line in page["lines"]:
            near_edge = line["y"] > page["box"][1] + 0.88 * height or line["y"] < page["box"][1] + 0.12 * height
            line["edge"] = near_edge
            if near_edge:
                counts[re.sub(r"\d+", "#", squash(line["text"]))] += 1
    for page in pages:
        for line in page["lines"]:
            key = re.sub(r"\d+", "#", squash(line["text"]))
            line["furniture"] = bool(line.pop("edge")) and (
                bool(_PAGE_NUMBER.match(line["text"].strip())) or (len(pages) >= 2 and counts[key] >= 2)
            )


# --------------------------------------------------------------------------- fonts


_FONT_DIRS = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/msttcorefonts"),
]
# Metric-compatible first: Liberation Serif/Sans set Vietnamese at Times/Arial widths.
_FONT_FILES = {
    (True, False): ["times.ttf", "LiberationSerif-Regular.ttf", "Times_New_Roman.ttf", "DejaVuSerif.ttf"],
    (True, True): ["timesbd.ttf", "LiberationSerif-Bold.ttf", "Times_New_Roman_Bold.ttf", "DejaVuSerif-Bold.ttf"],
    (False, False): ["arial.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "DejaVuSans.ttf"],
    (False, True): ["arialbd.ttf", "LiberationSans-Bold.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf"],
}


def _font(serif: bool, bold: bool) -> str:
    """A registered reportlab font able to set Vietnamese, as close to the original as installed."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for wanted in ((serif, bold), (serif, False), (not serif, bold), (not serif, False)):
        for filename in _FONT_FILES[wanted]:
            for directory in _FONT_DIRS:
                path = directory / filename
                if not path.is_file():
                    continue
                name = f"ContractEdit-{path.stem}"
                if name not in pdfmetrics.getRegisteredFontNames():
                    try:
                        pdfmetrics.registerFont(TTFont(name, str(path)))
                    except Exception:
                        continue
                return name
    raise ContractEditError(
        "Máy chủ chưa có font Unicode (Times New Roman, Liberation hoặc DejaVu) để ghi tiếng Việt vào PDF."
    )


def _width(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size)


def _wrap(paragraphs: list[str], font: str, size: float, width: float, indent: float) -> list[tuple[str, bool, bool]]:
    """Lines as (text, starts a paragraph, ends a paragraph)."""
    out: list[tuple[str, bool, bool]] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        current: list[str] = []
        first = True
        for word in words:
            available = width - (indent if first else 0)
            candidate = " ".join([*current, word])
            if current and _width(candidate, font, size) > available:
                out.append((" ".join(current), first, False))
                current, first = [word], False
            else:
                current.append(word)
        if current:
            out.append((" ".join(current), first, True))
    return out


# --------------------------------------------------------------------------- planning


class _Region:
    """A clause body in the PDF: its lines, page by page, and how its text is set."""

    def __init__(self, blocks: list[dict[str, Any]], pages: list[dict[str, Any]],
                 following: dict[str, Any] | None, floor: float) -> None:
        self.blocks = blocks
        self.following = following
        self.floor = floor
        self.justified = False
        self.by_page: dict[int, list[dict[str, Any]]] = {}
        for block in blocks:
            self.by_page.setdefault(block["page"], []).append(block)
        weights: Counter[tuple[bool, float, str]] = Counter()
        for block in blocks:
            weights[(block["serif"], block["size"], block["font"])] += len(block["text"])
        (self.serif, self.size, _), _ = weights.most_common(1)[0]
        self.left = min(block["x"] for block in blocks)
        self.first_indent = max(0.0, blocks[0]["x"] - self.left)
        gaps = []
        for page_blocks in self.by_page.values():
            ys = [block["y"] for block in page_blocks]
            gaps.extend(round(a - b, 2) for a, b in zip(ys, ys[1:]) if a - b > 0.5)
        gaps.sort()
        self.leading = gaps[0] if gaps else round(self.size * 1.15, 2)
        wide = [gap for gap in gaps if gap > 1.25 * self.leading]
        self.paragraph_gap = (sorted(wide)[len(wide) // 2] - self.leading) if wide else 0.0
        self.pages = pages

    def right(self, page: int) -> float:
        box = self.pages[page]["box"]
        margin = min((line["x"] for line in self.pages[page]["lines"] if not line.get("furniture")), default=self.left)
        return box[2] - (margin - box[0])

    def _limit(self, page: int) -> float:
        """The lowest baseline the rewritten body may use on ``page``.

        The body's own lines, and below the last of them the blank space before the next
        line of the document -- the spacing after a paragraph is room a longer revision
        can use without touching anything. A body that runs on to another page keeps to
        its own lines on this one.
        """
        page_blocks = self.by_page[page]
        bottom = page_blocks[-1]["y"]
        if page != max(self.by_page):
            return bottom
        if self.following is not None and self.following["page"] == page:
            clearance = max(self.leading, 1.15 * self.following["size"])
            return min(bottom, self.following["y"] + clearance)
        return min(bottom, self.floor)

    def slots(self, leading: float) -> list[tuple[int, float, float]]:
        """Baselines available to write on: (page, y, lowest y allowed on that page)."""
        out = []
        for page, page_blocks in sorted(self.by_page.items()):
            top, limit = page_blocks[0]["y"], self._limit(page)
            count = int((top - limit) / leading + 1e-6) + 1
            out.extend((page, top - index * leading, limit) for index in range(count))
        return out

    def graphics_inside(self) -> bool:
        for page, page_blocks in self.by_page.items():
            top = page_blocks[0]["y"] + page_blocks[0]["size"]
            bottom = self._limit(page) - 0.35 * page_blocks[-1]["size"]
            for x0, y0, x1, y1 in self.pages[page]["graphics"]:
                if y1 > bottom and y0 < top and (x1 - x0) > 1 and not (y1 - y0 > 0.6 * (self.pages[page]["box"][3] - self.pages[page]["box"][1])):
                    return True  # a table border, rule or picture; a full-page frame is not
        return False


def _layout(region: _Region, paragraphs: list[str], font: str) -> list[dict[str, Any]] | None:
    """The revision set into the region's space, at the original size if it fits, else a little smaller."""
    for factor in (1.0, 0.96, 0.92):
        size, leading = region.size * factor, region.leading * factor
        slots = region.slots(leading)
        width = min(region.right(page) for page in region.by_page) - region.left
        lines = _wrap(paragraphs, font, size, width, region.first_indent)
        if len(lines) > len(slots):
            continue
        justify = region.justified
        placed = []
        for (text, starts, ends), (page, y, _) in zip(lines, slots):
            x = region.left + (region.first_indent if starts else 0.0)
            placed.append({
                "page": page, "x": x, "y": y, "text": text, "size": size, "font": font,
                "justify_width": (region.right(page) - x) if justify and not ends else None,
            })
        return placed
    return None


def _draw(canvas: Any, item: dict[str, Any]) -> None:
    canvas.setFont(item["font"], item["size"])
    width = item.get("justify_width")
    words = item["text"].split(" ")
    if not width or len(words) < 2:
        canvas.drawString(item["x"], item["y"], item["text"])
        return
    natural = sum(_width(word, item["font"], item["size"]) for word in words)
    gap = (width - natural) / (len(words) - 1)
    if gap > 3 * _width(" ", item["font"], item["size"]):
        canvas.drawString(item["x"], item["y"], item["text"])  # too sparse to justify
        return
    x = item["x"]
    for word in words:
        canvas.drawString(x, item["y"], word)
        x += _width(word, item["font"], item["size"]) + gap


# --------------------------------------------------------------------------- editing


def apply_pdf_revisions(
    data: bytes, form: dict[str, Any] | None, clauses: list[dict[str, Any]], edits: list[Edit],
    *, document_name: str,
) -> dict[str, Any]:
    from pypdf import PdfWriter
    from pypdf.generic import ContentStream

    reader = _reader(data)
    if not form or form.get("format") != "pdf" or len(form.get("pages") or []) != len(reader.pages):
        form = capture_pdf_form(data)
    pages = form["pages"]
    blocks = [
        {**line, "page": number}
        for number, page in enumerate(pages)
        for line in page["lines"]
        if not line.get("furniture")
    ]
    ranges = locate_clauses([block["text"] for block in blocks], clauses, squash_spaces=True)
    # The lowest line of text anywhere: the bottom margin, as far as the file shows it.
    floor = min((block["y"] for block in blocks), default=0.0)

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    annex: list[dict[str, Any]] = []
    removals: dict[int, list[float]] = {}  # page -> baselines whose text is taken out
    drawings: list[dict[str, Any]] = []

    def label(edit: Edit) -> str:
        title = f" · {edit.clause_title}" if edit.clause_title else ""
        return f"Điều {edit.clause_number}{title}"

    def legal_title(edit: Edit) -> str:
        title = f" ({edit.clause_title})" if edit.clause_title else ""
        return f"Điều {edit.clause_number}{title}"

    for edit in (item for item in edits if item.kind == "REPLACE"):
        located = ranges.get(edit.clause_id or "")
        if located is None:
            skipped.append({"finding_key": edit.finding_key, "clause": label(edit),
                            "reason": "Không tìm thấy điều khoản này trong file PDF gốc."})
            continue
        start, end = located
        body = blocks[start + 1:end]
        lines = revision_lines(edit)
        if not lines:
            skipped.append({"finding_key": edit.finding_key, "clause": label(edit),
                            "reason": "Đề xuất chỉ có tiêu đề, không có nội dung để thay."})
            continue
        if not body:
            annex.append({"edit": edit, "title": label(edit), "legal_title": legal_title(edit), "lines": lines, "kind": "REPLACE"})
            applied.append({"finding_key": edit.finding_key, "clause": label(edit), "action": "ANNEXED",
                            "decision": edit.decision, "location": "Phụ lục sửa đổi (điều khoản viết liền một dòng)",
                            "notes": edit.notes})
            continue
        region = _Region(body, pages, blocks[end] if end < len(blocks) else None, floor)
        region.justified = _looks_justified(region)
        font = _font(region.serif, False)
        reference = None
        placed = None if region.graphics_inside() else _layout(region, lines, font)
        if placed is None:
            reference_number = len(annex) + 1
            # Short enough to sit on one line at the body's own size.
            reference = f"Sửa đổi theo mục {reference_number} Phụ lục sửa đổi, bổ sung đính kèm."
            if region.graphics_inside():
                # Leave the text where it sits among the table/graphics; the annex amends it.
                annex.append({"edit": edit, "title": label(edit), "legal_title": legal_title(edit), "lines": lines, "kind": "REPLACE"})
                applied.append({"finding_key": edit.finding_key, "clause": label(edit), "action": "ANNEXED",
                                "decision": edit.decision,
                                "location": f"Phụ lục sửa đổi, mục {reference_number} (điều khoản có bảng/đồ hoạ, giữ nguyên bản gốc tại chỗ)",
                                "notes": edit.notes})
                continue
            placed = _layout(region, [f"({reference})"], font)
            if placed is None:
                skipped.append({"finding_key": edit.finding_key, "clause": label(edit),
                                "reason": "Không đủ chỗ trong PDF để ghi nội dung sửa."})
                continue
            annex.append({"edit": edit, "title": label(edit), "legal_title": legal_title(edit), "lines": lines, "kind": "REPLACE"})
        for block in body:
            removals.setdefault(block["page"], []).append(block["y"])
        drawings.extend(placed)
        applied.append({
            "finding_key": edit.finding_key, "clause": label(edit),
            "action": "REPLACED" if reference is None else "ANNEXED",
            "decision": edit.decision,
            "location": (f"Trang {body[0]['page'] + 1}" + (f"–{body[-1]['page'] + 1}" if body[-1]["page"] != body[0]["page"] else "")
                         + ("" if reference is None else f" · nội dung đầy đủ ở Phụ lục sửa đổi, mục {len(annex)}")),
            "notes": edit.notes,
        })

    for edit in (item for item in edits if item.kind == "INSERT"):
        title, lines = insert_heading(edit)
        annex.append({"edit": edit, "title": title, "lines": lines, "kind": "INSERT"})
        applied.append({"finding_key": edit.finding_key, "clause": f"Điều khoản mới · {title}", "action": "ANNEXED",
                        "decision": edit.decision, "location": f"Phụ lục sửa đổi, mục {len(annex)}", "notes": edit.notes})

    if not applied:
        return {"content": None, "applied": applied, "skipped": skipped, "checks": [], "warnings": []}

    writer = PdfWriter(clone_from=reader)
    removed_counts: dict[int, int] = {}
    for page_number, baselines in removals.items():
        page = writer.pages[page_number]
        content = ContentStream(page.get_contents(), writer)
        events = _walk(content.operations)
        drop: dict[int, list[Any] | None] = {}
        for event in events:
            if event["kind"] == "text" and any(abs(event["y"] - y) <= _Y_TOLERANCE for y in baselines):
                operands, operator = content.operations[event["index"]]
                # "'" and '"' also move to the next line; the move has to stay for the text after.
                drop[event["index"]] = (
                    [([], b"T*")] if operator == b"'"
                    else [([operands[0]], b"Tw"), ([operands[1]], b"Tc"), ([], b"T*")] if operator == b'"'
                    else None
                )
        found_ys = {round(event["y"], 1) for event in events if event["index"] in drop}
        missing = [y for y in baselines if not any(abs(y - found) <= _Y_TOLERANCE for found in found_ys)]
        if missing:
            raise ContractEditError(
                "Chữ trong PDF này nằm trong khối đồ hoạ nhúng (XObject) nên không gỡ được an toàn; "
                "hãy dùng bản Word của hợp đồng để sửa."
            )
        operations = []
        for index, operation in enumerate(content.operations):
            if index in drop:
                operations.extend(drop[index] or [])
            else:
                operations.append(operation)
        content.operations = operations
        page.replace_contents(content)
        removed_counts[page_number] = len(drop)

    _overlay(writer, drawings)
    annex_pages = _append_annex(writer, annex, pages, document_name, blocks) if annex else 0
    buffer = io.BytesIO()
    writer.write(buffer)
    content_bytes = buffer.getvalue()
    checks = _check_form(form, content_bytes, removals, drawings, annex_pages, removed_counts)
    warnings = []
    if any(item["action"] == "ANNEXED" for item in applied):
        warnings.append(
            "Một số nội dung không vừa chỗ trống của điều khoản trong PDF nên được đưa vào Phụ lục sửa đổi, "
            "bổ sung ở cuối file; Phụ lục cần được ký cùng hợp đồng."
        )
    return {"content": content_bytes, "applied": applied, "skipped": skipped, "checks": checks, "warnings": warnings}


def _looks_justified(region: _Region) -> bool:
    font = _font(region.serif, False)
    for page, page_blocks in region.by_page.items():
        right = region.right(page)
        for block in page_blocks[:-1]:
            end = block["x"] + _width(block["text"], font, block["size"])
            if end >= right - 0.03 * (right - region.left):
                return True
    return False


def _overlay(writer: Any, drawings: list[dict[str, Any]]) -> None:
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas as rl_canvas

    by_page: dict[int, list[dict[str, Any]]] = {}
    for item in drawings:
        by_page.setdefault(item["page"], []).append(item)
    for page_number, items in by_page.items():
        page = writer.pages[page_number]
        box = page.mediabox
        buffer = io.BytesIO()
        canvas = rl_canvas.Canvas(buffer, pagesize=(float(box.right), float(box.top)))
        for item in items:
            _draw(canvas, item)
        canvas.showPage()
        canvas.save()
        page.merge_page(PdfReader(io.BytesIO(buffer.getvalue())).pages[0])


def _append_annex(writer: Any, annex: list[dict[str, Any]], pages: list[dict[str, Any]],
                  document_name: str, blocks: list[dict[str, Any]]) -> int:
    """Amendment annex after the last page, in the contract's own page size, margins and font."""
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas as rl_canvas

    last = pages[-1]
    left_page, bottom, right_page, top = last["box"]
    body = [block for block in blocks if block["size"]] or [{"serif": True, "size": 12.0, "x": left_page + 72}]
    serif = Counter(block["serif"] for block in body).most_common(1)[0][0]
    size = Counter(round(block["size"], 1) for block in body).most_common(1)[0][0]
    margin = max(36.0, min(block["x"] for block in body) - left_page)
    regular, bold = _font(serif, False), _font(serif, True)
    width = (right_page - left_page) - 2 * margin
    leading = size * 1.35
    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer, pagesize=(right_page - left_page, top - bottom))
    count = 1
    y = (top - bottom) - margin

    def new_page() -> None:
        nonlocal y, count
        canvas.showPage()
        count += 1
        y = (top - bottom) - margin

    def write(text: str, font: str, *, center: bool = False, indent: float = 0.0) -> None:
        nonlocal y
        for line, _, _ in _wrap([text], font, size, width - indent, 0.0):
            if y < margin:
                new_page()
            canvas.setFont(font, size)
            if center:
                canvas.drawCentredString((right_page - left_page) / 2, y, line)
            else:
                canvas.drawString(margin + indent, y, line)
            y -= leading

    write("PHỤ LỤC SỬA ĐỔI, BỔ SUNG HỢP ĐỒNG", bold, center=True)
    y -= leading * 0.5
    write(f"Kèm theo: {document_name}", regular, center=True)
    y -= leading * 0.5
    write("Các nội dung dưới đây sửa đổi, bổ sung hợp đồng nêu trên và là một phần không tách rời của hợp đồng. "
          "Các điều khoản khác của hợp đồng giữ nguyên hiệu lực.", regular)
    y -= leading * 0.5
    for number, item in enumerate(annex, 1):
        heading = (f"{number}. Sửa đổi {item['legal_title']} như sau:" if item["kind"] == "REPLACE"
                   else f"{number}. Bổ sung điều khoản \"{item['title']}\" như sau:")
        write(heading, bold)
        for line in item["lines"]:
            write(line, regular, indent=size)
        y -= leading * 0.4
    if y < margin + 2 * leading:
        new_page()
    y -= leading
    canvas.setFont(bold, size)
    canvas.drawString(margin, y, "ĐẠI DIỆN BÊN A")
    canvas.drawRightString(margin + width, y, "ĐẠI DIỆN BÊN B")
    canvas.showPage()
    canvas.save()
    annex_reader = PdfReader(io.BytesIO(buffer.getvalue()))
    for page in annex_reader.pages:
        writer.add_page(page)
    return len(annex_reader.pages)


def _check_form(form: dict[str, Any], edited: bytes, removals: dict[int, list[float]],
                drawings: list[dict[str, Any]], annex_pages: int, removed_counts: dict[int, int]) -> list[dict[str, Any]]:
    """Compare the edited PDF with the recorded form; raise when anything else changed."""
    from pypdf.generic import ContentStream

    reader = _reader(edited)
    pages = form["pages"]
    if len(reader.pages) != len(pages) + annex_pages:
        raise ContractEditError("Số trang PDF sau khi sửa không khớp; bản sửa bị huỷ.")
    written: dict[int, list[float]] = {}
    for item in drawings:
        written.setdefault(item["page"], []).append(item["y"])
    kept_lines = 0
    for number, page in enumerate(pages):
        edited_page = reader.pages[number]
        box = edited_page.mediabox
        if [float(box.left), float(box.bottom), float(box.right), float(box.top)] != page["box"]:
            raise ContractEditError("Khổ trang PDF bị thay đổi sau khi sửa; bản sửa bị huỷ.")
        touched = removals.get(number, []) + written.get(number, [])

        def untouched(line: dict[str, Any]) -> bool:
            return not any(abs(line["y"] - y) <= _Y_TOLERANCE for y in touched)

        before = [(squash(line["text"]), round(line["y"])) for line in page["lines"] if untouched(line)]
        after = [(squash(line["text"]), round(line["y"])) for line in _page_lines(edited_page) if untouched(line)]
        if before != after:
            raise ContractEditError(
                f"Nội dung ngoài điều khoản được sửa ở trang {number + 1} bị thay đổi; bản sửa bị huỷ."
            )
        kept_lines += len(before)
        events = _walk(ContentStream(edited_page.get_contents(), reader).operations)
        graphics = [event for event in events if event["kind"] != "text"]
        # The overlay merged onto an edited page brings no graphics of its own.
        if len(graphics) != len(page["graphics"]):
            raise ContractEditError(f"Đồ hoạ ở trang {number + 1} bị thay đổi sau khi sửa; bản sửa bị huỷ.")
    return [
        {"name": "Nội dung ngoài điều được sửa", "ok": True, "detail": f"{kept_lines} dòng giữ nguyên vị trí và chữ"},
        {"name": "Khổ trang, bảng, đường kẻ, hình ảnh", "ok": True, "detail": f"{len(pages)} trang giữ nguyên"},
        {"name": "Chữ cũ đã gỡ khỏi PDF", "ok": True,
         "detail": f"{sum(removed_counts.values())} cụm chữ cũ đã xoá hẳn (không chỉ che)"},
    ]
