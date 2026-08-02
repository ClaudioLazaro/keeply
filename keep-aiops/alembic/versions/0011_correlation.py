"""correlation_client + rule_suggestion — the Keeply Alert Correlation plugin

`correlation_client` holds the tenants Keep has reminded us about, with the
back-API key it issued for us to call back with.

`rule_suggestion` is the queue of correlation rules the analysis proposes.
Nothing here creates incidents: Keep's own rules engine does that, on the
ingestion path, once an operator accepts a suggestion. Each row keeps the
evidence behind the proposal so it can be judged rather than trusted.

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
        "rule_suggestion",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("cel", sa.String(), nullable=False),
        sa.Column("grouping_criteria", json_type, nullable=True),
        sa.Column("timeframe_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_covered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rationale", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_rule_id", sa.String(), nullable=True),
        sa.Column("settings_snapshot", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_suggestion_tenant_id", "rule_suggestion", ["tenant_id"])
    op.create_index("ix_rule_suggestion_status", "rule_suggestion", ["status"])
    op.create_index("ix_rule_suggestion_created_at", "rule_suggestion", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_rule_suggestion_created_at", table_name="rule_suggestion")
    op.drop_index("ix_rule_suggestion_status", table_name="rule_suggestion")
    op.drop_index("ix_rule_suggestion_tenant_id", table_name="rule_suggestion")
    op.drop_table("rule_suggestion")
    op.drop_table("correlation_client")
