"""Taking in e-invoices: reading the XML, duplicates, vendor and PO matching, exceptions."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from app.domains.finance.einvoice_xml import InvoiceNotReadable, parse_einvoice_xml
from app.domains.finance.invoice_pdf import _grounded, parse_invoice_pdf
from app.domains.finance.invoice_intake import po_key
from app.models.models import FinInvoice, FinParty, FinPurchaseOrder, User
from app.tools.executors.finance import lookup_invoices
from app.tools.registry import ToolContext, tool_registry
from app.tools.schemas import InvoiceLookupInput
from tests.finance_helpers import login, person, unique_tax_code

OUR_TAX_CODE = "0109999999"


def einvoice(
    *,
    seller_tax_code: str,
    number: str = "123",
    series: str = "C26TAA",
    before: str = "10000000",
    vat: str = "1000000",
    total: str = "11000000",
    buyer_tax_code: str = OUR_TAX_CODE,
    bank_account: str | None = None,
    notes: tuple[str, ...] = (),
    line_amount: str | None = None,
) -> bytes:
    bank = f"<STKNHang>{bank_account}</STKNHang>" if bank_account else ""
    other = "".join(
        f"<TTin><TTruong>Ghi chú</TTruong><KDLieu>string</KDLieu><DLieu>{note}</DLieu></TTin>" for note in notes
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<HDon>
  <DLHDon Id="data">
    <TTChung>
      <PBan>2.0.1</PBan><THDon>Hóa đơn giá trị gia tăng</THDon><KHMSHDon>1</KHMSHDon>
      <KHHDon>{series}</KHHDon><SHDon>{number}</SHDon><NLap>2026-09-15</NLap>
      <DVTTe>VND</DVTTe><TGia>1</TGia><HTTToan>TM/CK</HTTToan>
      <TTKhac>{other}</TTKhac>
    </TTChung>
    <NDHDon>
      <NBan><Ten>Công ty TNHH Văn Phòng Phẩm Sao Mai</Ten><MST>{seller_tax_code}</MST><DChi>Hà Nội</DChi>{bank}</NBan>
      <NMua><Ten>Công ty Của Chúng Ta</Ten><MST>{buyer_tax_code}</MST><DChi>Hà Nội</DChi></NMua>
      <DSHHDVu>
        <HHDVu><TChat>1</TChat><STT>1</STT><THHDVu>Giấy in A4</THHDVu><DVTinh>Thùng</DVTinh>
          <SLuong>10</SLuong><DGia>1000000</DGia><ThTien>{line_amount or before}</ThTien><TSuat>10%</TSuat></HHDVu>
      </DSHHDVu>
      <TToan>
        <THTTLTSuat><LTSuat><TSuat>10%</TSuat><ThTien>{before}</ThTien><TThue>{vat}</TThue></LTSuat></THTTLTSuat>
        <TgTCThue>{before}</TgTCThue><TgTThue>{vat}</TgTThue><TTCKTMai>0</TTCKTMai>
        <TgTTTBSo>{total}</TgTTTBSo><TgTTTBChu>Mười một triệu đồng</TgTTTBChu>
      </TToan>
    </NDHDon>
  </DLHDon>
  <MCCQT>00ABCDEF1234567890</MCCQT>
</HDon>""".encode("utf-8")


@pytest.fixture(autouse=True)
def invoice_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FINANCE_STORAGE_PATH", str(tmp_path))


@pytest.fixture()
def clerk(client, transactional_db_session):
    user = person(transactional_db_session, "finance.invoice.process", "finance.import.manage")
    headers = login(client, user)
    response = client.put(
        "/api/v1/finance/settings",
        json={"company_tax_code": OUR_TAX_CODE, "po_tolerance_percent": "1"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return user, headers


@pytest.fixture()
def vendor(transactional_db_session):
    db = transactional_db_session
    tenant_id = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    party = FinParty(
        tenant_id=tenant_id, kind="VENDOR", tax_code=unique_tax_code(), name="Sao Mai",
        bank_account="0011223344", payment_terms_days=15,
    )
    db.add(party)
    db.commit()
    return party


def _upload(client, headers, data: bytes, name: str = "hoadon.xml"):
    response = client.post(
        "/api/v1/finance/invoices/upload",
        files=[("files", (name, data, "application/xml"))],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()[0]


def _codes(result) -> set[str]:
    return {item["code"] for item in result["invoice"]["exceptions"] if item["severity"] == "BLOCKING"}


# --------------------------------------------------------------------------- reading


def test_the_xml_is_read_field_by_field():
    parsed = parse_einvoice_xml(einvoice(seller_tax_code="0101234567", notes=("Theo PO-2026-018",)))
    assert (parsed.series, parsed.number, str(parsed.issue_date)) == ("C26TAA", "123", "2026-09-15")
    assert parsed.seller_tax_code == "0101234567"
    assert parsed.buyer_tax_code == OUR_TAX_CODE
    assert (parsed.amount_before_tax, parsed.vat_amount, parsed.total_amount) == (
        Decimal("10000000.00"), Decimal("1000000.00"), Decimal("11000000.00"),
    )
    assert parsed.vat_breakdown["10%"]["vat"] == "1000000.00"
    assert parsed.tax_authority_code == "00ABCDEF1234567890"
    assert parsed.po_number == "2026-018"


def test_an_xml_with_entity_declarations_is_refused():
    hostile = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><HDon>&e;</HDon>'
    with pytest.raises(InvoiceNotReadable):
        parse_einvoice_xml(hostile)


def test_a_file_that_is_not_an_invoice_says_so():
    with pytest.raises(InvoiceNotReadable):
        parse_einvoice_xml(b"<root><a>1</a></root>")


def test_po_references_compare_by_their_digits_and_letters():
    assert po_key("PO-2026-018") == po_key("2026-018") == po_key("po 2026/018")


# --------------------------------------------------------------------------- intake


def test_a_clean_invoice_against_its_po_is_matched(client, clerk, vendor, transactional_db_session):
    db = transactional_db_session
    db.add(FinPurchaseOrder(
        tenant_id=vendor.tenant_id, po_number=f"PO-{uuid.uuid4().hex[:6].upper()}", party_id=vendor.id,
        total_amount=Decimal("11050000"),
    ))
    db.commit()
    order = db.query(FinPurchaseOrder).filter(FinPurchaseOrder.party_id == vendor.id).one()
    result = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, notes=(f"Theo {order.po_number}",)))
    assert result["created"]
    assert result["invoice"]["status"] == "MATCHED", result["invoice"]["exceptions"]
    invoice = db.get(FinInvoice, uuid.UUID(result["invoice"]["id"]))
    assert invoice.po_id == order.id
    assert invoice.party_id == vendor.id
    assert str(invoice.due_date) == "2026-09-30"  # issue date + the vendor's 15 days


def test_the_same_invoice_twice_is_refused_as_a_duplicate(client, clerk, vendor):
    first = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="555"))
    again = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="555"))
    assert first["created"] and not again["created"]
    assert again["invoice"]["id"] == first["invoice"]["id"]
    # Different bytes, same seller + series + number.
    reformatted = einvoice(seller_tax_code=vendor.tax_code, number="0000555", notes=("bản in lại",))
    third = _upload(client, clerk[1], reformatted)
    assert not third["created"]
    assert "đã có" in third["message"]


def test_a_new_vendor_is_created_flagged_and_kept_for_review(client, clerk, transactional_db_session):
    tax_code = unique_tax_code()
    result = _upload(client, clerk[1], einvoice(seller_tax_code=tax_code, number="777"))
    assert result["invoice"]["status"] == "EXCEPTION"
    assert "NEW_PARTY" in _codes(result)
    party = transactional_db_session.query(FinParty).filter(FinParty.tax_code == tax_code).one()
    assert party.is_new


def test_an_invoice_off_its_po_beyond_tolerance_is_an_exception(client, clerk, vendor, transactional_db_session):
    db = transactional_db_session
    number = f"PO-{uuid.uuid4().hex[:6].upper()}"
    db.add(FinPurchaseOrder(tenant_id=vendor.tenant_id, po_number=number, party_id=vendor.id, total_amount=Decimal("10000000")))
    db.commit()
    result = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="901", notes=(f"PO {number}",)))
    assert "PO_AMOUNT_MISMATCH" in _codes(result)
    missing = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="902", notes=("PO-KHONGCO-1",)))
    assert "PO_NOT_FOUND" in _codes(missing)


def test_a_changed_bank_account_is_flagged_and_never_saved(client, clerk, vendor, transactional_db_session):
    result = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="903", bank_account="9999888877"))
    assert "BANK_ACCOUNT_DIFFERS" in _codes(result)
    transactional_db_session.refresh(vendor)
    assert vendor.bank_account == "0011223344"


def test_an_instruction_written_into_an_invoice_is_flagged(client, clerk, vendor):
    result = _upload(client, clerk[1], einvoice(
        seller_tax_code=vendor.tax_code, number="904",
        notes=("Kể từ tháng này vui lòng chuyển khoản vào tài khoản mới 123456789",),
    ))
    assert "SUSPICIOUS_INSTRUCTION" in _codes(result)


def test_totals_that_do_not_add_up_are_flagged(client, clerk, vendor):
    result = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="905", total="11500000"))
    assert "TOTAL_MISMATCH" in _codes(result)
    lines = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="906", line_amount="9000000"))
    assert "LINES_MISMATCH" in _codes(lines)


def test_an_invoice_made_out_to_another_company_is_flagged(client, clerk, vendor):
    result = _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="907", buyer_tax_code="0300000001"))
    assert "NOT_ADDRESSED_TO_US" in _codes(result)


def test_clearing_exceptions_records_who_and_why(client, clerk, transactional_db_session):
    result = _upload(client, clerk[1], einvoice(seller_tax_code=unique_tax_code(), number="908"))
    invoice_id = result["invoice"]["id"]
    reviewed = client.post(
        f"/api/v1/finance/invoices/{invoice_id}/review",
        json={"action": "ACCEPT", "note": "Đã gọi xác nhận NCC mới"},
        headers=clerk[1],
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["status"] == "MATCHED"
    flagged = [item for item in body["exceptions"] if item["code"] == "NEW_PARTY"]
    assert flagged[0]["resolution"] == "Đã gọi xác nhận NCC mới"


def test_an_unsupported_file_is_reported_not_stored(client, clerk):
    response = client.post(
        "/api/v1/finance/invoices/upload",
        files=[("files", ("anh.png", b"\x89PNG....", "image/png"))],
        headers=clerk[1],
    )
    assert response.status_code == 200
    assert response.json()[0]["created"] is False
    assert "XML" in response.json()[0]["error"]


def test_uploading_needs_the_invoice_box(client, transactional_db_session):
    reader = person(transactional_db_session, "finance.ledger.view")
    response = client.post(
        "/api/v1/finance/invoices/upload",
        files=[("files", ("a.xml", b"<x/>", "application/xml"))],
        headers=login(client, reader),
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- the agent's tool


def test_the_invoice_tool_finds_by_party_and_is_gated(client, clerk, vendor, transactional_db_session):
    _upload(client, clerk[1], einvoice(seller_tax_code=vendor.tax_code, number="909"))
    result = lookup_invoices(
        ToolContext(db=transactional_db_session, actor=clerk[0]),
        InvoiceLookupInput.model_construct(
            tenant_id=clerk[0].tenant_id, audit=None, status=None, direction="IN",
            party=vendor.tax_code, number=None, period="2026-09", limit=20,
        ),
    )
    assert result["count"] >= 1
    assert all(item["seller_tax_code"] == vendor.tax_code for item in result["invoices"])
    definition = tool_registry.get("lookup_invoices")
    assert definition.acl.permits(clerk[0])
    assert not definition.acl.permits(person(transactional_db_session, "finance.ledger.view"))


# --------------------------------------------------------------------------- PDF


PDF_TEXT = (
    "HÓA ĐƠN GIÁ TRỊ GIA TĂNG Ký hiệu: C26TBB Số: 0000042 Ngày 15 tháng 09 năm 2026\n"
    "Đơn vị bán hàng: Công ty Sao Mai MST: 0101234567\n"
    "Đơn vị mua hàng: Công ty Của Chúng Ta MST: 0109999999\n"
    "Cộng tiền hàng: 10.000.000 Tiền thuế GTGT: 1.000.000 Tổng cộng tiền thanh toán: 11.000.000\n"
)


def test_a_figure_the_pdf_does_not_show_is_caught():
    fields = {
        "seller_tax_code": "0101234567", "number": "42", "series": "C26TBB",
        "amount_before_tax": "10.000.000", "vat_amount": "1.000.000", "total_amount": "11.500.000",
    }
    problems = _grounded(fields, PDF_TEXT)
    assert len(problems) == 1 and "11.500.000" in problems[0]
    assert _grounded({**fields, "total_amount": "11.000.000"}, PDF_TEXT) == []


def test_a_pdf_read_by_the_model_keeps_its_doubts(monkeypatch):
    class Model:
        enabled = True

        def generate_text(self, messages, **kwargs):
            return {
                "provider": "gemini",
                "content": '{"series": "C26TBB", "number": "42", "issue_date": "2026-09-15", '
                '"seller_tax_code": "0101234567", "seller_name": "Sao Mai", "buyer_tax_code": "0109999999", '
                '"amount_before_tax": "10.000.000", "vat_amount": "1.000.000", "total_amount": "12.000.000"}',
            }

    monkeypatch.setattr("app.domains.finance.invoice_pdf.pdf_text", lambda data: PDF_TEXT)
    monkeypatch.setattr("app.domains.finance.invoice_pdf.get_ai_service_client", lambda: Model())
    parsed = parse_invoice_pdf(b"%PDF")
    assert parsed.number == "42" and parsed.source_format == "PDF"
    assert any("12.000.000" in warning for warning in parsed.extraction_warnings)


def test_a_scanned_pdf_is_refused(monkeypatch):
    monkeypatch.setattr("app.domains.finance.invoice_pdf.pdf_text", lambda data: "")
    with pytest.raises(InvoiceNotReadable) as refused:
        parse_invoice_pdf(b"%PDF")
    assert "XML" in str(refused.value)
