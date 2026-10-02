"""Payment vouchers and reminders: drafted by the agent, decided by a person."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.domains.finance.payments import DraftRefused, draft_payment_reminder, draft_payment_voucher
from app.domains.finance.settings import get_settings, seed_chart
from app.models.models import FinInvoice, FinLedgerLine, FinParty, FinPayment, User, WorkflowApproval
from app.tools.executors.finance import draft_reminder, draft_voucher
from app.tools.registry import ToolContext, tool_registry
from app.tools.schemas import DraftPaymentReminderInput, DraftPaymentVoucherInput
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
