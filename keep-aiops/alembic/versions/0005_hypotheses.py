"""hypotheses table + investigation.rca_citations (RCA slice, ADR-0007)

Revision ID: 0005_hypotheses
Revises: 0004_knowledge_documents
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_hypotheses"
down_revision = "0004_knowledge_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypothesis",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("investigation_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False),
        sa.Column("supporting_knowledge", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hypothesis_investigation_id", "hypothesis", ["investigation_id"])
    op.add_column("investigation", sa.Column("rca_citations", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("investigation", "rca_citations")
    op.drop_index("ix_hypothesis_investigation_id", table_name="hypothesis")
    op.drop_table("hypothesis")
