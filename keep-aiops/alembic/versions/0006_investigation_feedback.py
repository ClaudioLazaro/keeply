"""investigation_feedback table (human feedback slice, M2)

Revision ID: 0006_investigation_feedback
Revises: 0005_hypotheses
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_investigation_feedback"
down_revision = "0005_hypotheses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("investigation_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investigation_feedback_investigation_id",
        "investigation_feedback",
        ["investigation_id"],
        unique=True,
    )
    op.create_index("ix_investigation_feedback_tenant_id", "investigation_feedback", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_investigation_feedback_tenant_id", table_name="investigation_feedback")
    op.drop_index("ix_investigation_feedback_investigation_id", table_name="investigation_feedback")
    op.drop_table("investigation_feedback")
