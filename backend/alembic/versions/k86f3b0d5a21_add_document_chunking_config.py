"""add configurable document chunking

Revision ID: k86f3b0d5a21
Revises: j75e2a9c4f10
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "k86f3b0d5a21"
down_revision: Union[str, None] = "j75e2a9c4f10"
branch_labels = None
depends_on = None


DEFAULT_CONFIG = {
    "mode": "standard",
    "chunk_size": 700,
    "chunk_overlap": 80,
    "parent_chunk_size": 1024,
}


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("knowledge_documents")
    }
    if "chunking_config" in columns:
        return
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "chunking_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(
                "'{\"mode\": \"standard\", \"chunk_size\": 700, "
                "\"chunk_overlap\": 80, \"parent_chunk_size\": 1024}'::jsonb"
            ),
        ),
    )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("knowledge_documents")
    }
    if "chunking_config" in columns:
        op.drop_column("knowledge_documents", "chunking_config")
