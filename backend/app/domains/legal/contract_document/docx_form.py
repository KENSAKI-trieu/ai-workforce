"""Write accepted revisions into the uploaded Word file without disturbing its form.

The file is edited in place at the XML level: only the paragraphs of a revised clause's
body are replaced, and each new paragraph is a copy of one of the paragraphs it replaces --
its paragraph properties (style, alignment, indents, spacing, numbering) and the run
properties of its text (font, size, bold) -- with only the words changed. Headings, tables,
headers, footers, section setup, images and every other clause are left byte-for-byte.

The form is recorded when the file is uploaded, before it is parsed into text
(``capture_docx_form``), and checked again after every edit (``_check_form``): a revision
that changed anything outside its own clause is refused rather than delivered.
"""

from __future__ import annotations

import io
import re
from copy import deepcopy
from typing import Any

from app.domains.knowledge.document_markdown import _W, _Numbering
from app.domains.legal.contract_document.common import (
    LIST_MARKER,
    ContractEditError,
    Edit,
    article_number,
    insert_heading,
    locate_clauses,
    revision_lines,
)

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _document(data: bytes) -> Any:
    try:
        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError as exc:  # pragma: no cover - a hard dependency
        raise ContractEditError("Chưa cài thư viện đọc DOCX") from exc
    try:
        return Document(io.BytesIO(data))
    except (PackageNotFoundError, ValueError, KeyError) as exc:
        raise ContractEditError("File Word không hợp lệ hoặc đã bị hỏng.") from exc


def _own_text(element: Any) -> str:
    parts: list[str] = []
    for node in element.iter(f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr"):
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        else:
            parts.append("\n")
    return "".join(parts)


def _p_pr(element: Any) -> Any:
    return element.find(f"{_W}pPr")


def _val(parent: Any, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(path)
    return None if node is None else node.get(f"{_W}val")


def _blocks(document: Any) -> list[dict[str, Any]]:
    """The body in order, each paragraph with Word's numbering rendered as the reader saw it.

    Numbering is counted only on paragraphs with text, as ``docx_to_markdown`` counts it,
    so a heading reads here exactly as it read in the review.
    """
    from docx.text.paragraph import Paragraph

    numbering = _Numbering(document)
    blocks: list[dict[str, Any]] = []
    for element in document.element.body.iterchildren():
        if element.tag == f"{_W}p":
            own = _own_text(element)
            label = numbering.label(Paragraph(element, document)).strip() if own.strip() else ""
            p_pr = _p_pr(element)
            num_pr = p_pr.find(f"{_W}numPr") if p_pr is not None else None
            blocks.append({
                "element": element,
                "kind": "p",
                "own_text": own,
                "label": label,
                "text": f"{label} {own}".strip() if label else own.strip(),
                "style": _val(p_pr, f"{_W}pStyle"),
                "align": _val(p_pr, f"{_W}jc"),
                "num": None if num_pr is None else [_val(num_pr, f"{_W}numId"), _val(num_pr, f"{_W}ilvl")],
                "drawings": len(element.findall(f".//{_W}drawing")) + len(element.findall(f".//{_W}pict")),
            })
        elif element.tag == f"{_W}tbl":
            cells: list[str] = []
            for cell in element.iter(f"{_W}tc"):
                text = " ".join(_own_text(p) for p in cell.iter(f"{_W}p")).strip()
                if text and (not cells or cells[-1] != text):
                    cells.append(text)
            blocks.append({
                "element": element,
                "kind": "tbl",
                "own_text": " ".join(cells),
                "label": "",
                "text": " ".join(cells),
                "style": _val(element.find(f"{_W}tblPr"), f"{_W}tblStyle"),
                "align": None,
                "num": None,
                "drawings": len(element.findall(f".//{_W}drawing")) + len(element.findall(f".//{_W}pict")),
            })
    return blocks


def _signature(block: dict[str, Any]) -> dict[str, Any]:
    """What a block's form is: kind, words (numbering label apart), style, alignment, list."""
    return {
        "kind": block["kind"],
        "text": " ".join(block["own_text"].split()),
        "label": block["label"],
        "style": block["style"],
        "align": block["align"],
        "num": block["num"],
        "drawings": block["drawings"],
    }


def _page_setup(document: Any) -> list[dict[str, Any]]:
    sections = []
    for section in document.sections:
        sections.append({
            "page_width": section.page_width,
            "page_height": section.page_height,
            "orientation": int(section.orientation) if section.orientation is not None else None,
            "margins": [section.top_margin, section.right_margin, section.bottom_margin, section.left_margin],
            "header": [p.text for p in section.header.paragraphs] if not section.header.is_linked_to_previous else None,
            "footer": [p.text for p in section.footer.paragraphs] if not section.footer.is_linked_to_previous else None,
        })
    return sections


def capture_docx_form(data: bytes) -> dict[str, Any]:
    """The form of a Word file as uploaded, recorded before it is parsed into text."""
    document = _document(data)
    blocks = _blocks(document)
    return {
        "format": "docx",
        "blocks": [_signature(block) for block in blocks],
        "sections": _page_setup(document),
        "counts": {
            "paragraphs": sum(block["kind"] == "p" for block in blocks),
            "tables": sum(block["kind"] == "tbl" for block in blocks),
            "drawings": sum(block["drawings"] for block in blocks),
        },
    }


# --------------------------------------------------------------------------- writing


def _first_run_pr(paragraph: Any, *, at: int = 0) -> Any:
    """The run properties of the text at character ``at`` of the paragraph (the first run by default)."""
    position = 0
    fallback = None
    for run in paragraph.iter(f"{_W}r"):
        text = _own_text(run)
        if not text:
            continue
        r_pr = run.find(f"{_W}rPr")
        if fallback is None:
            fallback = r_pr
        position += len(text)
        if position > at:
            return r_pr
    return fallback


def _new_paragraph(template: Any, text: str, *, r_pr: Any = None, keep_numbering: bool = True) -> Any:
    """A paragraph with the template's paragraph properties and one run of ``text``."""
    from lxml import etree

    paragraph = etree.Element(f"{_W}p")
    p_pr = _p_pr(template)
    if p_pr is not None:
        p_pr = deepcopy(p_pr)
        for tag in ("sectPr", "pPrChange"):
            for node in p_pr.findall(f"{_W}{tag}"):
                p_pr.remove(node)
        if not keep_numbering:
            for node in p_pr.findall(f"{_W}numPr"):
                p_pr.remove(node)
        paragraph.append(p_pr)
    run = etree.SubElement(paragraph, f"{_W}r")
    run_pr = r_pr if r_pr is not None else _first_run_pr(template)
    if run_pr is not None:
        run.append(deepcopy(run_pr))
    text_node = etree.SubElement(run, f"{_W}t")
    text_node.text = text
    text_node.set(_XML_SPACE, "preserve")
    return paragraph


def _is_listy(block: dict[str, Any]) -> bool:
    return bool(block["label"]) or bool(LIST_MARKER.match(block["own_text"]))


def _template_for(line: str, position: int, body: list[dict[str, Any]]) -> dict[str, Any]:
    """The paragraph a new line takes its form from: the one at its position when alike."""
    listy = bool(LIST_MARKER.match(line))
    if position < len(body) and _is_listy(body[position]) == listy:
        return body[position]
    return next((block for block in body if _is_listy(block) == listy), body[0])


def _line_text(line: str, template: dict[str, Any]) -> str:
    # Word draws the marker of an automatically numbered paragraph; writing it too would
    # show "a) a) ...".
    return LIST_MARKER.sub("", line, count=1) if template["label"] and LIST_MARKER.match(line) else line


class _Writer:
    def __init__(self, blocks: list[dict[str, Any]]) -> None:
        self.blocks = blocks
        self.new: set[int] = set()  # id() of every element this edit wrote
        self.removed: set[int] = set()  # id() of every original element it took out
        # lxml hands out a fresh proxy -- with a fresh id() -- for an element nobody holds;
        # holding every written element keeps the ids above meaning the same element.
        self._held: list[Any] = []

    def _mark(self, element: Any) -> Any:
        self._held.append(element)
        self.new.add(id(element))
        return element

    def replace_body(self, start: int, end: int, lines: list[str]) -> str | None:
        """Write ``lines`` as the body of the clause at [start, end); why not, when refused."""
        heading = self.blocks[start]
        body = self.blocks[start + 1:end]
        while body and body[-1]["kind"] == "p" and not body[-1]["own_text"].strip():
            body.pop()  # the spacing before the next clause is the document's, not the clause's
        if heading["kind"] != "p":
            return "Không xác định được tiêu đề điều khoản trong file Word."
        if any(block["kind"] == "tbl" for block in body):
            return "Điều khoản có bảng trong file Word; để giữ nguyên bảng, hãy sửa điều này bằng tay."
        filled = [block for block in body if block["own_text"].strip()]
        if not filled:
            return self._rewrite_inline(heading, lines)
        written = []
        for position, line in enumerate(lines):
            template = _template_for(line, position, filled)
            written.append(self._mark(_new_paragraph(template["element"], _line_text(line, template))))
        anchor = body[0]["element"]
        for element in written:
            anchor.addprevious(element)
        for block in body:
            section = block["element"].find(f"{_W}pPr/{_W}sectPr")
            if section is not None:
                # A section break rides on the paragraph that ends the section; it has to
                # stay with whatever paragraph now ends it.
                last_pr = _p_pr(written[-1])
                if last_pr is None:
                    from lxml import etree

                    last_pr = etree.Element(f"{_W}pPr")
                    written[-1].insert(0, last_pr)
                last_pr.append(deepcopy(section))
            self.removed.add(id(block["element"]))
            block["element"].getparent().remove(block["element"])
        return None

    def _rewrite_inline(self, heading: dict[str, Any], lines: list[str]) -> str | None:
        """A clause written in one paragraph: keep its "Điều N." prefix, replace the rest."""
        from lxml import etree

        element = heading["element"]
        own = heading["own_text"]
        prefix = ""
        if article_number(own) is not None:
            match = re.match(r"^\s*\S+\s+\d+(?:\.\d+)*\s*[.:\-)]?\s*", own)
            prefix = own[: match.end()] if match else ""
        prefix_pr = _first_run_pr(element)
        body_pr = _first_run_pr(element, at=len(prefix))
        # The paragraph's own form (properties, bookmarks) stays; its runs are rewritten.
        for child in list(element):
            if child.tag in {f"{_W}r", f"{_W}hyperlink", f"{_W}ins", f"{_W}del", f"{_W}smartTag"}:
                element.remove(child)
        for text, r_pr in ((prefix, prefix_pr), (lines[0], body_pr)):
            if not text:
                continue
            run = etree.SubElement(element, f"{_W}r")
            if r_pr is not None:
                run.append(deepcopy(r_pr))
            node = etree.SubElement(run, f"{_W}t")
            node.text = text
            node.set(_XML_SPACE, "preserve")
        self._mark(element)
        anchor = element
        for line in lines[1:]:
            paragraph = self._mark(_new_paragraph(element, line, r_pr=body_pr, keep_numbering=False))
            anchor.addnext(paragraph)
            anchor = paragraph
        return None

    def insert_clause(self, before: Any, body: Any, heading: dict[str, Any], body_template: dict[str, Any],
                      title: str, lines: list[str], number: int | None) -> None:
        """Add a clause before ``before`` (or at the end of ``body``), formed like the heading and body given."""
        heading_text = title
        if not heading["label"] and number is not None:
            heading_text = f"Điều {number}. {title}"
        written = [self._mark(_new_paragraph(heading["element"], heading_text))]
        for line in lines:
            written.append(self._mark(_new_paragraph(body_template["element"], _line_text(line, body_template))))
        for element in written:
            if before is not None:
                before.addprevious(element)
            else:
                # The body ends with its section properties; content goes ahead of them.
                section = body.find(f"{_W}sectPr")
                if section is not None:
                    section.addprevious(element)
                else:
                    body.append(element)


def _check_form(form: dict[str, Any], original: list[dict[str, Any]], writer: _Writer,
                edited_bytes: bytes, marks: list[bool]) -> tuple[list[dict[str, Any]], list[str]]:
    """Compare the edited file with the recorded form; raise when anything else changed."""
    reloaded = _document(edited_bytes)
    blocks = _blocks(reloaded)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    if len(blocks) != len(marks):
        raise ContractEditError("File Word sau khi sửa không đọc lại được đúng cấu trúc; bản sửa bị huỷ.")
    kept_before = [
        signature
        for signature, block in zip(form["blocks"], original)
        if id(block["element"]) not in writer.removed and id(block["element"]) not in writer.new
    ]
    kept_after = [_signature(block) for block, is_new in zip(blocks, marks) if not is_new]
    if len(kept_before) != len(kept_after):
        raise ContractEditError("Bản sửa đã làm thay đổi các đoạn ngoài điều khoản được sửa; bản sửa bị huỷ.")
    renumbered = []
    for before, after in zip(kept_before, kept_after):
        if {**before, "label": None} != {**after, "label": None}:
            raise ContractEditError(
                "Bản sửa làm thay đổi nội dung hoặc định dạng ngoài điều khoản được sửa "
                f"(\"{before['text'][:60]}\"); bản sửa bị huỷ."
            )
        if before["label"] != after["label"]:
            renumbered.append(f"{before['label']} → {after['label']}")
    checks.append({"name": "Nội dung & định dạng ngoài điều được sửa", "ok": True,
                   "detail": f"{len(kept_after)} đoạn/bảng giữ nguyên"})
    if renumbered:
        warnings.append(
            "Word tự đánh lại số cho một số đoạn sau phần sửa (" + ", ".join(renumbered[:5])
            + ("…" if len(renumbered) > 5 else "") + "); hãy kiểm tra lại số thứ tự."
        )
    if _page_setup(reloaded) != form["sections"]:
        raise ContractEditError("Khổ giấy, lề hoặc header/footer bị thay đổi sau khi sửa; bản sửa bị huỷ.")
    checks.append({"name": "Khổ giấy, lề, header/footer", "ok": True, "detail": f"{len(form['sections'])} section giữ nguyên"})
    drawings = sum(block["drawings"] for block, is_new in zip(blocks, marks) if not is_new)
    expected = sum(s["drawings"] for s, b in zip(form["blocks"], original)
                   if id(b["element"]) not in writer.removed and id(b["element"]) not in writer.new)
    if drawings != expected:
        raise ContractEditError("Hình ảnh trong văn bản bị thay đổi sau khi sửa; bản sửa bị huỷ.")
    checks.append({"name": "Hình ảnh, bảng biểu", "ok": True,
                   "detail": f"{sum(b['kind'] == 'tbl' for b in blocks)} bảng, {drawings} hình giữ nguyên"})
    return checks, warnings


def apply_docx_revisions(
    data: bytes, form: dict[str, Any] | None, clauses: list[dict[str, Any]], edits: list[Edit],
) -> dict[str, Any]:
    """The edited file and a report of what was written where, and what was not and why."""
    document = _document(data)
    blocks = _blocks(document)
    if not form or form.get("format") != "docx" or len(form.get("blocks") or []) != len(blocks):
        form = capture_docx_form(data)
    ranges = locate_clauses([block["text"] for block in blocks], clauses)
    writer = _Writer(blocks)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    def skip(edit: Edit, reason: str) -> None:
        skipped.append({"finding_key": edit.finding_key, "clause": _clause_label(edit), "reason": reason})

    for edit in (item for item in edits if item.kind == "REPLACE"):
        located = ranges.get(edit.clause_id or "")
        if located is None:
            skip(edit, "Không tìm thấy điều khoản này trong file gốc (tiêu đề đã khác với bản được rà soát).")
            continue
        lines = revision_lines(edit)
        if not lines:
            skip(edit, "Đề xuất chỉ có tiêu đề, không có nội dung để thay.")
            continue
        refused = writer.replace_body(*located, lines)
        if refused:
            skip(edit, refused)
            continue
        applied.append({
            "finding_key": edit.finding_key, "clause": _clause_label(edit), "action": "REPLACED",
            "decision": edit.decision, "location": f"Đoạn {located[0] + 1}–{located[1]} của file Word",
            "notes": edit.notes,
        })

    inserts = [item for item in edits if item.kind == "INSERT"]
    if inserts:
        before, heading, body_template, next_number = _insert_point(blocks, ranges)
        for edit in inserts:
            if heading is None:
                skip(edit, "Không xác định được vị trí chèn điều khoản mới trong file Word.")
                continue
            title, lines = insert_heading(edit)
            writer.insert_clause(before, document.element.body, heading, body_template, title, lines, next_number)
            label = f"Điều {next_number}" if next_number is not None else "Điều khoản mới"
            applied.append({
                "finding_key": edit.finding_key, "clause": f"{label} · {title}", "action": "INSERTED",
                "decision": edit.decision, "location": "Sau điều khoản cuối cùng, trước phần ký",
                "notes": edit.notes,
            })
            if next_number is not None:
                next_number += 1

    if not applied:
        return {"content": None, "applied": applied, "skipped": skipped, "checks": [], "warnings": []}
    marks = [id(element) in writer.new for element in document.element.body.iterchildren()
             if element.tag in (f"{_W}p", f"{_W}tbl")]
    if sum(marks) != len(writer.new):
        # Something written did not end up in the document: reporting it as applied would
        # tell the reviewer a clause is in the file when it is not.
        raise ContractEditError("Một phần nội dung sửa không được ghi vào văn bản; bản sửa bị huỷ.")
    buffer = io.BytesIO()
    document.save(buffer)
    content = buffer.getvalue()
    checks, warnings = _check_form(form, blocks, writer, content, marks)
    return {"content": content, "applied": applied, "skipped": skipped, "checks": checks, "warnings": warnings}


def _insert_point(blocks: list[dict[str, Any]], ranges: dict[str, tuple[int, int]]):
    """Where a missing clause goes -- after the last clause's text -- and what it is formed like.

    The place is given as the element the new clause goes *before*: the blank spacing that
    ends the last clause, else the signature block, else the end of the body. None of those
    is ever removed by a rewrite, whereas the last clause's own paragraphs are when that
    clause is revised too -- and a clause added after a removed paragraph is silently lost.
    """
    if not ranges:
        return None, None, None, None
    start, end = max(ranges.values())
    tail = end
    while tail - 1 > start and blocks[tail - 1]["kind"] == "p" and not blocks[tail - 1]["own_text"].strip():
        tail -= 1
    before = blocks[tail]["element"] if tail < len(blocks) else None
    body = [block for block in blocks[start + 1:end] if block["own_text"].strip()]
    heading = blocks[start]
    body_template = next((block for block in body if block["kind"] == "p" and not _is_listy(block)),
                         next((block for block in body if block["kind"] == "p"), heading))
    if heading["kind"] != "p":
        return None, None, None, None
    numbers = []
    for first, _ in ranges.values():
        number = article_number(blocks[first]["text"])
        if number and number.split(".")[0].isdigit():
            numbers.append(int(number.split(".")[0]))
    next_number = max(numbers) + 1 if numbers else None
    return before, heading, body_template, next_number


def _clause_label(edit: Edit) -> str:
    if edit.kind == "INSERT":
        return "Điều khoản mới"
    title = f" · {edit.clause_title}" if edit.clause_title else ""
    return f"Điều {edit.clause_number}{title}"
