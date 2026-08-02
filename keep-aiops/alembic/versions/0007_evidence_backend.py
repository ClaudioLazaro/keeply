"""evidence.backend — provenance of each evidence item (live/stub/gap)

Existing rows are backfilled from the payload when the tool reported a
backend, and left as "unknown" otherwise. They are deliberately NOT
assumed to be live: mislabelling stub data as real is the failure mode
this column exists to prevent.

Revision ID: 0007_evidence_backend
Revises: 0006_investigation_feedback
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_evidence_backend"
down_revision = "0006_investigation_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column("backend", sa.String(), nullable=False, server_default="unknown"),
    )
    op.create_index("ix_evidence_backend", "evidence", ["backend"])

    # Backfill: a gap is recognisable by its summary marker; everything
    # else takes whatever the stored payload reported, if anything.
    op.execute(
        """
        UPDATE evidence
           SET backend = 'gap'
         WHERE summary LIKE '%evidence gap%'
        """
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE evidence
               SET backend = payload->'result'->>'backend'
             WHERE backend = 'unknown'
               AND payload->'result'->>'backend' IS NOT NULL
            """
        )


def downgrade() -> None:
    op.drop_index("ix_evidence_backend", table_name="evidence")
    op.drop_column("evidence", "backend")
