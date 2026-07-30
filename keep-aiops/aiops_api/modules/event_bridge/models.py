"""Persisted dedupe records for consumed Keep events (idempotency on event id)."""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class ProcessedEvent(SQLModel, table=True):
    __tablename__ = "processed_event"

    id: str = Field(primary_key=True)  # CloudEvents event id
    event_type: str
    tenant_id: str
    subject: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
