"""Payment vouchers and reminders: drafted by the agent, decided by a person."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.domains.finance.payments import DraftRefused, draft_payment_reminder, draft_payment_voucher
from app.domains.finance.reports import payment_schedule
from app.domains.finance.settings import get_settings, seed_chart
from app.models.models import FinInvoice, FinLedgerLine, FinParty, FinPayment, User, WorkflowApproval
from app.tools.executors.finance import draft_reminder, draft_voucher, list_finance_drafts
from app.tools.registry import ToolContext, tool_registry
from app.tools.schemas import DraftPaymentReminderInput, DraftPaymentVoucherInput, FinanceDraftsInput
from tests.finance_helpers import login, person, unique_tax_code


@pytest.fixture()
def tenant_id(transactional_db_session):
    db = transactional_db_session
    tenant = db.query(User).filter(User.email == "employee@company.com").one().tenant_id
    get_settings(db, tenant)
    seed_chart(db, tenant, "TT200")
    db.commit()
    return tenant


@pytest.fixture()
def clerk(transactional_db_session):
    return person(transactional_db_session, "finance.journal.draft", "finance.reminder.send", "finance.ar_ap.view")


@pytest.fixture()
def signer(transactional_db_session):
    return person(transactional_db_session, "finance.journal.approve", "finance.journal.approve_high")


def _party(db, tenant_id, kind, **values):
    party = FinParty(tenant_id=tenant_id, kind=kind, tax_code=unique_tax_code(), name=f"{kind} {uuid.uuid4().hex[:5]}", **values)
    db.add(party)
    db.commit()
    return party


def _posted(db, tenant_id, party, total, *, direction="IN", due=None, exceptions=None):
    invoice = FinInvoice(
        tenant_id=tenant_id, direction=direction, party_id=party.id,
        seller_tax_code=party.tax_code if direction == "IN" else "", seller_name=party.name,
        series="PC", number=str(uuid.uuid4().int)[:7], issue_date=date(2026, 9, 1),
        due_date=due or date(2026, 9, 15), status="POSTED", exceptions=exceptions or [],
        amount_before_tax=Decimal(total), vat_amount=Decimal("0"), total_amount=Decimal(total),
    )
    db.add(invoice)
    db.commit()
    return invoice


def test_a_voucher_goes_from_draft_to_scheduled_to_paid(client, tenant_id, clerk, signer, transactional_db_session):
    db = transactional_db_session
    vendor = _party(db, tenant_id, "VENDOR", bank_account="0011223344", bank_name="VCB")
    first, second = _posted(db, tenant_id, vendor, "6000000"), _posted(db, tenant_id, vendor, "9000000")
    approval = draft_payment_voucher(db, clerk, [first.id, second.id])
    db.commit()
    assert approval.payload["amount"] == "15000000.00"
    assert approval.payload["party"]["bank_account"] == "•••• 3344"
    assert approval.payload["required_permission"] == "finance.journal.approve"
    # One draft per invoice at a time.
    with pytest.raises(DraftRefused):
        draft_payment_voucher(db, clerk, [first.id])

    signed = client.post(f"/api/v1/approvals/{approval.id}/action", json={"action": "APPROVE"}, headers=login(client, signer))
    assert signed.status_code == 200, signed.text
    payments = db.query(FinPayment).filter(FinPayment.workflow_id == approval.workflow_id).all()
    assert {payment.status for payment in payments} == {"SCHEDULED"}

    paid = client.post(
        f"/api/v1/finance/payments/vouchers/{approval.workflow_id}/paid",
        json={"paid_on": "2026-09-20", "bank_reference": "VCB-778899"}, headers=login(client, clerk),
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["total"] == "15000000.00"
    db.refresh(first)
    db.refresh(second)
    assert first.status == second.status == "PAID"
    posted = db.query(FinLedgerLine).filter(FinLedgerLine.voucher_no.like("PC-%"), FinLedgerLine.party_id == vendor.id).all()
    assert [(line.account_code, line.debit) for line in posted] == [("331", Decimal("15000000.00"))]
    again = client.post(
        f"/api/v1/finance/payments/vouchers/{approval.workflow_id}/paid",
        json={"paid_on": "2026-09-20", "bank_reference": "VCB-778899"}, headers=login(client, clerk),
    )
    assert again.status_code == 409


def test_drafts_are_listed_with_their_status_and_whose_decision_they_wait_for(
    client, tenant_id, clerk, signer, transactional_db_session
):
    db = transactional_db_session
    vendor = _party(db, tenant_id, "VENDOR", bank_account="0099887766")
    voucher = draft_payment_voucher(db, clerk, [_posted(db, tenant_id, vendor, "4000000").id])
    db.commit()

    def drafts(user, **filters):
        request = FinanceDraftsInput.model_construct(
            tenant_id=tenant_id, audit=None, **{"kind": None, "status": None, "scope": "ALL", "limit": 20, **filters},
        )
        return list_finance_drafts(ToolContext(db=db, actor=user), request)

    mine = drafts(clerk, scope="MINE", kind="VOUCHER")
    [item] = [row for row in mine["drafts"] if row["approval_id"] == str(voucher.id)]
    assert (item["kind"], item["status"], item["amount"], item["payment"]) == ("Phiếu chi", "WAITING", "4000000.00", "chờ duyệt")
    # The requester never decides their own draft; the signer does.
    assert str(voucher.id) not in {row["approval_id"] for row in drafts(clerk, scope="TO_DECIDE")["drafts"]}
    assert str(voucher.id) in {row["approval_id"] for row in drafts(signer, scope="TO_DECIDE", limit=50)["drafts"]}
    signed = client.post(f"/api/v1/approvals/{voucher.id}/action", json={"action": "APPROVE"}, headers=login(client, signer))
    assert signed.status_code == 200, signed.text
    [item] = [row for row in drafts(clerk, scope="MINE", kind="VOUCHER")["drafts"] if row["approval_id"] == str(voucher.id)]
    # Approved is not paid: the transfer is still to be made.
    assert (item["status"], item["payment"]) == ("APPROVED", "đã duyệt, chưa chuyển tiền")


def test_a_voucher_is_for_one_vendor_and_posted_purchases_only(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    one, other = _party(db, tenant_id, "VENDOR"), _party(db, tenant_id, "VENDOR")
    with pytest.raises(DraftRefused):
        draft_payment_voucher(db, clerk, [_posted(db, tenant_id, one, "1000").id, _posted(db, tenant_id, other, "1000").id])
    customer = _party(db, tenant_id, "CUSTOMER")
    with pytest.raises(DraftRefused):
        draft_payment_voucher(db, clerk, [_posted(db, tenant_id, customer, "1000", direction="OUT").id])


def test_a_vendor_that_once_showed_another_account_is_flagged_on_the_voucher(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    vendor = _party(db, tenant_id, "VENDOR", bank_account="0011223344")
    invoice = _posted(db, tenant_id, vendor, "2000000", exceptions=[
        {"code": "BANK_ACCOUNT_DIFFERS", "message": "khác", "severity": "BLOCKING", "resolved_by": "A"},
    ])
    result = draft_voucher(
        ToolContext(db=db, actor=clerk),
        DraftPaymentVoucherInput.model_construct(tenant_id=tenant_id, audit=None, invoice_ids=[invoice.id]),
    )
    assert result["created"]
    assert "xác minh trực tiếp" in result["reply"]
    assert "không tự chuyển tiền" in result["reply"]


def test_a_reminder_is_only_sent_once_approved_and_keeps_its_figures(
    client, tenant_id, clerk, signer, transactional_db_session, monkeypatch
):
    db = transactional_db_session
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_MODE", "outbox")
    customer = _party(db, tenant_id, "CUSTOMER", email="ketoan@khachhang.vn")
    _posted(db, tenant_id, customer, "12000000", direction="OUT", due=date.today() - timedelta(days=40))
    result = draft_reminder(
        ToolContext(db=db, actor=clerk),
        DraftPaymentReminderInput.model_construct(tenant_id=tenant_id, audit=None, party=customer.tax_code, level=2),
    )
    assert result["created"], result
    # The AI service masks e-mail addresses in tool results; the reply must not need one.
    assert "ketoan@khachhang.vn" not in result["reply"]
    approval = db.get(WorkflowApproval, uuid.UUID(result["approval_id"]))
    assert "12.000.000 ₫" in approval.payload["body"]
    assert f"hạn {(date.today() - timedelta(days=40)).strftime('%d/%m/%Y')}" in approval.payload["body"]
    assert "delivery" not in approval.payload
    edited = client.post(
        f"/api/v1/approvals/{approval.id}/action",
        json={"action": "EDIT_AND_APPROVE", "edited_payload": {
            "subject": "Nhắc thanh toán", "recipient": "ke-gian@lua-dao.vn", "amount": "1",
        }},
        headers=login(client, signer),
    )
    assert edited.status_code == 200, edited.text
    db.refresh(approval)
    assert approval.payload["subject"] == "Nhắc thanh toán"
    assert approval.payload["recipient"] == "ketoan@khachhang.vn"
    assert approval.payload["delivery"]["status"] == "ACCEPTED"
    # Another letter within the week is allowed, and says so to whoever drafts and signs it.
    resent = draft_reminder(
        ToolContext(db=db, actor=clerk),
        DraftPaymentReminderInput.model_construct(tenant_id=tenant_id, audit=None, party=customer.tax_code, level=2),
    )
    assert resent["created"], resent
    assert "đã được gửi thư nhắc nợ mức 2" in resent["reply"]
    assert db.get(WorkflowApproval, uuid.UUID(resent["approval_id"])).payload["warnings"]


def test_a_reminder_needs_an_email_and_a_debt(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    no_email = _party(db, tenant_id, "CUSTOMER")
    with pytest.raises(DraftRefused):
        draft_payment_reminder(db, clerk, no_email, 1, company_name="X", as_of=date.today())
    no_debt = _party(db, tenant_id, "CUSTOMER", email="a@b.vn")
    with pytest.raises(DraftRefused):
        draft_payment_reminder(db, clerk, no_debt, 2, company_name="X", as_of=date.today())


def test_drafting_tools_are_terminal_and_gated():
    for name, code in (("draft_payment_voucher", "finance.journal.draft"), ("draft_payment_reminder", "finance.reminder.send")):
        definition = tool_registry.get(name)
        assert definition.terminal and definition.opens_approval
        assert definition.acl.permission == code


def test_a_waiting_voucher_shows_on_the_schedule_and_can_be_withdrawn(
    client, tenant_id, clerk, signer, transactional_db_session
):
    db = transactional_db_session
    vendor = _party(db, tenant_id, "VENDOR", bank_account="0011223344")
    invoice = _posted(db, tenant_id, vendor, "7000000", due=date(2039, 5, 10))
    approval = draft_payment_voucher(db, clerk, [invoice.id])
    db.commit()
    row = next(
        row for row in payment_schedule(db, tenant_id, date(2039, 5, 1), 30)["invoices"]
        if row["invoice_id"] == str(invoice.id)
    )
    # Already in a voucher: a second one would be refused, so nothing is left to schedule.
    assert (row["in_pending_voucher"], row["to_schedule"]) == ("7000000.00", "0.00")

    mine = client.get("/api/v1/approvals/submitted", headers=login(client, clerk)).json()
    assert next(item for item in mine if item["id"] == str(approval.id))["can_withdraw"]
    bystander = person(db, "finance.journal.draft", "finance.journal.approve")
    refused = client.post(f"/api/v1/finance/approvals/{approval.id}/withdraw", json={}, headers=login(client, bystander))
    assert refused.status_code == 403

    taken_back = client.post(
        f"/api/v1/finance/approvals/{approval.id}/withdraw", json={"reason": "Nhầm hoá đơn"}, headers=login(client, clerk),
    )
    assert taken_back.status_code == 200, taken_back.text
    db.expire_all()
    assert db.get(WorkflowApproval, approval.id).status == "WITHDRAWN"
    assert {p.status for p in db.query(FinPayment).filter(FinPayment.workflow_id == approval.workflow_id)} == {"CANCELLED"}
    late = client.post(f"/api/v1/approvals/{approval.id}/action", json={"action": "APPROVE"}, headers=login(client, signer))
    assert late.status_code == 409
    # The invoice is free again.
    assert draft_payment_voucher(db, clerk, [invoice.id]).payload["amount"] == "7000000.00"


def test_the_top_signer_can_withdraw_a_reminder_and_the_customer_can_be_reminded_again(
    client, tenant_id, clerk, signer, transactional_db_session
):
    db = transactional_db_session
    customer = _party(db, tenant_id, "CUSTOMER", email="kt@khach.vn")
    _posted(db, tenant_id, customer, "4000000", direction="OUT", due=date.today() - timedelta(days=10))
    approval = draft_payment_reminder(db, clerk, customer, 2, company_name="X", as_of=date.today())
    db.commit()
    with pytest.raises(DraftRefused):
        draft_payment_reminder(db, clerk, customer, 2, company_name="X", as_of=date.today())
    taken_back = client.post(f"/api/v1/finance/approvals/{approval.id}/withdraw", json={}, headers=login(client, signer))
    assert taken_back.status_code == 200, taken_back.text
    db.expire_all()
    assert draft_payment_reminder(db, clerk, customer, 2, company_name="X", as_of=date.today()).status == "WAITING"


def test_a_reminder_lists_every_invoice_its_total_counts(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    customer = _party(db, tenant_id, "CUSTOMER", email="nhieu@khach.vn")
    for _ in range(55):
        _posted(db, tenant_id, customer, "1000", direction="OUT", due=date.today() - timedelta(days=5))
    approval = draft_payment_reminder(db, clerk, customer, 2, company_name="X", as_of=date.today())
    assert approval.payload["body"].count("- Hoá đơn") == 55


def test_a_refused_duplicate_names_the_waiting_draft_and_how_to_redo_it(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    vendor = _party(db, tenant_id, "VENDOR", bank_account="0011223344")
    invoice = _posted(db, tenant_id, vendor, "3000000")
    context = ToolContext(db=db, actor=clerk)
    request = DraftPaymentVoucherInput.model_construct(tenant_id=tenant_id, audit=None, invoice_ids=[invoice.id])
    assert draft_voucher(context, request)["created"]
    again = draft_voucher(context, request)
    assert again["status"] == "REFUSED"
    assert again["reply"].startswith("Chưa lập được phiếu chi. Hoá đơn PC số ")
    assert "Duyệt phiếu chi 3.000.000 ₫" in again["reply"] and "Tôi đã gửi" in again["reply"]
    assert again["reply"].endswith(".")


def test_a_reminder_without_a_level_matches_how_overdue_the_debt_is(tenant_id, clerk, transactional_db_session):
    db = transactional_db_session
    late = _party(db, tenant_id, "CUSTOMER", email="tre@khach.vn")
    _posted(db, tenant_id, late, "2000000", direction="OUT", due=date.today() - timedelta(days=5))
    on_time = _party(db, tenant_id, "CUSTOMER", email="dung@khach.vn")
    _posted(db, tenant_id, on_time, "2000000", direction="OUT", due=date.today())
    assert draft_payment_reminder(db, clerk, late, None, company_name="X", as_of=date.today()).payload["level"] == 2
    assert draft_payment_reminder(db, clerk, on_time, None, company_name="X", as_of=date.today()).payload["level"] == 1
    # What the user asked for still wins.
    asked = _party(db, tenant_id, "CUSTOMER", email="hoi@khach.vn")
    _posted(db, tenant_id, asked, "2000000", direction="OUT", due=date.today() - timedelta(days=5))
    reply = draft_reminder(ToolContext(db=db, actor=clerk), DraftPaymentReminderInput.model_construct(
        tenant_id=tenant_id, audit=None, party=asked.tax_code, level=3,
    ))
    assert reply["reply"].startswith("Đã soạn thư nhắc nợ mức 3")
