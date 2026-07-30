"""feat: add domaineventoutbox table for the domain event bridge

Revision ID: 8f3a1c2d4e5b
Revises: 67ff7efffed4
Create Date: 2026-07-29 12:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision = "8f3a1c2d4e5b"
down_revision = "67ff7efffed4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "domaineventoutbox",
        sa.Column("id", sqlmodel.sql.sqltypes.types.Uuid(), nullable=False),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("subject", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domaineventoutbox_tenant_id",
        "domaineventoutbox",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_domaineventoutbox_status",
        "domaineventoutbox",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_domaineventoutbox_status", table_name="domaineventoutbox")
    op.drop_index("ix_domaineventoutbox_tenant_id", table_name="domaineventoutbox")
    op.drop_table("domaineventoutbox")
