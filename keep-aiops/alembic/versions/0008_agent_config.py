"""agent_config table — runtime-tunable agent settings

All columns are nullable: NULL means "inherit the env default", so an
existing deployment keeps its exact behaviour until an operator changes
something through the API.

No credential is stored — only `llm_api_key_env`, the NAME of the
environment variable the runtime resolves the key from.

Revision ID: 0008_agent_config
Revises: 0007_evidence_backend
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_agent_config"
down_revision = "0007_evidence_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "agent_config",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("llm_provider", sa.String(), nullable=True),
        sa.Column("llm_model", sa.String(), nullable=True),
        sa.Column("llm_api_key_env", sa.String(), nullable=True),
        sa.Column("budget_max_tool_calls", sa.Integer(), nullable=True),
        sa.Column("budget_max_wall_time_seconds", sa.Float(), nullable=True),
        sa.Column("budget_max_llm_tokens", sa.Integer(), nullable=True),
        sa.Column("auto_investigate_severities", json_type, nullable=True),
        sa.Column("disabled_specialists", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_config_tenant_id", "agent_config", ["tenant_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_config_tenant_id", table_name="agent_config")
    op.drop_table("agent_config")
