"""investigation context_pack column (M2 context builder)

Revision ID: 0003_investigation_context_pack
Revises: 0002_policy_tables
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_investigation_context_pack"
down_revision = "0002_policy_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investigation", sa.Column("context_pack", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("investigation", "context_pack")
