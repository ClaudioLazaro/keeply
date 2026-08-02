"""Remove the AI plane's parallel credential store

Keep's provider system (`/providers`) is the system of record for
integration and LLM credentials: it already ships the catalog, install UI,
secret manager, scope validation and rotation. A second store in the AI
plane meant two rotation paths, two audit surfaces, and an operator
configuring the same backend twice.

Dropped here:
  * `agent_config.llm_api_key_encrypted` — LLM keys come from an installed
    Keep AI provider
  * `config_secret_key` — the Fernet key that existed only to protect the
    column above
  * `integration_config` — integration credentials and stub/live mode now
    derive from which Keep providers are installed

`agent_config` itself stays: budget caps, auto-investigate severities and
enabled specialists are genuinely AI-plane concepts Keep has no notion of.

Revision ID: 0010_drop_parallel_secrets
Revises: 0009_hypothesis_corroboration
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_drop_parallel_secrets"
down_revision = "0009_hypothesis_corroboration"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Conditional so this applies cleanly both to databases that ran the
    # withdrawn 0010/0011 revisions and to ones that never saw them.
    if _has_column("agent_config", "llm_api_key_encrypted"):
        op.drop_column("agent_config", "llm_api_key_encrypted")
    if _has_table("integration_config"):
        op.drop_table("integration_config")
    if _has_table("config_secret_key"):
        op.drop_table("config_secret_key")


def downgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("agent_config", sa.Column("llm_api_key_encrypted", sa.String(), nullable=True))
    op.create_table(
        "config_secret_key",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "integration_config",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="stub"),
        sa.Column("settings", json_type, nullable=True),
        sa.Column("secrets", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_config_name", "integration_config", ["name"], unique=True)
