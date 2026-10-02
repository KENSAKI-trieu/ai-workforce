"""The Finance agent's books: its permissions, Excel imports and amount-based approvals."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.finance_capabilities import (
    FINANCE_CONFIGURATION_VERSION,
    finance_default_tools,
    upgrade_finance_grants,
)
from app.core.permissions import SPECIALISED_POSITIONS, legacy_role_for_position
from app.domains.finance.approvals import (
    JOURNAL_APPROVAL,
    can_approve_finance,
    open_finance_approval,
)
from app.domains.finance.money import format_vnd, normalize_tax_code, to_decimal
from app.domains.finance.settings import get_settings, required_permission
from app.domains.platform.approval_access import can_approve, eligible_approvers
from app.models.models import AIAgent, FinAccount, FinBudget, FinLedgerLine, User
from app.tools.executors.approvals import submit_approval_request
from app.tools.registry import ToolContext
from app.tools.schemas import SubmitApprovalInput
from tests.finance_helpers import login, person, unique_tax_code, upload, xlsx

FINANCE_CODES = (
    "finance.ledger.view",
    "finance.invoice.process",
    "finance.journal.draft",
    "finance.journal.approve",
    "finance.journal.approve_high",
    "finance.ar_ap.view",
    "finance.reminder.send",
    "finance.import.manage",
)


# --------------------------------------------------------------------------- money


@pytest.mark.parametrize("raw, expected", [
    ("1.234.567", "1234567.00"),
    ("1,234,567", "1234567.00"),
    ("1 234 567,50", "1234567.50"),
    ("12.5", "12.50"),
    ("(2.000)", "-2000.00"),
    (1234567, "1234567.00"),
    ("15.000.000 VNĐ", "15000000.00"),
    ("0,125", "0.13"),
    ("0.5", "0.50"),
])
def test_amounts_are_read_the_way_vietnamese_books_write_them(raw, expected):
    assert str(to_decimal(raw)) == expected


def test_amounts_are_shown_with_dots_and_the_dong_sign():
    assert format_vnd(Decimal("1234567")) == "1.234.567 ₫"


def test_tax_codes_accept_branches_and_reject_junk():
    assert normalize_tax_code("0101234567") == "0101234567"
    assert normalize_tax_code("0101234567001") == "0101234567-001"
    with pytest.raises(ValueError):
        normalize_tax_code("12345")


# --------------------------------------------------------------------------- permissions


def test_finance_boxes_never_promote_a_position_to_admin():
    # Everything in _ADMIN_CORE a Manager lacks is an admin marker; finance stays out of it.
    assert legacy_role_for_position("custom", False, FINANCE_CODES) == "Employee"


def test_finance_positions_carry_their_boxes():
    by_slug = {item.slug: set(item.permissions) for item in SPECIALISED_POSITIONS}
    assert set(FINANCE_CODES) <= by_slug["finance-admin"]
    assert "finance.journal.approve" in by_slug["finance-manager"]
    assert "finance.journal.approve_high" not in by_slug["finance-manager"]
    assert "finance.import.manage" not in by_slug["finance-manager"]


def test_finance_routes_check_their_box(client, transactional_db_session):
    db = transactional_db_session
    outsider = person(db)
    assert client.get("/api/v1/finance/settings", headers=login(client, outsider)).status_code == 403
    reader = person(db, "finance.ledger.view")
    headers = login(client, reader)
    assert client.get("/api/v1/finance/settings", headers=headers).status_code == 200
    # Reading the books is not changing them.
    assert client.put("/api/v1/finance/settings", json={"chart": "TT133"}, headers=headers).status_code == 403


# --------------------------------------------------------------------------- imports


@pytest.fixture()
def manager_headers(client, transactional_db_session):
    user = person(transactional_db_session, *FINANCE_CODES)
    headers = login(client, user)
    response = client.put("/api/v1/finance/settings", json={"chart": "TT200"}, headers=headers)
    assert response.status_code == 200, response.text
    return headers


def test_choosing_a_chart_seeds_its_accounts_without_touching_existing_ones(
    client, manager_headers, transactional_db_session
):
    db = transactional_db_session
    tenant_id = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    account = db.query(FinAccount).filter(FinAccount.tenant_id == tenant_id, FinAccount.code == "6422").one()
    account.name = "Tên công ty tự đặt"
    db.commit()
    response = client.put("/api/v1/finance/settings", json={"chart": "TT200"}, headers=manager_headers)
    assert response.json()["accounts_added"] == 0
    db.refresh(account)
    assert account.name == "Tên công ty tự đặt"
    accounts = {row["code"]: row for row in client.get("/api/v1/finance/accounts", headers=manager_headers).json()}
    assert accounts["331"]["normal_balance"] == "BOTH"
    assert accounts["214"]["normal_balance"] == "CREDIT"
    assert accounts["1331"]["parent_code"] == "133"


LEDGER_HEADER = ("Ngày hạch toán", "Số chứng từ", "Diễn giải", "Số TK", "Phát sinh Nợ", "Phát sinh Có", "Phòng ban")


def test_a_balanced_ledger_imports_and_can_be_undone(client, manager_headers, transactional_db_session):
    data = xlsx(LEDGER_HEADER, [
        ("05/09/2026", "PC001", "Mua văn phòng phẩm", "6423", "1.000.000", None, "MARKETING"),
        ("05/09/2026", "PC001", "Mua văn phòng phẩm", "1331", "100.000", None, None),
        ("05/09/2026", "PC001", "Mua văn phòng phẩm", "1111", None, "1.100.000", None),
    ])
    response = upload(client, manager_headers, "ledger", data)
    assert response.status_code == 200, response.text
    batch_id = response.json()["id"]
    lines = transactional_db_session.query(FinLedgerLine).filter(
        FinLedgerLine.import_batch_id == uuid.UUID(batch_id)
    ).all()
    assert {(line.account_code, line.period) for line in lines} == {
        ("6423", "2026-09"), ("1331", "2026-09"), ("1111", "2026-09"),
    }
    undone = client.delete(f"/api/v1/finance/import/batches/{batch_id}", headers=manager_headers)
    assert undone.status_code == 200, undone.text
    assert undone.json()["rows_removed"] == 3
    assert client.delete(f"/api/v1/finance/import/batches/{batch_id}", headers=manager_headers).status_code == 409


def test_an_unbalanced_voucher_rejects_the_whole_file(client, manager_headers, transactional_db_session):
    before = transactional_db_session.query(FinLedgerLine).count()
    data = xlsx(LEDGER_HEADER, [
        ("05/09/2026", "PC002", "Chi tiền", "6428", "500.000", None, None),
        ("05/09/2026", "PC002", "Chi tiền", "1111", None, "400.000", None),
        ("06/09/2026", "PC003", "TK lạ", "9999", "1", None, None),
    ])
    response = upload(client, manager_headers, "ledger", data)
    assert response.status_code == 422
    messages = " ".join(error["message"] for error in response.json()["detail"]["errors"])
    assert "PC002" in messages
    assert "9999" in messages
    assert transactional_db_session.query(FinLedgerLine).count() == before


def test_a_missing_required_column_is_named(client, manager_headers):
    response = upload(client, manager_headers, "ledger", xlsx(("Diễn giải",), [("x",)]))
    assert response.status_code == 422
    columns = {error["column"] for error in response.json()["detail"]["errors"]}
    assert {"Ngày hạch toán", "Số TK"} <= columns


def test_reimporting_a_budget_line_replaces_it(client, manager_headers, transactional_db_session):
    header = ("Phòng ban", "Số TK", "Kỳ (YYYY-MM)", "Số tiền")
    first = upload(client, manager_headers, "budgets", xlsx(header, [("MARKETING", "6428", "2026-09", "50.000.000")]))
    assert first.status_code == 200, first.text
    second = upload(client, manager_headers, "budgets", xlsx(header, [("marketing", "6428", "09/2026", "70.000.000")]))
    assert second.status_code == 200, second.text
    rows = transactional_db_session.query(FinBudget).filter(
        FinBudget.department == "MARKETING", FinBudget.account_code == "6428", FinBudget.period == "2026-09",
    ).all()
    assert [row.amount for row in rows] == [Decimal("70000000.00")]


def test_parties_import_needs_a_valid_tax_code(client, manager_headers):
    header = ("Loại (NCC/KH/CẢ HAI)", "MST", "Tên")
    good = unique_tax_code()
    response = upload(client, manager_headers, "parties", xlsx(header, [
        ("NCC", good, "Công ty A"),
        ("KH", "123", "Công ty B"),
    ]))
    assert response.status_code == 422
    assert any(error["row"] == 3 for error in response.json()["detail"]["errors"])
    response = upload(client, manager_headers, "parties", xlsx(header, [("NCC", good, "Công ty A")]))
    assert response.status_code == 200, response.text
    parties = client.get("/api/v1/finance/parties", headers=manager_headers).json()
    assert any(party["tax_code"] == good and party["kind"] == "VENDOR" for party in parties)


def test_the_template_round_trips_its_own_header(client, manager_headers):
    template = client.get("/api/v1/finance/import/budgets/template", headers=manager_headers)
    assert template.status_code == 200
    response = upload(client, manager_headers, "budgets", template.content)
    # Header recognised; the only complaint is that there is nothing under it.
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["message"] == "File không có dòng dữ liệu nào"


# --------------------------------------------------------------------------- approvals


@pytest.mark.parametrize("amount, permission, risk", [
    ("19999999", "finance.journal.approve", "MEDIUM"),
    ("20000000", "finance.journal.approve", "MEDIUM"),
    ("20000001", "finance.journal.approve_high", "HIGH"),
    ("500000000", "finance.journal.approve_high", "HIGH"),
    ("500000001", "approvals.sign_critical", "CRITICAL"),
])
def test_the_amount_decides_who_signs(amount, permission, risk, transactional_db_session):
    db = transactional_db_session
    tenant_id = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    assert required_permission(get_settings(db, tenant_id), Decimal(amount)) == (permission, risk)


def test_a_finance_draft_is_signed_by_the_right_level_and_never_by_its_requester(transactional_db_session):
    db = transactional_db_session
    requester = person(db, "finance.journal.draft", "finance.journal.approve_high")
    accountant = person(db, "finance.journal.approve")
    chief = person(db, "finance.journal.approve_high")
    approval = open_finance_approval(
        db, requester, action_type=JOURNAL_APPROVAL, title="Bút toán thử",
        amount=Decimal("120000000"), record_id=str(uuid.uuid4()),
        payload={"required_permission": "finance.journal.approve", "amount": "1"},
    )
    db.commit()
    # The caller's payload could not lower the bar.
    assert approval.payload["required_permission"] == "finance.journal.approve_high"
    assert approval.payload["amount"] == "120000000.00"
    assert not can_approve(db, requester, approval)
    assert not can_approve(db, accountant, approval)
    assert can_approve(db, chief, approval)
    assert chief.id in {user.id for user in eligible_approvers(db, approval)}
    assert can_approve_finance(db, chief, approval)


def test_the_generic_approval_tool_cannot_open_a_finance_approval(transactional_db_session):
    # Finance drafts are signed by amount, which only their own tools compute: the generic
    # tool can neither name a finance action nor smuggle in the keys that set the bar.
    with pytest.raises(ValidationError) as invalid:
        SubmitApprovalInput(title="Lách ngưỡng", action_type="FINANCE_JOURNAL_APPROVAL", payload={})
    assert any(error["loc"] == ("action_type",) for error in invalid.value.errors())
    actor = person(transactional_db_session)
    request = SubmitApprovalInput.model_construct(
        title="Lách ngưỡng", action_type="EXPENSE_APPROVAL", risk_level="MEDIUM",
        payload={"required_permission": "finance.journal.approve"}, approver_id=None, expires_at=None,
    )
    with pytest.raises(HTTPException) as raised:
        submit_approval_request(ToolContext(db=transactional_db_session, actor=actor), request)
    assert raised.value.status_code == 422


# --------------------------------------------------------------------------- agent grants


def test_a_placeholder_finance_agent_catches_up_once_and_keeps_later_choices():
    agent = AIAgent(
        role_code="FINANCE", tools_access=["reconcile_po_db", "expense_lookup", "rag_search"],
        allowed_actions=["reconcile_po_db", "expense_lookup", "rag_search"],
        disallowed_actions=[], configuration_version=9,
    )
    assert upgrade_finance_grants(agent)
    assert set(agent.tools_access) == set(finance_default_tools())
    assert agent.configuration_version == FINANCE_CONFIGURATION_VERSION
    agent.tools_access = []
    assert not upgrade_finance_grants(agent)
    assert agent.tools_access == []
