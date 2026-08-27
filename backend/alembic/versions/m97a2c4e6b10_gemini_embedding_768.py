"""switch knowledge embeddings to Gemini 768 dimensions

Revision ID: m97a2c4e6b10
Revises: k86f3b0d5a21
"""

from typing import Union

from alembic import op


revision: str = "m97a2c4e6b10"
down_revision: Union[str, None] = "k86f3b0d5a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_doc_chunks_embedding_hnsw")
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN embedding "
        "TYPE vector(768) USING NULL::vector(768)"
    )
    op.execute(
        "UPDATE document_chunks SET embedding_status = 'pending', "
        "embedding_model = NULL, embedding_version = NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_doc_chunks_embedding_hnsw")
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN embedding "
        "TYPE vector(1024) USING NULL::vector(1024)"
    )
    op.execute(
        "UPDATE document_chunks SET embedding_status = 'pending', "
        "embedding_model = NULL, embedding_version = NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )
