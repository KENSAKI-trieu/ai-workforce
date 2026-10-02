"""Reading a Vietnamese e-invoice from its XML (NĐ 123/2020, TT 78/2021 format).

The XML is the legal invoice; the PDF is only its picture. Every provider (VNPT, Viettel,
MISA meInvoice, BKAV, ...) emits the same element names defined by the tax authority, so
this reads them directly: no model is involved and nothing is guessed. Elements are found
by local name, which copes with the namespaces and the <TDiep><DLieu> envelope some
providers wrap around <HDon>.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.domains.finance.money import ZERO, AmountError, to_date, to_decimal

# "PO-2026-018", "PO: 2026/18", "P.O. 18", "đơn đặt hàng số DH18". The separator (or a
# digit straight after "PO") keeps words like "POS-1" from being read as a PO reference.
PO_RE = re.compile(
    r"(?:\bP\.?O\.?(?:[\s:#\-]+|(?=\d))|đơn\s+(?:đặt|mua)\s+hàng(?:\s+số)?[\s:#\-]*)"
    r"([A-Z0-9][A-Z0-9\-/\.]{1,40})",
    re.IGNORECASE,
)


class InvoiceNotReadable(ValueError):
    """The file is not an invoice this reader understands; the message says why, in Vietnamese."""


@dataclass
class ParsedInvoice:
    series: str
    number: str
    template_code: str | None
    issue_date: date | None
    currency: str
    exchange_rate: Decimal
    seller_tax_code: str
    seller_name: str
    seller_bank_account: str | None
    buyer_tax_code: str | None
    buyer_name: str | None
    amount_before_tax: Decimal
    vat_amount: Decimal
    total_amount: Decimal
    discount_amount: Decimal = ZERO
    vat_breakdown: dict[str, dict[str, str]] = field(default_factory=dict)
    lines: list[dict[str, Any]] = field(default_factory=list)
    tax_authority_code: str | None = None
    notes: list[str] = field(default_factory=list)
    po_number: str | None = None
    # How the fields were obtained; a PDF read by a model carries its own doubts here.
    source_format: str = "XML"
    extraction_warnings: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    if node is None:
        return None
    for child in node.iter():
        if _local(child.tag) == name:
            return child
    return None


def _child(node: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    if node is None:
        return None
    for child in node:
        if _local(child.tag) == name:
            return child
    return None


def _text(node: ElementTree.Element | None, name: str) -> str:
    found = _child(node, name)
    return (found.text or "").strip() if found is not None and found.text else ""


def _amount(node: ElementTree.Element | None, name: str, label: str) -> Decimal:
    raw = _text(node, name)
    try:
        return to_decimal(raw) if raw else ZERO
    except AmountError as exc:
        raise InvoiceNotReadable(f"Trường {label} ({name}) không phải số tiền: {raw!r}") from exc


def _parse_xml(data: bytes) -> ElementTree.Element:
    head = data[:4096].lower()
    # Entity declarations are how an XML file reads local files or explodes in memory.
    # A real e-invoice never carries one.
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise InvoiceNotReadable("File XML có khai báo DOCTYPE/ENTITY, không phải hoá đơn điện tử hợp lệ")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise InvoiceNotReadable(f"File XML hỏng: {exc}") from exc


def _other_info(node: ElementTree.Element | None) -> list[str]:
    """Free text of the TTKhac blocks: notes, PO references, anything the seller added."""
    texts: list[str] = []
    if node is None:
        return texts
    for element in node.iter():
        if _local(element.tag) != "TTin":
            continue
        name = _text(element, "TTruong")
        value = _text(element, "DLieu")
        if value:
            texts.append(f"{name}: {value}" if name else value)
    return texts


def find_po_number(texts: list[str]) -> str | None:
    for text in texts:
        match = PO_RE.search(text)
        if match:
            return match.group(1).rstrip(".-/").upper()
    return None


def parse_einvoice_xml(data: bytes) -> ParsedInvoice:
    root = _parse_xml(data)
    invoice = root if _local(root.tag) == "HDon" else _find(root, "HDon")
    content = _find(invoice, "DLHDon")
    if content is None:
        raise InvoiceNotReadable("Không thấy phần DLHDon: file không phải hoá đơn điện tử theo NĐ123")
    general = _child(content, "TTChung")
    body = _child(content, "NDHDon")
    seller = _child(body, "NBan")
    buyer = _child(body, "NMua")
    totals = _child(body, "TToan")
    if general is None or body is None or seller is None or totals is None:
        raise InvoiceNotReadable("Hoá đơn thiếu thông tin chung, người bán hoặc phần tổng tiền")

    number = _text(general, "SHDon")
    if not number:
        raise InvoiceNotReadable("Hoá đơn không có số (SHDon)")
    try:
        issue_date = to_date(_text(general, "NLap")) if _text(general, "NLap") else None
    except ValueError as exc:
        raise InvoiceNotReadable(f"Ngày lập hoá đơn không hợp lệ: {_text(general, 'NLap')!r}") from exc
    rate_text = _text(general, "TGia")
    try:
        exchange_rate = Decimal(rate_text.replace(",", ".")) if rate_text else Decimal("1")
    except ArithmeticError as exc:
        raise InvoiceNotReadable(f"Tỷ giá không hợp lệ: {rate_text!r}") from exc

    lines: list[dict[str, Any]] = []
    items = _child(body, "DSHHDVu")
    for item in (list(items) if items is not None else []):
        if _local(item.tag) != "HHDVu":
            continue
        lines.append({
            "kind": _text(item, "TChat") or "1",  # 1 goods/services, 3 discount, 4 note
            "name": _text(item, "THHDVu"),
            "unit": _text(item, "DVTinh"),
            "quantity": _text(item, "SLuong"),
            "unit_price": _text(item, "DGia"),
            "amount": str(_amount(item, "ThTien", "thành tiền")),
            "vat_rate": _text(item, "TSuat"),
        })

    breakdown: dict[str, dict[str, str]] = {}
    for rate in totals.iter():
        if _local(rate.tag) != "LTSuat":
            continue
        breakdown[_text(rate, "TSuat") or "?"] = {
            "base": str(_amount(rate, "ThTien", "tiền chịu thuế")),
            "vat": str(_amount(rate, "TThue", "tiền thuế")),
        }

    notes = _other_info(content) + [
        line["name"] for line in lines if line["kind"] == "4" and line["name"]
    ]
    return ParsedInvoice(
        series=_text(general, "KHHDon"),
        number=number.lstrip("0") or "0",
        template_code=_text(general, "KHMSHDon") or None,
        issue_date=issue_date,
        currency=(_text(general, "DVTTe") or "VND").upper()[:3],
        exchange_rate=exchange_rate,
        seller_tax_code=_text(seller, "MST"),
        seller_name=_text(seller, "Ten"),
        seller_bank_account=_text(seller, "STKNHang") or None,
        buyer_tax_code=_text(buyer, "MST") or None,
        buyer_name=_text(buyer, "Ten") or None,
        amount_before_tax=_amount(totals, "TgTCThue", "tổng tiền chưa thuế"),
        vat_amount=_amount(totals, "TgTThue", "tổng tiền thuế"),
        total_amount=_amount(totals, "TgTTTBSo", "tổng tiền thanh toán"),
        discount_amount=_amount(totals, "TTCKTMai", "chiết khấu thương mại"),
        vat_breakdown=breakdown,
        lines=lines,
        tax_authority_code=_text(invoice, "MCCQT") or None,
        notes=notes,
        po_number=find_po_number(notes + [line["name"] for line in lines]),
    )
