"""Taking in an uploaded invoice: duplicate check, vendor and PO matching, exceptions.

What a person must look at is an exception with a code; the invoice's status says whether
anything blocks it from being proposed for posting. Matching is two-way, invoice against
purchase order: there is no goods-receipt record in these books yet to make it three-way.

Nothing on an invoice changes master data. In particular a bank account printed on an
invoice never replaces the one on file -- a changed account is the classic invoice fraud,
so it is flagged and left for a person.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.domains.finance.einvoice_xml import InvoiceNotReadable, ParsedInvoice, parse_einvoice_xml
from app.domains.finance.money import format_vnd, normalize_tax_code, plain
from app.domains.finance.settings import get_settings
from app.domains.finance.storage import save_invoice_file
from app.models.models import FinInvoice, FinParty, FinPurchaseOrder, User

MAX_INVOICE_BYTES = 5 * 1024 * 1024
# Totals on a valid invoice add up to the dong; anything beyond rounding is an error.
ROUNDING = Decimal("1")

# Phrases that ask the payer to do something unusual with the money. On an invoice they
# are the shape of payment-redirection fraud, whoever wrote them.
_INSTRUCTION_RE = re.compile(
    r"(đổi|thay đổi|cập nhật|chuyển sang)\s+(số\s+)?(tài khoản|stk|tk ngân hàng)"
    r"|tài khoản (mới|khác)|chuyển (tiền|khoản) (vào|đến|tới) (tài khoản|stk)"
    r"|ignore (previous|all) instructions|bỏ qua (mọi|các) (hướng dẫn|chỉ dẫn)",
    re.IGNORECASE,
)

BLOCKING = "BLOCKING"
INFO = "INFO"


@dataclass(frozen=True)
class IntakeResult:
    invoice: FinInvoice
    created: bool
    message: str


def _exception(code: str, message: str, severity: str = BLOCKING) -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def po_key(value: str | None) -> str:
    """`PO-2026-018`, `po 2026/018` and `2026-018` are the same order."""
    text = re.sub(r"^\s*P\.?O\.?[\s:#\-]*", "", str(value or "").upper())
    return re.sub(r"[^A-Z0-9]", "", text)


def _find_po(db: Session, tenant_id: uuid.UUID, reference: str) -> FinPurchaseOrder | None:
    wanted = po_key(reference)
    if not wanted:
        return None
    for order in db.query(FinPurchaseOrder).filter(FinPurchaseOrder.tenant_id == tenant_id):
        if po_key(order.po_number) == wanted:
            return order
    return None


def read_invoice(filename: str, data: bytes, **pdf_options: Any) -> ParsedInvoice:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xml":
        return parse_einvoice_xml(data)
    if suffix == ".pdf":
        from app.domains.finance.invoice_pdf import parse_invoice_pdf

        return parse_invoice_pdf(data, **pdf_options)
    raise InvoiceNotReadable("Chỉ nhận hoá đơn dạng XML (hoá đơn điện tử) hoặc PDF có lớp chữ")


def _arithmetic(parsed: ParsedInvoice) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    expected = parsed.amount_before_tax - parsed.discount_amount + parsed.vat_amount
    # Some providers report the base already net of the trade discount.
    if abs(expected - parsed.total_amount) > ROUNDING and abs(
        parsed.amount_before_tax + parsed.vat_amount - parsed.total_amount
    ) > ROUNDING:
        problems.append(_exception(
            "TOTAL_MISMATCH",
            f"Tiền trước thuế {format_vnd(parsed.amount_before_tax)} + thuế {format_vnd(parsed.vat_amount)} "
            f"không bằng tổng thanh toán {format_vnd(parsed.total_amount)}",
        ))
    goods = [line for line in parsed.lines if line.get("kind") in {"1", "2"}]
    if goods:
        line_sum = sum((Decimal(line["amount"]) for line in goods), Decimal("0"))
        discounts = sum(
            (Decimal(line["amount"]) for line in parsed.lines if line.get("kind") == "3"), Decimal("0")
        )
        if abs(line_sum - discounts - parsed.amount_before_tax) > ROUNDING and abs(
            line_sum - parsed.amount_before_tax
        ) > ROUNDING:
            problems.append(_exception(
                "LINES_MISMATCH",
                f"Tổng các dòng hàng {format_vnd(line_sum)} không bằng tiền trước thuế "
                f"{format_vnd(parsed.amount_before_tax)}",
            ))
    return problems


def intake_invoice(
    db: Session,
    actor: User,
    *,
    filename: str,
    data: bytes,
    direction: str = "IN",
    **pdf_options: Any,
) -> IntakeResult:
    """Store one uploaded invoice with its matching result. Raises InvoiceNotReadable."""
    if len(data) > MAX_INVOICE_BYTES:
        raise InvoiceNotReadable("File hoá đơn vượt quá 5 MB")
    tenant_id = actor.tenant_id
    content_hash = hashlib.sha256(data).hexdigest()
    same_file = db.query(FinInvoice).filter(
        FinInvoice.tenant_id == tenant_id, FinInvoice.content_hash == content_hash
    ).first()
    if same_file:
        return IntakeResult(same_file, False, "File này đã được tải lên trước đó")

    parsed = read_invoice(filename, data, **pdf_options)
    exceptions: list[dict[str, str]] = []
    try:
        seller_tax_code = normalize_tax_code(parsed.seller_tax_code) or ""
    except ValueError:
        seller_tax_code = parsed.seller_tax_code[:14]
        exceptions.append(_exception("SELLER_TAX_CODE_INVALID", f"MST người bán không hợp lệ: {parsed.seller_tax_code!r}"))
    try:
        buyer_tax_code = normalize_tax_code(parsed.buyer_tax_code)
    except ValueError:
        buyer_tax_code = (parsed.buyer_tax_code or "")[:14] or None
        exceptions.append(_exception("BUYER_TAX_CODE_INVALID", f"MST người mua không hợp lệ: {parsed.buyer_tax_code!r}"))

    duplicate = db.query(FinInvoice).filter(
        FinInvoice.tenant_id == tenant_id,
        FinInvoice.direction == direction,
        FinInvoice.seller_tax_code == seller_tax_code,
        FinInvoice.series == parsed.series,
        FinInvoice.number == parsed.number,
    ).first()
    if duplicate:
        return IntakeResult(
            duplicate, False,
            f"Hoá đơn {parsed.series} số {parsed.number} của MST {seller_tax_code} đã có trong hệ thống",
        )

    for warning in parsed.extraction_warnings:
        exceptions.append(_exception("EXTRACTION_UNVERIFIED", f"Cần kiểm tra lại: {warning}"))
    exceptions.extend(_arithmetic(parsed))

    settings = get_settings(db, tenant_id)
    own_tax_code = settings.company_tax_code
    counterpart_tax_code = seller_tax_code if direction == "IN" else buyer_tax_code
    if own_tax_code:
        ours = buyer_tax_code if direction == "IN" else seller_tax_code
        if ours != own_tax_code:
            exceptions.append(_exception(
                "NOT_ADDRESSED_TO_US",
                f"Hoá đơn ghi MST {'người mua' if direction == 'IN' else 'người bán'} {ours or '(trống)'}, "
                f"không phải MST công ty {own_tax_code}",
            ))
    else:
        exceptions.append(_exception(
            "COMPANY_TAX_CODE_NOT_SET",
            "Chưa khai báo MST công ty trong cài đặt Tài chính nên chưa kiểm tra được người mua",
            INFO,
        ))

    party = None
    if counterpart_tax_code:
        party = db.query(FinParty).filter(
            FinParty.tenant_id == tenant_id, FinParty.tax_code == counterpart_tax_code
        ).first()
    if party is None:
        party = FinParty(
            tenant_id=tenant_id,
            kind="VENDOR" if direction == "IN" else "CUSTOMER",
            tax_code=counterpart_tax_code or None,
            name=(parsed.seller_name if direction == "IN" else parsed.buyer_name) or "(chưa rõ tên)",
            is_new=True,
        )
        db.add(party)
        db.flush()
        exceptions.append(_exception(
            "NEW_PARTY",
            f"{'Nhà cung cấp' if direction == 'IN' else 'Khách hàng'} mới, chưa có trong danh mục: {party.name}",
        ))
    elif party.is_new:
        exceptions.append(_exception("NEW_PARTY", f"Đối tác {party.name} chưa từng được hạch toán", INFO))

    if direction == "IN" and parsed.seller_bank_account:
        on_file = (party.bank_account or "").replace(" ", "")
        printed = parsed.seller_bank_account.replace(" ", "")
        if on_file and printed != on_file:
            exceptions.append(_exception(
                "BANK_ACCOUNT_DIFFERS",
                "Số tài khoản ngân hàng in trên hoá đơn khác số đã lưu của nhà cung cấp. "
                "Không thanh toán theo số mới trước khi xác minh trực tiếp với nhà cung cấp.",
            ))
    for note in parsed.notes:
        if _INSTRUCTION_RE.search(note):
            exceptions.append(_exception(
                "SUSPICIOUS_INSTRUCTION",
                f"Hoá đơn có ghi chú yêu cầu thay đổi cách thanh toán: “{note[:200]}”",
            ))
            break

    order = None
    if parsed.po_number and direction == "IN":
        order = _find_po(db, tenant_id, parsed.po_number)
        if order is None:
            exceptions.append(_exception("PO_NOT_FOUND", f"Hoá đơn ghi PO {parsed.po_number} nhưng không có PO này"))
        else:
            if order.party_id and order.party_id != party.id:
                exceptions.append(_exception("PO_VENDOR_MISMATCH", f"PO {order.po_number} thuộc nhà cung cấp khác"))
            tolerance = order.total_amount * settings.po_tolerance_percent / Decimal("100")
            difference = parsed.total_amount - order.total_amount
            if abs(difference) > max(tolerance, ROUNDING):
                exceptions.append(_exception(
                    "PO_AMOUNT_MISMATCH",
                    f"Tổng hoá đơn {format_vnd(parsed.total_amount)} lệch PO {order.po_number} "
                    f"({format_vnd(order.total_amount)}) {format_vnd(difference)}",
                ))
    elif direction == "IN":
        exceptions.append(_exception("NO_PO", "Hoá đơn không ghi số PO; đối chiếu bằng mắt nếu cần", INFO))

    if parsed.issue_date:
        lookalike = db.query(FinInvoice).filter(
            FinInvoice.tenant_id == tenant_id,
            FinInvoice.direction == direction,
            FinInvoice.party_id == party.id,
            FinInvoice.total_amount == parsed.total_amount,
            FinInvoice.issue_date >= parsed.issue_date - timedelta(days=7),
            FinInvoice.issue_date <= parsed.issue_date + timedelta(days=7),
        ).first()
        if lookalike:
            exceptions.append(_exception(
                "POSSIBLE_DUPLICATE",
                f"Giống hoá đơn {lookalike.series} số {lookalike.number} (cùng đối tác, cùng số tiền, gần ngày)",
            ))

    blocking = any(item["severity"] == BLOCKING for item in exceptions)
    invoice_id = uuid.uuid4()
    invoice = FinInvoice(
        id=invoice_id,
        tenant_id=tenant_id,
        direction=direction,
        party_id=party.id,
        seller_tax_code=seller_tax_code,
        seller_name=parsed.seller_name[:255],
        buyer_tax_code=buyer_tax_code,
        buyer_name=(parsed.buyer_name or "")[:255] or None,
        template_code=parsed.template_code,
        series=parsed.series[:20],
        number=parsed.number[:20],
        issue_date=parsed.issue_date,
        due_date=(parsed.issue_date + timedelta(days=party.payment_terms_days)) if parsed.issue_date else None,
        currency=parsed.currency,
        exchange_rate=parsed.exchange_rate,
        amount_before_tax=parsed.amount_before_tax,
        vat_amount=parsed.vat_amount,
        total_amount=parsed.total_amount,
        vat_breakdown=parsed.vat_breakdown,
        lines=parsed.lines,
        tax_authority_code=parsed.tax_authority_code,
        po_number=parsed.po_number,
        po_id=order.id if order else None,
        status="EXCEPTION" if blocking else "MATCHED",
        exceptions=exceptions,
        source_format=parsed.source_format,
        source_filename=Path(filename).name[:255],
        content_hash=content_hash,
        created_by_id=actor.id,
    )
    invoice.source_storage_key = save_invoice_file(
        tenant_id=tenant_id, invoice_id=invoice_id, filename=filename, data=data
    )
    db.add(invoice)
    db.flush()
    message = (
        f"Hoá đơn {invoice.series} số {invoice.number}: {len([e for e in exceptions if e['severity'] == BLOCKING])} "
        "ngoại lệ cần xem" if blocking else f"Hoá đơn {invoice.series} số {invoice.number} khớp, sẵn sàng đề xuất bút toán"
    )
    return IntakeResult(invoice, True, message)


def serialize_invoice(invoice: FinInvoice, *, with_lines: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(invoice.id),
        "direction": invoice.direction,
        "party_id": str(invoice.party_id) if invoice.party_id else None,
        "party_name": invoice.party.name if invoice.party else (invoice.seller_name or invoice.buyer_name),
        "seller_tax_code": invoice.seller_tax_code,
        "seller_name": invoice.seller_name,
        "buyer_tax_code": invoice.buyer_tax_code,
        "series": invoice.series,
        "number": invoice.number,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "currency": invoice.currency,
        "amount_before_tax": plain(invoice.amount_before_tax),
        "vat_amount": plain(invoice.vat_amount),
        "total_amount": plain(invoice.total_amount),
        "po_number": invoice.po_number,
        "status": invoice.status,
        "exceptions": invoice.exceptions or [],
        "source_format": invoice.source_format,
        "source_filename": invoice.source_filename,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
    }
    if with_lines:
        payload["lines"] = invoice.lines or []
        payload["vat_breakdown"] = invoice.vat_breakdown or {}
        payload["tax_authority_code"] = invoice.tax_authority_code
    return payload
