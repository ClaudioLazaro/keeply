"""SQLModel persistence for investigations and gathered evidence."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Investigation(SQLModel, table=True):
    """One investigation per (tenant_id, incident_id) — idempotent creation.

    Status FSM: queued -> gathering -> hypothesizing -> rca_ready
    (terminal: failed / cancelled).
    `incident.resolved` does NOT move the FSM: the RCA stays available
    (rca_ready) and `incident_resolved` is set instead — the draft remains
    useful post-resolution and the terminal-state list stays as specified.
    """

    __tablename__ = "investigation"
    __table_args__ = (UniqueConstraint("tenant_id", "incident_id", name="uq_investigation_tenant_incident"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    status: str = "queued"  # queued | gathering | hypothesizing | rca_ready | failed | cancelled
    mode: str = "suggest"  # M0 is suggest-only; no actions are ever taken
    created_at: datetime = Field(default_factory=_utcnow)
    # onupdate is a client-side default (no DDL/server_default); service.py
    # already sets updated_at explicitly, so this only backstops other writers.
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
    rca_draft: str | None = None
    # M2 context pack (migration 0003): incident/alerts/topology/timeline snapshot
    # assembled during the gathering phase; None until built (or on older rows).
    context_pack: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # M2 RCA citations (migration 0005): resolves [E#]/[K#] markers in rca_draft
    # {"evidence": {"E1": evidence_id, ...}, "knowledge": {"K1": doc_id, ...}}.
    rca_citations: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    incident_resolved: bool = False


class Evidence(SQLModel, table=True):
    """One tool result (or gap) gathered during an investigation.

    ``backend`` records the provenance of the payload: "live" means the
    tool talked to a real system, "stub" means it returned a canned demo
    payload, "gap" means the call failed. This is a first-class column
    rather than a payload detail because an RCA built on stub data must
    never be presentable as one built on production telemetry.
    """

    __tablename__ = "evidence"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    investigation_id: str = Field(foreign_key="investigation.id", index=True)
    tool: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # live | stub | gap | unknown ("unknown" = the tool reported no mode)
    backend: str = Field(default="unknown", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
