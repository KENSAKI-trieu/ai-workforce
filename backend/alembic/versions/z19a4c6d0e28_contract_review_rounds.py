"""link a contract review to the round it follows

A revised contract uploaded again was reviewed as a stranger, and the medium and low
findings of each round contradicted the wording the round before had proposed. A review
now records the review whose revised file it is, so the next round can be checked against
what the previous one found and the decisions taken on it.

Revision ID: z19a4c6d0e28
Revises: y08f3b5c9d17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "z19a4c6d0e28"
down_revision = "y08f3b5c9d17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contract_reviews",
        sa.Column("parent_review_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_contract_reviews_parent_review",
        "contract_reviews",
        "contract_reviews",
        ["parent_review_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_contract_reviews_parent", "contract_reviews", ["parent_review_id"])


def downgrade() -> None:
    op.drop_index("idx_contract_reviews_parent", table_name="contract_reviews")
    op.drop_constraint("fk_contract_reviews_parent_review", "contract_reviews", type_="foreignkey")
    op.drop_column("contract_reviews", "parent_review_id")
