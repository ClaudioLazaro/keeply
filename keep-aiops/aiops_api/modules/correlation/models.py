"""Persistence for the correlation plugin.

Two things are stored: which tenants Keep has told us about (the plugin
contract is stateless — Keep re-sends the tenant periodically), and an
audit row per correlation decision.

The audit is not optional. Auto-merge is destructive: a wrong grouping
buries a real incident inside another one. Recording what was merged, at
what confidence, and which signals produced it, is what makes that
reversible instead of mysterious.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CorrelationClient(SQLModel, table=True):
    """A tenant Keep has asked us to correlate for.

    ``back_api_key`` is issued by Keep for us to call back with; it is the
    credential this plugin uses to read alerts and write incidents.
    """

    __tablename__ = "correlation_client"

    tenant_id: str = Field(primary_key=True)
    back_api_url: str
    back_api_key: str
    enabled: bool = True
    last_reminded_at: datetime = Field(default_factory=_utcnow)
    last_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class CorrelationDecision(SQLModel, table=True):
    """One correlation the algorithm made — or declined to make."""

    __tablename__ = "correlation_decision"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True)
    # applied | suggested | skipped
    outcome: str = Field(index=True)
    confidence: float
    # Why this grouping was made, in the operator's language.
    explanation: str
    alert_fingerprints: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # The incident the alerts landed in, when one was created or reused.
    incident_id: str | None = None
    # Settings in force when the decision was made, so a surprising
    # grouping can be traced to the configuration that allowed it.
    settings_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, index=True)
