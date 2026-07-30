"""RCA module: hypothesis persistence for investigation root-cause drafts."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Hypothesis(SQLModel, table=True):
    """One ranked root-cause hypothesis for an investigation.

    supporting_evidence / supporting_knowledge store the referenced ids
    (evidence row ids / knowledge document ids); the [E#]/[K#] markers used
    in the draft text resolve via investigation.rca_citations.
    """

    __tablename__ = "hypothesis"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    investigation_id: str = Field(foreign_key="investigation.id", index=True)
    title: str
    confidence: float
    supporting_evidence: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    supporting_knowledge: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
