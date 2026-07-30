"""policy tables (persisted tenant policies, ADR-0003)

Revision ID: 0002_policy_tables
Revises: 0001_initial_schema
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_policy_tables"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_tenant_id", "policy", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_policy_tenant_id", table_name="policy")
    op.drop_table("policy")
