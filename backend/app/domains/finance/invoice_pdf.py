"""Reading an invoice from a PDF that has a text layer.

The PDF is only a picture of the legal XML invoice, and its layout differs by provider, so
a model reads the fields. What it returns is then held against the PDF's own text: every
tax code, number and amount must appear there, or the invoice is flagged for a person to
check. A model that misreads a digit is caught; one that invents a figure is caught. A
scanned PDF (no text layer) is refused -- OCR is not part of this product.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.agents.finance.prompts import resolve_slot
from app.agents.llm_json import UsageReporter, extract_json_object, is_echo_provider, report_usage
from app.clients.ai_service_client import AIServiceError, get_ai_service_client
from app.domains.finance.einvoice_xml import InvoiceNotReadable, ParsedInvoice, find_po_number
from app.domains.finance.money import ZERO, AmountError, to_date, to_decimal
from app.domains.knowledge.document_markdown import pdf_to_markdown

logger = logging.getLogger(__name__)

# Enough for any invoice's first pages; longer text is a statement, not an invoice.
MAX_PROMPT_CHARS = 12_000
_NUMBER_TOKEN = re.compile(r"\d[\d.,\s]*\d|\d")


def _amounts_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_TOKEN.findall(text):
        for candidate in (token, token.replace(" ", "")):
            try:
                found.add(to_decimal(candidate))
            except AmountError:
                continue
    return found


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _grounded(parsed: dict[str, Any], text: str) -> list[str]:
    """What the model returned that the PDF's own text does not show."""
    problems: list[str] = []
    digits_text = _digits(text)
    for key, label in (("seller_tax_code", "MST người bán"), ("buyer_tax_code", "MST người mua"), ("number", "số hoá đơn")):
        value = _digits(str(parsed.get(key) or ""))
        if value and value not in digits_text:
            problems.append(f"{label} {parsed.get(key)} không có trong file PDF")
    amounts = _amounts_in(text)
    for key, label in (("amount_before_tax", "tiền trước thuế"), ("vat_amount", "tiền thuế"), ("total_amount", "tổng tiền")):
        raw = parsed.get(key)
        if raw in (None, ""):
            continue
        try:
            value = to_decimal(raw)
        except AmountError:
            problems.append(f"{label} {raw!r} không phải số tiền")
            continue
        if value and value not in amounts:
            problems.append(f"{label} {raw} không có trong file PDF")
    series = str(parsed.get("series") or "").strip()
    if series and series.upper() not in text.upper():
        problems.append(f"ký hiệu {series} không có trong file PDF")
    return problems


def pdf_text(data: bytes) -> str:
    try:
        return pdf_to_markdown(data).strip()
    except Exception as exc:  # pdfminer raises a family of unrelated types
        raise InvoiceNotReadable(f"Không đọc được file PDF: {exc}") from exc


def parse_invoice_pdf(
    data: bytes,
    *,
    prompts: Mapping[str, str] | None = None,
    on_usage: UsageReporter | None = None,
) -> ParsedInvoice:
    text = pdf_text(data)
    if len(_digits(text)) < 10:
        raise InvoiceNotReadable(
            "File PDF không có lớp chữ (bản scan/ảnh). Hệ thống chưa đọc hoá đơn scan; "
            "hãy tải file XML của hoá đơn điện tử."
        )
    client = get_ai_service_client()
    if not client.enabled:
        raise InvoiceNotReadable("Chưa đọc được PDF lúc này (dịch vụ AI không chạy); hãy tải file XML.")
    try:
        result = client.generate_text([
            {"role": "system", "content": resolve_slot(prompts, "finance_invoice_extract")},
            {"role": "user", "content": text[:MAX_PROMPT_CHARS]},
        ])
    except AIServiceError as exc:
        logger.warning("Invoice PDF extraction failed", exc_info=True)
        raise InvoiceNotReadable("Chưa đọc được PDF lúc này; hãy thử lại hoặc tải file XML.") from exc
    if is_echo_provider(result):
        raise InvoiceNotReadable("Chưa đọc được PDF lúc này (không có mô hình AI); hãy tải file XML.")
    report_usage(on_usage, result)
    fields = extract_json_object(str(result.get("content") or "")) or {}
    if not fields.get("number") or not fields.get("total_amount"):
        raise InvoiceNotReadable("Không tìm thấy số hoá đơn hoặc tổng tiền trong file PDF; hãy tải file XML.")

    warnings = _grounded(fields, text)

    def amount(key: str) -> Decimal:
        try:
            return to_decimal(fields.get(key))
        except AmountError:
            return ZERO

    try:
        issue_date = to_date(fields.get("issue_date"))
    except ValueError:
        issue_date = None
        warnings.append(f"ngày lập {fields.get('issue_date')!r} không đọc được")
    before, vat, total = amount("amount_before_tax"), amount("vat_amount"), amount("total_amount")
    if not before and not vat:
        before = total
    return ParsedInvoice(
        series=str(fields.get("series") or "").strip()[:20],
        number=(_digits(str(fields.get("number"))).lstrip("0") or "0")[:20],
        template_code=None,
        issue_date=issue_date,
        currency=str(fields.get("currency") or "VND").upper()[:3],
        exchange_rate=Decimal("1"),
        seller_tax_code=str(fields.get("seller_tax_code") or "").strip(),
        seller_name=str(fields.get("seller_name") or "").strip()[:255],
        seller_bank_account=None,
        buyer_tax_code=str(fields.get("buyer_tax_code") or "").strip() or None,
        buyer_name=str(fields.get("buyer_name") or "").strip()[:255] or None,
        amount_before_tax=before,
        vat_amount=vat,
        total_amount=total,
        notes=[],
        po_number=(str(fields.get("po_number")).strip().upper() if fields.get("po_number") else None)
        or find_po_number([text]),
        source_format="PDF",
        extraction_warnings=warnings,
    )
