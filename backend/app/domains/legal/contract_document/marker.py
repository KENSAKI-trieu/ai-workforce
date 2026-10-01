"""A mark in a revised contract file naming the review it was written from.

A reviewer downloads the file with the accepted revisions written in, sends it round, and
uploads it again to be reviewed. Without the mark that upload is a stranger: the next
review judges every clause afresh, and in the medium and low findings it reliably finds
fault with the very wording the previous round proposed. With it, the next round is told
what changed since that review and is asked whether the change fixed what was found.

The mark lives where the file's own tools keep it: a custom document property in Word
(kept when the file is edited and saved again in Word) and an entry in a PDF's document
information. It is a value, not a guarantee -- anyone can copy it into another file --
so what it names is checked by the caller (signature, owner, and that the clauses still
line up).
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from lxml import etree

MARKER_NAME = "AIWorkforceContractReview"
_PDF_KEY = f"/{MARKER_NAME}"

_CUSTOM_PART = "docProps/custom.xml"
_CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_CUSTOM_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
# The format id Office gives every user-defined property.
_USER_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"


def stamp_review_marker(fmt: str | None, data: bytes, marker: str) -> bytes:
    """``data`` with the mark set, or unchanged when the file cannot carry one."""
    try:
        if fmt == "docx":
            return _stamp_docx(data, marker)
        if fmt == "pdf":
            return _stamp_pdf(data, marker)
    except Exception:  # noqa: BLE001 - a file that will not take the mark still downloads
        return data
    return data


def read_review_marker(fmt: str | None, data: bytes) -> str | None:
    """The mark in the file, or None -- for any file that has none or cannot be read."""
    try:
        if fmt == "docx":
            return _read_docx(data)
        if fmt == "pdf":
            return _read_pdf(data)
    except Exception:  # noqa: BLE001 - an unreadable mark is no mark
        return None
    return None


# --------------------------------------------------------------------------- Word


def _custom_properties(root: Any) -> list[Any]:
    return [element for element in root if etree.QName(element).localname == "property"]


def _read_docx(data: bytes) -> str | None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if _CUSTOM_PART not in archive.namelist():
            return None
        root = etree.fromstring(archive.read(_CUSTOM_PART))
    for prop in _custom_properties(root):
        if prop.get("name") == MARKER_NAME:
            value = "".join(prop.itertext()).strip()
            return value or None
    return None


def _custom_xml(existing: bytes | None, marker: str) -> bytes:
    if existing:
        root = etree.fromstring(existing)
    else:
        root = etree.Element(f"{{{_CUSTOM_NS}}}Properties", nsmap={None: _CUSTOM_NS, "vt": _VT_NS})
    props = _custom_properties(root)
    for prop in props:
        if prop.get("name") == MARKER_NAME:
            root.remove(prop)
    # Property ids start at 2 and must be unique within the part.
    pids = [int(prop.get("pid")) for prop in _custom_properties(root) if str(prop.get("pid") or "").isdigit()]
    prop = etree.SubElement(root, f"{{{_CUSTOM_NS}}}property")
    prop.set("fmtid", _USER_FMTID)
    prop.set("pid", str(max([1, *pids]) + 1))
    prop.set("name", MARKER_NAME)
    etree.SubElement(prop, f"{{{_VT_NS}}}lpwstr").text = marker
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _with_content_type(content_types: bytes) -> bytes:
    root = etree.fromstring(content_types)
    part_name = f"/{_CUSTOM_PART}"
    if not any(element.get("PartName") == part_name for element in root):
        override = etree.SubElement(root, f"{{{_CT_NS}}}Override")
        override.set("PartName", part_name)
        override.set("ContentType", _CUSTOM_TYPE)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _with_relationship(rels: bytes) -> bytes:
    root = etree.fromstring(rels)
    if not any(element.get("Type") == _CUSTOM_REL for element in root):
        ids = {element.get("Id") for element in root}
        number = 1
        while f"rId{number}" in ids:
            number += 1
        relationship = etree.SubElement(root, f"{{{_REL_NS}}}Relationship")
        relationship.set("Id", f"rId{number}")
        relationship.set("Type", _CUSTOM_REL)
        relationship.set("Target", _CUSTOM_PART)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _stamp_docx(data: bytes, marker: str) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    names = source.namelist()
    replaced = {
        _CUSTOM_PART: _custom_xml(source.read(_CUSTOM_PART) if _CUSTOM_PART in names else None, marker),
        "[Content_Types].xml": _with_content_type(source.read("[Content_Types].xml")),
        "_rels/.rels": _with_relationship(source.read("_rels/.rels")),
    }
    buffer = io.BytesIO()
    with source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        # Parts are copied in their order, [Content_Types].xml first as Word writes it.
        for info in source.infolist():
            content = replaced.pop(info.filename, None)
            target.writestr(info, content if content is not None else source.read(info.filename))
        for name, content in replaced.items():
            target.writestr(name, content)
    return buffer.getvalue()


# --------------------------------------------------------------------------- PDF


def _read_pdf(data: bytes) -> str | None:
    from pypdf import PdfReader

    info = PdfReader(io.BytesIO(data)).metadata or {}
    value = info.get(_PDF_KEY)
    if value is None:
        return None
    return str(value).strip() or None


def _stamp_pdf(data: bytes, marker: str) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({**{key: value for key, value in (reader.metadata or {}).items()}, _PDF_KEY: marker})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
