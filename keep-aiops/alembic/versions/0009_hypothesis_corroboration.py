"""hypothesis.corroborated / .caveat — why a confidence is low

Confidence was already being discounted for hypotheses no live evidence
backs, but the reason was not persisted, so the API returned a low number
with no explanation and the UI could not label it.

Existing rows default to corroborated=True: they were produced before the
discount existed, so their confidence is untouched and relabelling them
as unverified would be wrong.

Revision ID: 0009_hypothesis_corroboration
Revises: 0008_agent_config
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_hypothesis_corroboration"
down_revision = "0008_agent_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hypothesis",
        sa.Column("corroborated", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("hypothesis", sa.Column("caveat", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("hypothesis", "caveat")
    op.drop_column("hypothesis", "corroborated")
