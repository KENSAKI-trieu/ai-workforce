"""
Seed script: sổ sách mẫu cho Trợ lý Tài chính, để thử trang /finance và chat FINANCE.

Chạy từ thư mục `backend/`: `python -m scripts.seed_finance_demo admin@company.com`
(email của một người trong công ty cần nạp dữ liệu; mặc định admin@company.com).

Nạp qua đúng đường người dùng đi -- chọn TT200, import Excel, tải hoá đơn XML -- nên dữ
liệu đi qua mọi kiểm tra của hệ thống. Tạo:
  - MST công ty 0109999999, ngưỡng duyệt mặc định;
  - 3 nhà cung cấp, 2 khách hàng (có email để thử nhắc nợ);
  - số dư đầu kỳ tháng 08/2026 (cân), sổ nhật ký tháng 09/2026 có phòng ban;
  - ngân sách tháng 09/2026 cho MARKETING và SALES (MARKETING vượt);
  - 1 PO, 3 hoá đơn bán còn nợ (một quá hạn > 60 ngày), 1 hoá đơn mua đã ghi sổ;
  - 3 file XML hoá đơn mua vào ở data/finance-demo/ để tự tải lên: một khớp PO,
    một lệch PO, một từ nhà cung cấp mới có ghi chú đổi số tài khoản.

Chạy lại không nhân đôi: đối tượng cập nhật theo MST, lần nhập sổ đã có thì bỏ qua.
"""

from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from app.core.database import Base, SyncSessionLocal, sync_engine
from app.domains.finance.excel_import import ImportRejected, import_books
from app.domains.finance.settings import get_settings, seed_chart
from app.models.models import FinImportBatch, User

COMPANY_TAX_CODE = "0109999999"
VENDORS = [
    ("NCC", "0101111111", "Công ty TNHH Văn phòng phẩm Sao Mai", "ketoan@saomai.vn", "0011001234567", "Vietcombank", 15),
    ("NCC", "0102222222", "Công ty CP Dịch vụ Quảng cáo Ánh Dương", "billing@anhduong.vn", "1903555666777", "Techcombank", 30),
    ("NCC", "0103333333", "Công ty TNHH Thiết bị Số Việt", "sales@soviet.vn", "0123456789", "MB", 30),
]
CUSTOMERS = [
    ("KH", "0304444444", "Công ty CP Bán lẻ Phương Nam", "congno@phuongnam.vn", None, None, 30),
    ("KH", "0305555555", "Công ty TNHH Thương mại Bắc Hà", "ketoan@bacha.vn", None, None, 45),
]


def _xlsx(header, rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _invoice_xml(seller, number, before, vat, *, notes=(), bank=None) -> str:
    other = "".join(f"<TTin><TTruong>Ghi chú</TTruong><KDLieu>string</KDLieu><DLieu>{note}</DLieu></TTin>" for note in notes)
    bank_xml = f"<STKNHang>{bank}</STKNHang>" if bank else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<HDon><DLHDon Id="data">
  <TTChung><PBan>2.0.1</PBan><THDon>Hóa đơn giá trị gia tăng</THDon><KHMSHDon>1</KHMSHDon>
    <KHHDon>C26TAA</KHHDon><SHDon>{number}</SHDon><NLap>2026-09-18</NLap><DVTTe>VND</DVTTe><TGia>1</TGia>
    <TTKhac>{other}</TTKhac></TTChung>
  <NDHDon>
    <NBan><Ten>{seller[2]}</Ten><MST>{seller[1]}</MST><DChi>Hà Nội</DChi>{bank_xml}</NBan>
    <NMua><Ten>Công ty của bạn</Ten><MST>{COMPANY_TAX_CODE}</MST><DChi>Hà Nội</DChi></NMua>
    <DSHHDVu><HHDVu><TChat>1</TChat><STT>1</STT><THHDVu>Hàng hoá dịch vụ theo hợp đồng</THHDVu>
      <DVTinh>Gói</DVTinh><SLuong>1</SLuong><DGia>{before}</DGia><ThTien>{before}</ThTien><TSuat>10%</TSuat></HHDVu></DSHHDVu>
    <TToan><THTTLTSuat><LTSuat><TSuat>10%</TSuat><ThTien>{before}</ThTien><TThue>{vat}</TThue></LTSuat></THTTLTSuat>
      <TgTCThue>{before}</TgTCThue><TgTThue>{vat}</TgTThue><TTCKTMai>0</TTCKTMai><TgTTTBSo>{before + vat}</TgTTTBSo></TToan>
  </NDHDon></DLHDon><MCCQT>00DEMO{number}</MCCQT></HDon>"""


def _import_once(db, actor, kind, filename, data) -> None:
    exists = db.query(FinImportBatch).filter(
        FinImportBatch.tenant_id == actor.tenant_id,
        FinImportBatch.filename == filename,
        FinImportBatch.status == "COMMITTED",
    ).first()
    if exists and kind != "parties":
        print(f"  bỏ qua {filename}: đã nhập")
        return
    try:
        batch = import_books(db, actor, kind, filename, data)
        db.commit()
        print(f"  nhập {filename}: {batch.row_count} dòng")
    except ImportRejected as exc:
        db.rollback()
        raise SystemExit(f"{filename} bị từ chối: {exc.errors[:5]}") from exc


def seed(email: str) -> None:
    Base.metadata.create_all(bind=sync_engine)
    db = SyncSessionLocal()
    try:
        actor = db.query(User).filter(User.email == email).first()
        if actor is None:
            raise SystemExit(f"Không có người dùng {email}")
        settings = get_settings(db, actor.tenant_id)
        settings.company_tax_code = COMPANY_TAX_CODE
        added = seed_chart(db, actor.tenant_id, "TT200")
        db.commit()
        print(f"Hệ thống TK TT200: thêm {added} tài khoản; MST công ty {COMPANY_TAX_CODE}")

        _import_once(db, actor, "parties", "demo-doi-tuong.xlsx", _xlsx(
            ["Loại (NCC/KH/CẢ HAI)", "MST", "Tên", "Địa chỉ", "Email", "Số TK ngân hàng", "Ngân hàng", "Số ngày được nợ"],
            [(kind, tax, name, "Việt Nam", mail, bank, bank_name, days) for kind, tax, name, mail, bank, bank_name, days in VENDORS + CUSTOMERS],
        ))
        _import_once(db, actor, "opening_balances", "demo-so-du-dau-ky.xlsx", _xlsx(
            ["Kỳ (YYYY-MM)", "Số TK", "Dư Nợ", "Dư Có", "MST đối tượng"],
            [
                ("2026-08", "1111", 150_000_000, None, None),
                ("2026-08", "1121", 2_350_000_000, None, None),
                ("2026-08", "131", 230_000_000, None, "0304444444"),
                ("2026-08", "131", 58_000_000, None, "0305555555"),
                ("2026-08", "156", 640_000_000, None, None),
                ("2026-08", "331", None, 110_000_000, "0101111111"),
                ("2026-08", "331", None, 198_000_000, "0102222222"),
                ("2026-08", "411", None, 3_000_000_000, None),
                ("2026-08", "421", None, 120_000_000, None),
            ],
        ))
        _import_once(db, actor, "ledger", "demo-nhat-ky-2026-09.xlsx", _xlsx(
            ["Ngày hạch toán", "Số chứng từ", "Diễn giải", "Số TK", "Phát sinh Nợ", "Phát sinh Có", "MST đối tượng", "Phòng ban"],
            [
                ("05/09/2026", "PC0901", "Chi quảng cáo Facebook", "6428", 62_000_000, None, None, "MARKETING"),
                ("05/09/2026", "PC0901", "Chi quảng cáo Facebook", "1121", None, 62_000_000, None, None),
                ("10/09/2026", "PC0902", "Hoa hồng đại lý", "6418", 45_000_000, None, None, "SALES"),
                ("10/09/2026", "PC0902", "Hoa hồng đại lý", "1121", None, 45_000_000, None, None),
                ("12/09/2026", "BC0901", "Khách Phương Nam trả nợ", "1121", 120_000_000, None, None, None),
                ("12/09/2026", "BC0901", "Khách Phương Nam trả nợ", "131", None, 120_000_000, "0304444444", None),
                ("20/09/2026", "UNC0901", "Trả nợ Sao Mai", "331", 110_000_000, None, "0101111111", None),
                ("20/09/2026", "UNC0901", "Trả nợ Sao Mai", "1121", None, 110_000_000, None, None),
            ],
        ))
        _import_once(db, actor, "budgets", "demo-ngan-sach-2026-09.xlsx", _xlsx(
            ["Phòng ban", "Số TK", "Kỳ (YYYY-MM)", "Số tiền"],
            [("MARKETING", "6428", "2026-09", 50_000_000), ("SALES", "6418", "2026-09", 80_000_000)],
        ))
        _import_once(db, actor, "purchase_orders", "demo-po.xlsx", _xlsx(
            ["Số PO", "MST nhà cung cấp", "Ngày PO", "Tiền trước thuế", "Tiền thuế", "Tổng tiền"],
            [("PO-2026-018", "0101111111", "01/09/2026", 10_000_000, 1_000_000, 11_000_000),
             ("PO-2026-019", "0102222222", "02/09/2026", 40_000_000, 4_000_000, 44_000_000)],
        ))
        today = date.today()
        _import_once(db, actor, "open_invoices", "demo-hoa-don-con-no.xlsx", _xlsx(
            ["Loại (MUA/BAN)", "MST đối tượng", "Ký hiệu", "Số hoá đơn", "Ngày hoá đơn", "Hạn thanh toán", "Tiền trước thuế", "Tiền thuế", "Tổng tiền", "Đã thanh toán"],
            [
                ("BAN", "0304444444", "K26TBB", "301", today - timedelta(days=95), today - timedelta(days=65), 90_000_000, 9_000_000, 99_000_000, 0),
                ("BAN", "0304444444", "K26TBB", "315", today - timedelta(days=20), today + timedelta(days=10), 10_000_000, 1_000_000, 11_000_000, 0),
                ("BAN", "0305555555", "K26TBB", "322", today - timedelta(days=50), today - timedelta(days=5), 80_000_000, 8_000_000, 88_000_000, 30_000_000),
                ("MUA", "0102222222", "C26TAA", "8812", today - timedelta(days=25), today + timedelta(days=5), 180_000_000, 18_000_000, 198_000_000, 0),
            ],
        ))

        folder = Path("data/finance-demo")
        folder.mkdir(parents=True, exist_ok=True)
        samples = {
            "hoa-don-sao-mai-khop-po.xml": _invoice_xml(VENDORS[0], 4501, 10_000_000, 1_000_000, notes=("Theo PO-2026-018",)),
            "hoa-don-anh-duong-lech-po.xml": _invoice_xml(VENDORS[1], 7702, 48_000_000, 4_800_000, notes=("Theo đơn đặt hàng số PO-2026-019",)),
            "hoa-don-ncc-moi-doi-tai-khoan.xml": _invoice_xml(
                ("NCC", "0106666666", "Công ty TNHH Giải pháp Mới", None, None, None, 30), 120, 15_000_000, 1_500_000,
                notes=("Vui lòng chuyển khoản vào tài khoản mới 9999888877 tại ngân hàng ABC",), bank="9999888877",
            ),
        }
        for name, content in samples.items():
            (folder / name).write_text(content, encoding="utf-8")
        print(f"Đã ghi {len(samples)} hoá đơn XML mẫu vào {folder.resolve()} để tải lên ở trang /finance")
    finally:
        db.close()


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else "admin@company.com")
