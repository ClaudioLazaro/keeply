import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import TEXT, Column, Field, SQLModel


class DomainEventOutboxStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class DomainEventOutbox(SQLModel, table=True):
    """Transactional outbox row holding a full CloudEvents envelope (JSON string).

    Written in the same session as the domain mutation (see
    keep.api.core.domain_events) and delivered asynchronously by the
    domain events dispatcher.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    # CloudEvents type, e.g. "incident.created"
    type: str
    # CloudEvents subject, e.g. the incident uuid
    subject: str
    # Full CloudEvents envelope serialized as JSON
    payload: str = Field(sa_column=Column(TEXT))
    status: str = Field(default=DomainEventOutboxStatus.PENDING.value, index=True)
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_attempt_at: datetime | None = Field(default=None)

    class Config:
        arbitrary_types_allowed = True
