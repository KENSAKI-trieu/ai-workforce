"""Contract document tool: write a review's accepted revisions into the uploaded Word/PDF.

Two calls make up the tool:

* ``capture_form(filename, data)`` -- at upload, before the file is parsed into text,
  record its form: for Word, every paragraph and table with its style, alignment and
  numbering, the page setup, headers and footers; for PDF, every line with its position,
  font and size, the page furniture and the graphics. Stored (sealed) with the review.
* ``apply_revisions(...)`` -- after the reviewer accepts or edits suggestions, write them
  into the original file, clause by clause, and check the result against that form.

The original is never modified; a new file is produced, together with a report of what
was written where, what was left out and why, and which form checks passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.legal.contract_document.common import (
    ContractEditError,
    decisions_fingerprint,
    plan_edits,
)
from app.domains.legal.contract_document.docx_form import apply_docx_revisions, capture_docx_form
from app.domains.legal.contract_document.marker import read_review_marker, stamp_review_marker
from app.domains.legal.contract_document.pdf_form import apply_pdf_revisions, capture_pdf_form

EDITABLE_FORMATS = {"docx", "pdf"}
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

__all__ = [
    "EDITABLE_FORMATS",
    "MEDIA_TYPES",
    "ContractEditError",
    "apply_revisions",
    "capture_form",
    "decisions_fingerprint",
    "document_format",
    "read_review_marker",
    "revised_filename",
    "stamp_review_marker",
]


def document_format(filename: str) -> str | None:
    extension = Path(filename or "").suffix.lower().lstrip(".")
    return extension if extension in EDITABLE_FORMATS else None


def capture_form(filename: str, data: bytes) -> dict[str, Any] | None:
    """The file's form, or None for a format the tool does not edit.

    Raises ContractEditError for a DOCX/PDF it cannot read -- the caller decides whether
    that stops the upload (it does not: the review can still run on the text).
    """
    fmt = document_format(filename)
    if fmt == "docx":
        return capture_docx_form(data)
    if fmt == "pdf":
        return capture_pdf_form(data)
    return None


def revised_filename(original: str) -> str:
    path = Path(original or "hop-dong")
    return f"{path.stem}-da-sua{path.suffix.lower()}"


def apply_revisions(
    *,
    data: bytes,
    filename: str,
    form: dict[str, Any] | None,
    review_result: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write the accepted revisions into ``data``.

    Returns ``content`` (None when nothing could be written) and ``report``: applied,
    skipped, warnings, form checks, and the fingerprint of the decisions it reflects.
    """
    fmt = document_format(filename)
    if fmt is None:
        raise ContractEditError("Chỉ sửa trực tiếp được file Word (.docx) hoặc PDF.")
    if review_result.get("translated_for_review"):
        raise ContractEditError(
            "Hợp đồng gốc không phải tiếng Việt và được dịch để rà soát; đề xuất sửa bằng tiếng Việt "
            "không được ghi thẳng vào bản gốc. Hãy dùng file redline để thương lượng."
        )
    edits, skipped = plan_edits(review_result, decisions)
    if not edits:
        raise ContractEditError("Chưa có đề xuất nào được chấp nhận hoặc chỉnh sửa để ghi vào văn bản.")
    clauses = list(review_result.get("clauses") or [])
    if fmt == "docx":
        outcome = apply_docx_revisions(data, form, clauses, edits)
    else:
        outcome = apply_pdf_revisions(data, form, clauses, edits, document_name=filename)
    report = {
        "format": fmt,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decisions_fingerprint": decisions_fingerprint(decisions),
        "applied": outcome["applied"],
        "skipped": [*skipped, *outcome["skipped"]],
        "warnings": outcome["warnings"],
        "checks": outcome["checks"],
    }
    return {"content": outcome["content"], "report": report}
