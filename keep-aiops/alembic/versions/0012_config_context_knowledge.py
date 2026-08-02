"""agent_config: context_timeline_limit + llm_embedding_model

The last two AI-plane knobs that were env-only. Everything an operator
tunes now lives behind the API and the settings UI; env vars remain the
bootstrap default for a fresh deployment, never the only way to change
something.

Revision ID: 0012_config_context_knowledge
Revises: 0011_correlation
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_config_ctx_knowledge"
down_revision = "0011_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_config", sa.Column("context_timeline_limit", sa.Integer(), nullable=True))
    op.add_column("agent_config", sa.Column("llm_embedding_model", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_config", "llm_embedding_model")
    op.drop_column("agent_config", "context_timeline_limit")
