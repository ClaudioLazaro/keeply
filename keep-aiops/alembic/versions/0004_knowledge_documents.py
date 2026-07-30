"""knowledge documents (RAG over runbooks + incident history, ADR-0005)

Embedding is a portable JSON list[float] (nullable); pgvector ANN indexes are
a documented M3+ optimization and do not change this row shape.

Revision ID: 0004_knowledge_documents
Revises: 0003_investigation_context_pack
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_knowledge_documents"
down_revision = "0003_investigation_context_pack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledgedocument",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("chunk", sa.String(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("doc_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledgedocument_tenant_id", "knowledgedocument", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledgedocument_tenant_id", table_name="knowledgedocument")
    op.drop_table("knowledgedocument")
