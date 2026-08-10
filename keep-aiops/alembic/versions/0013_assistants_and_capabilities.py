"""agent_config.assistants + llm_capability

Two additions that are deliberately kept apart.

``agent_config.assistants`` is what the operator chose: which provider and
model each AI feature routes to, so drafting a workflow and writing an RCA
need not share a model, a cost, or a latency.

``llm_capability`` is what the system found out by trying — the
compatibility downgrades a given model turned out to require, with the
provider's own error kept as the cause. Merging the two would let a
discovered workaround appear in the UI as something the operator asked for.

Both are additive and nullable: a deployment that never opens the new
settings page behaves exactly as it did before.

Revision ID: 0013_assistants_capabilities
Revises: 0012_config_ctx_knowledge
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_assistants_capabilities"
down_revision = "0012_config_ctx_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_config", sa.Column("assistants", sa.JSON(), nullable=True))

    op.create_table(
        "llm_capability",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        # Which downgrades this model needs. An empty list means the strong
        # form worked, which is a distinct fact from having never been tried
        # — hence a row rather than an absence.
        sa.Column("downgrades", sa.JSON(), nullable=True),
        # The provider's verbatim refusal. A downgrade with no cause on
        # record is indistinguishable from a bug.
        sa.Column("evidence", sa.String(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # Lookup is always by the full triple on the hot path (every chat
    # request resolves one), so the index matches that shape.
    op.create_index(
        "ix_llm_capability_lookup",
        "llm_capability",
        ["tenant_id", "provider", "model"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_capability_lookup", table_name="llm_capability")
    op.drop_table("llm_capability")
    op.drop_column("agent_config", "assistants")
