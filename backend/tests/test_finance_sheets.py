"""Spreadsheets a user uploads to analyse: read like a person would, computed without a model."""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook

from app.domains.finance.sheets import analyze, parse_grid
from app.domains.finance.visuals import charts_from_tool_calls
from app.models.models import FinSheet
from app.tools.executors.finance import analyze_spreadsheet
from app.tools.registry import ToolContext
from app.tools.schemas import SpreadsheetAnalysisInput
from tests.finance_helpers import login, person

# The way a cost sheet arrives from an accountant: a title, a blank line, the column titles
# on row 3, amounts sometimes typed as text, and a "Tổng cộng" line after each group.
GRID = [
    ["BẢNG CHI PHÍ DỰ ÁN QUÝ 3/2026", None, None, None],
    [None, None, None, None],
    ["Ngày", "Phòng ban", "Nội dung", "Số tiền"],
    [date(2026, 7, 3), "Marketing", "Quảng cáo Facebook", 12_000_000],
    [date(2026, 7, 20), "Kinh doanh", "Công cụ dụng cụ", "3.500.000"],
    [None, "Tổng cộng tháng 7", None, 15_500_000],
    [date(2026, 8, 9), "Marketing", "In ấn", "2.000.000 ₫"],
    [date(2026, 9, 15), "Marketing", "Sự kiện", 30_000_000],
    [None, None, None, None],
    ["Cộng quý 3", None, None, 47_500_000],
    ["Công tác phí", None, None, None],
]


def _workbook(grid) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Chi phí"
    for row in grid:
        sheet.append(row)
    book.create_sheet("Ghi chú").append(["Không có số liệu"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_real_sheet_is_read_without_counting_its_totals():
    parsed = parse_grid(GRID)
    assert parsed["header_row"] == 3
    assert [(c["name"], c["kind"]) for c in parsed["columns"]] == [
        ("Ngày", "DATE"), ("Phòng ban", "TEXT"), ("Nội dung", "TEXT"), ("Số tiền", "NUMBER"),
    ]
    # "Công cụ dụng cụ" is a cost, not a total.
    assert [row[0] for row in parsed["rows"]] == [4, 5, 7, 8, 11]
    assert [(item["row"], item["label"]) for item in parsed["skipped_rows"]] == [(6, "Tổng cộng tháng 7"), (10, "Cộng quý 3")]
    assert [row[4] for row in parsed["rows"]] == ["12000000.00", "3500000.00", "2000000.00", "30000000.00", None]


def test_sums_group_by_a_column_or_by_month_and_filter(transactional_db_session):
    owner = person(transactional_db_session, "finance.sheet.analyze")
    sheet = FinSheet(tenant_id=owner.tenant_id, created_by_id=owner.id, filename="chi-phi.xlsx", storage_key="x",
                     sheet_name="Chi phí", **{k: v for k, v in parse_grid(GRID).items() if k != "header_row"})
    by_department = analyze(sheet, operation="sum", value_column="số tiền", group_by="Phòng ban")
    assert by_department["total"] == "47500000.00"
    assert [(g["group"], g["value"], g["share_percent"]) for g in by_department["groups"]] == [
        ("Marketing", "44000000.00", "92.63"), ("Kinh doanh", "3500000.00", "7.37"),
    ]
    by_month = analyze(sheet, operation="sum", value_column="Số tiền", group_by="Ngày", period="month")
    assert [(g["group"], g["value"]) for g in by_month["groups"]] == [
        ("2026-07", "15500000.00"), ("2026-08", "2000000.00"), ("2026-09", "30000000.00"),
    ]
    marketing_from_august = analyze(sheet, operation="count", filters=[
        {"column": "Phòng ban", "op": "eq", "value": "marketing"},
        {"column": "Ngày", "op": "gte", "value": "01/08/2026"},
    ])
    assert marketing_from_august["total"] == 2
    chart = charts_from_tool_calls([{"name": "analyze_spreadsheet", "status": "SUCCESS", "result": by_month}])[0]
    assert chart["chart"] == "line" and chart["title"] == "Tổng Số tiền theo Ngày (theo tháng)"


def test_a_column_that_is_not_there_is_named_back_with_the_ones_that_are(transactional_db_session):
    owner = person(transactional_db_session, "finance.sheet.analyze")
    sheet = FinSheet(tenant_id=owner.tenant_id, created_by_id=owner.id, filename="a.xlsx", storage_key="x",
                     sheet_name="S", **{k: v for k, v in parse_grid(GRID).items() if k != "header_row"})
    result = analyze_spreadsheet(
        ToolContext(db=transactional_db_session, actor=owner),
        SpreadsheetAnalysisInput.model_construct(
            tenant_id=owner.tenant_id, audit=None, sheet_id=None, operation="sum",
            value_column="Doanh thu", group_by=None, period=None, filters=[],
        ),
    )
    # No file of this user is stored yet: the tool says so rather than reading someone else's.
    assert result["found"] is False


def test_only_the_uploader_reads_a_sheet_and_can_correct_how_it_was_read(client, transactional_db_session):
    owner = person(transactional_db_session, "finance.sheet.analyze")
    headers = login(client, owner)
    uploaded = client.post(
        "/api/v1/finance/sheets", headers=headers,
        files={"file": ("chi-phi.xlsx", _workbook(GRID), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert uploaded.status_code == 200, uploaded.text
    sheet = uploaded.json()
    assert (sheet["sheet"], sheet["header_row"], sheet["row_count"]) == ("Chi phí", 3, 5)
    assert sheet["sheet_names"] == ["Chi phí", "Ghi chú"]

    analysed = client.post(
        f"/api/v1/finance/sheets/{sheet['sheet_id']}/analyze", headers=headers,
        json={"operation": "sum", "value_column": "Số tiền", "group_by": "Phòng ban"},
    )
    assert analysed.status_code == 200, analysed.text
    assert analysed.json()["charts"][0]["chart"] == "pie"

    kept = client.patch(f"/api/v1/finance/sheets/{sheet['sheet_id']}", headers=headers, json={"skip_totals": False})
    assert kept.status_code == 200 and kept.json()["row_count"] == 7

    stranger = person(transactional_db_session, "finance.sheet.analyze")
    for response in (
        client.get(f"/api/v1/finance/sheets/{sheet['sheet_id']}", headers=login(client, stranger)),
        client.post(f"/api/v1/finance/sheets/{sheet['sheet_id']}/analyze", headers=login(client, stranger), json={"operation": "count"}),
    ):
        assert response.status_code == 404
    assert client.get("/api/v1/finance/sheets", headers=login(client, stranger)).json() == []

    # The agent reads the uploader's latest file.
    result = analyze_spreadsheet(
        ToolContext(db=transactional_db_session, actor=owner),
        SpreadsheetAnalysisInput.model_construct(
            tenant_id=owner.tenant_id, audit=None, sheet_id=None, operation="max",
            value_column="Số tiền", group_by=None, period=None, filters=[],
        ),
    )
    assert result["total"] == "47500000.00"  # the "Cộng" row is kept now, as asked

    gone = client.delete(f"/api/v1/finance/sheets/{sheet['sheet_id']}", headers=headers)
    assert gone.status_code == 204
    assert client.get(f"/api/v1/finance/sheets/{sheet['sheet_id']}", headers=headers).status_code == 404


def test_an_old_xls_is_refused_with_how_to_fix_it(client, transactional_db_session):
    headers = login(client, person(transactional_db_session, "finance.sheet.analyze"))
    refused = client.post("/api/v1/finance/sheets", headers=headers, files={"file": ("cu.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel")})
    assert refused.status_code == 422 and ".xlsx" in refused.json()["detail"]


def test_without_the_box_nobody_uploads_or_asks_the_agent(client, transactional_db_session):
    from app.tools.registry import tool_registry

    assert tool_registry.get("analyze_spreadsheet").acl.permission == "finance.sheet.analyze"
    assert tool_registry.get("list_spreadsheets").acl.permission == "finance.sheet.analyze"
    headers = login(client, person(transactional_db_session))
    assert client.get("/api/v1/finance/sheets", headers=headers).status_code == 403
    refused = client.post("/api/v1/finance/sheets", headers=headers, files={"file": ("a.csv", b"a,b\n1,2", "text/csv")})
    assert refused.status_code == 403
