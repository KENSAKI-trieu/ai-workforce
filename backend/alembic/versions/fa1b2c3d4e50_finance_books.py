"""finance books: chart, parties, invoices, journal drafts, ledger, budgets, posting rules

The Finance agent leaves "under development" with books of its own to read: a company
imports its chart, ledger, parties and budgets from the Excel its accounting software
exports, and uploads its e-invoices. The agent drafts journal entries and payment
vouchers; a person approves them.

The finance permissions are granted here to the positions that already did the job by
their slug -- the finance specialisations and the `ceo` job -- and "Xem ngân sách phòng
mình" to every position that could already look up expenses. The sets are copied from
app/core/permissions.py as of this revision so later changes there cannot rewrite history.

Revision ID: fa1b2c3d4e50
Revises: z19a4c6d0e28
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "fa1b2c3d4e50"
down_revision = "z19a4c6d0e28"
branch_labels = None
depends_on = None

FINANCE_STAFF = {
    "finance.ledger.view",
    "finance.invoice.process",
    "finance.journal.draft",
    "finance.ar_ap.view",
    "finance.reminder.send",
}
FINANCE_MANAGER = FINANCE_STAFF | {"finance.journal.approve"}
FINANCE_ADMIN = FINANCE_MANAGER | {"finance.journal.approve_high", "finance.import.manage"}
BY_SLUG = {
    "ceo": FINANCE_ADMIN,
    "finance-admin": FINANCE_ADMIN,
    "finance-manager": FINANCE_MANAGER,
}
NEW_CODES = FINANCE_ADMIN | {"finance.budget.view_own"}

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(18, 2)


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def _tenant() -> sa.Column:
    return sa.Column(
        "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _fk(name: str, target: str, ondelete: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, UUID, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _codes(value) -> set[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return {str(code) for code in (value or [])}


def upgrade() -> None:
    op.create_table(
        "fin_settings",
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("chart", sa.String(10), nullable=False, server_default="TT200"),
        sa.Column("company_tax_code", sa.String(14)),
        sa.Column("approval_thresholds", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("po_tolerance_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "fin_import_batches",
        _id(),
        _tenant(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMMITTED"),
        _fk("created_by_id", "users.id", "SET NULL"),
        _created(),
    )
    op.create_index("idx_fin_import_batches_tenant", "fin_import_batches", ["tenant_id", "created_at"])
    op.create_table(
        "fin_accounts",
        _id(),
        _tenant(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_code", sa.String(20)),
        sa.Column("normal_balance", sa.String(10), nullable=False, server_default="DEBIT"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fin_account_code"),
    )
    op.create_table(
        "fin_parties",
        _id(),
        _tenant(),
        sa.Column("kind", sa.String(10), nullable=False, server_default="VENDOR"),
        sa.Column("tax_code", sa.String(14)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("email", sa.String(255)),
        sa.Column("bank_account", sa.Text),
        sa.Column("bank_name", sa.String(255)),
        sa.Column("payment_terms_days", sa.Integer, nullable=False, server_default="30"),
        sa.Column("is_new", sa.Boolean, nullable=False, server_default=sa.false()),
        _fk("import_batch_id", "fin_import_batches.id", "SET NULL"),
        _created(),
        sa.UniqueConstraint("tenant_id", "tax_code", name="uq_fin_party_tax_code"),
    )
    op.create_index("idx_fin_parties_tenant_name", "fin_parties", ["tenant_id", "name"])
    op.create_table(
        "fin_purchase_orders",
        _id(),
        _tenant(),
        sa.Column("po_number", sa.String(50), nullable=False),
        _fk("party_id", "fin_parties.id", "SET NULL"),
        sa.Column("order_date", sa.Date),
        sa.Column("currency", sa.String(3), nullable=False, server_default="VND"),
        sa.Column("amount_before_tax", MONEY, nullable=False, server_default="0"),
        sa.Column("vat_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("total_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("lines", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        _fk("import_batch_id", "fin_import_batches.id", "SET NULL"),
        _created(),
        sa.UniqueConstraint("tenant_id", "po_number", name="uq_fin_po_number"),
    )
    op.create_table(
        "fin_invoices",
        _id(),
        _tenant(),
        sa.Column("direction", sa.String(3), nullable=False, server_default="IN"),
        _fk("party_id", "fin_parties.id", "SET NULL"),
        sa.Column("seller_tax_code", sa.String(14), nullable=False, server_default=""),
        sa.Column("seller_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("buyer_tax_code", sa.String(14)),
        sa.Column("buyer_name", sa.String(255)),
        sa.Column("template_code", sa.String(20)),
        sa.Column("series", sa.String(20), nullable=False, server_default=""),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("issue_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("currency", sa.String(3), nullable=False, server_default="VND"),
        sa.Column("exchange_rate", sa.Numeric(18, 4), nullable=False, server_default="1"),
        sa.Column("amount_before_tax", MONEY, nullable=False, server_default="0"),
        sa.Column("vat_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("total_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("vat_breakdown", postgresql.JSONB, nullable=False, server_default="{}"),
        # EncryptedJSONB is JSONB holding a sealed string when a key is configured.
        sa.Column("lines", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("tax_authority_code", sa.String(64)),
        sa.Column("po_number", sa.String(50)),
        _fk("po_id", "fin_purchase_orders.id", "SET NULL"),
        sa.Column("status", sa.String(20), nullable=False, server_default="RECEIVED"),
        sa.Column("exceptions", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("source_format", sa.String(10), nullable=False, server_default="XML"),
        sa.Column("source_filename", sa.String(255)),
        sa.Column("source_storage_key", sa.Text),
        sa.Column("content_hash", sa.String(64)),
        _fk("import_batch_id", "fin_import_batches.id", "SET NULL"),
        _fk("created_by_id", "users.id", "SET NULL"),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "direction", "seller_tax_code", "series", "number",
            name="uq_fin_invoice_identity",
        ),
    )
    op.create_index("idx_fin_invoices_tenant_status", "fin_invoices", ["tenant_id", "status"])
    op.create_index("idx_fin_invoices_tenant_party", "fin_invoices", ["tenant_id", "party_id"])
    op.create_table(
        "fin_journal_entries",
        _id(),
        _tenant(),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False, server_default="INVOICE"),
        _fk("invoice_id", "fin_invoices.id", "SET NULL"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING_APPROVAL"),
        sa.Column("proposed_by", sa.String(10), nullable=False, server_default="MODEL"),
        sa.Column("confidence", sa.String(10), nullable=False, server_default="LOW"),
        sa.Column("confidence_reasons", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("corrections", postgresql.JSONB),
        _fk("workflow_id", "agent_workflows.id", "SET NULL"),
        _fk("created_by_id", "users.id", "SET NULL"),
        _fk("approved_by_id", "users.id", "SET NULL"),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        _created(),
    )
    op.create_index("idx_fin_journal_tenant_status", "fin_journal_entries", ["tenant_id", "status"])
    op.create_index(
        "uq_fin_journal_live_invoice",
        "fin_journal_entries",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING_APPROVAL', 'POSTED')"),
    )
    op.create_table(
        "fin_journal_lines",
        _id(),
        _fk("entry_id", "fin_journal_entries.id", "CASCADE", nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("account_code", sa.String(20), nullable=False),
        sa.Column("debit", MONEY, nullable=False, server_default="0"),
        sa.Column("credit", MONEY, nullable=False, server_default="0"),
        _fk("party_id", "fin_parties.id", "SET NULL"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("debit >= 0 AND credit >= 0", name="ck_fin_journal_line_non_negative"),
    )
    op.create_table(
        "fin_ledger_lines",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("account_code", sa.String(20), nullable=False),
        sa.Column("debit", MONEY, nullable=False, server_default="0"),
        sa.Column("credit", MONEY, nullable=False, server_default="0"),
        _fk("party_id", "fin_parties.id", "SET NULL"),
        sa.Column("department", sa.String(50)),
        sa.Column("voucher_no", sa.String(50)),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(10), nullable=False, server_default="IMPORT"),
        _fk("journal_entry_id", "fin_journal_entries.id", "SET NULL"),
        _fk("import_batch_id", "fin_import_batches.id", "CASCADE"),
        sa.CheckConstraint("debit >= 0 AND credit >= 0", name="ck_fin_ledger_line_non_negative"),
    )
    op.create_index(
        "idx_fin_ledger_account_period", "fin_ledger_lines", ["tenant_id", "account_code", "period"]
    )
    op.create_index("idx_fin_ledger_party", "fin_ledger_lines", ["tenant_id", "party_id"])
    op.create_table(
        "fin_payments",
        _id(),
        _tenant(),
        _fk("invoice_id", "fin_invoices.id", "CASCADE"),
        _fk("party_id", "fin_parties.id", "SET NULL"),
        sa.Column("direction", sa.String(10), nullable=False, server_default="PAY"),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("payment_date", sa.Date),
        sa.Column("status", sa.String(20), nullable=False, server_default="PAID"),
        sa.Column("reference", sa.String(100)),
        _fk("workflow_id", "agent_workflows.id", "SET NULL"),
        _fk("import_batch_id", "fin_import_batches.id", "CASCADE"),
        _fk("created_by_id", "users.id", "SET NULL"),
        _created(),
    )
    op.create_index("idx_fin_payments_invoice", "fin_payments", ["tenant_id", "invoice_id"])
    op.create_table(
        "fin_budgets",
        _id(),
        _tenant(),
        sa.Column("department", sa.String(50), nullable=False),
        sa.Column("account_code", sa.String(20), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        _fk("import_batch_id", "fin_import_batches.id", "CASCADE"),
        sa.UniqueConstraint(
            "tenant_id", "department", "account_code", "period", name="uq_fin_budget_line"
        ),
    )
    op.create_table(
        "fin_posting_rules",
        _id(),
        _tenant(),
        _fk("party_id", "fin_parties.id", "CASCADE", nullable=False),
        sa.Column("direction", sa.String(3), nullable=False, server_default="IN"),
        sa.Column("main_account", sa.String(20), nullable=False),
        sa.Column("times_confirmed", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "party_id", "direction", name="uq_fin_posting_rule"),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, slug, grants_all, permissions FROM positions"
    )).all()
    for position_id, slug, grants_all, permissions in rows:
        if grants_all:
            continue
        codes = _codes(permissions)
        granted = codes | BY_SLUG.get(slug, set())
        if "finance.expense.view" in codes:
            granted.add("finance.budget.view_own")
        if granted != codes:
            bind.execute(
                sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
                {"perms": json.dumps(sorted(granted)), "id": position_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM positions")).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        if codes & NEW_CODES:
            bind.execute(
                sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
                {"perms": json.dumps(sorted(codes - NEW_CODES)), "id": position_id},
            )
    for table in (
        "fin_posting_rules",
        "fin_budgets",
        "fin_payments",
        "fin_ledger_lines",
        "fin_journal_lines",
        "fin_journal_entries",
        "fin_invoices",
        "fin_purchase_orders",
        "fin_parties",
        "fin_accounts",
        "fin_import_batches",
        "fin_settings",
    ):
        op.drop_table(table)
