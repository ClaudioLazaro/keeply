"""correlation_client + correlation_decision — the Keeply Alert Correlation plugin

`correlation_client` holds the tenants Keep has reminded us about, with the
back-API key it issued for us to call back with.

`correlation_decision` is the audit trail. Auto-merge is destructive — a
wrong grouping buries a real incident inside another one — so every
decision records what was joined, at what confidence, on which signals,
and under which settings.

Revision ID: 0011_correlation
Revises: 0010_drop_parallel_secrets
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_correlation"
down_revision = "0010_drop_parallel_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "correlation_client",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("back_api_url", sa.String(), nullable=False),
        sa.Column("back_api_key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_reminded_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "correlation_decision",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column("alert_fingerprints", json_type, nullable=True),
        sa.Column("incident_id", sa.String(), nullable=True),
        sa.Column("settings_snapshot", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_correlation_decision_tenant_id", "correlation_decision", ["tenant_id"])
    op.create_index("ix_correlation_decision_outcome", "correlation_decision", ["outcome"])
    op.create_index("ix_correlation_decision_created_at", "correlation_decision", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_correlation_decision_created_at", table_name="correlation_decision")
    op.drop_index("ix_correlation_decision_outcome", table_name="correlation_decision")
    op.drop_index("ix_correlation_decision_tenant_id", table_name="correlation_decision")
    op.drop_table("correlation_decision")
    op.drop_table("correlation_client")
