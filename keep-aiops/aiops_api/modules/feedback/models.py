"""SQLModel persistence for human feedback on investigations."""

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationFeedback(SQLModel, table=True):
    """One human feedback entry per investigation (upsert on investigation_id).

    `tenant_id` is denormalized from the investigation so feedback metrics
    and queries do not need a join. `rating` is a bounded enum
    ("useful" | "not_useful") — the only label allowed on feedback metrics.
    """

    __tablename__ = "investigation_feedback"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    investigation_id: str = Field(foreign_key="investigation.id", unique=True, index=True)
    tenant_id: str = Field(index=True)
    rating: str  # useful | not_useful
    comment: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
