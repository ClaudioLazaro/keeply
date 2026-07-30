"""SQLModel persistence for the Knowledge Engine (ADR-0005).

A KnowledgeDocument is one indexed chunk of a source (runbook section,
resolved-incident RCA comment, …) plus its optional embedding. The embedding
is stored as a portable JSON ``list[float]``; swapping in pgvector for ANN
search is a documented M3+ optimization that does not change this row shape.

Cross-tenant retrieval is forbidden: every query filters ``tenant_id`` (the
``"*"`` global tenant carries shared runbooks, mirroring the policy module's
GLOBAL_TENANT convention).
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledgedocument"

    # Deterministic id (hash of tenant|source|title) so seeding is upsertable.
    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)  # '*' = global shared runbooks
    source: str = ""  # runbook filename, 'incident:<id>', connector name, …
    title: str = ""
    chunk: str = ""
    # JSON list[float]; null in keyword mode (no embedder configured).
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    # Free-form provenance/ACL metadata (source uri, section, incident id, …).
    doc_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)
