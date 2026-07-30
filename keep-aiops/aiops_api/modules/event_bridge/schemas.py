"""CloudEvents envelope + IncidentEventData v1.

EXACTLY mirrors docs/aiops/contracts/event-envelope.mdx and the payloads in
docs/aiops/contracts/examples/. Compatible evolution: unknown fields are
ignored at every level (consumers MUST ignore unknown fields).
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class IncidentSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    INFO = "info"
    LOW = "low"


class IncidentStatus(str, Enum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    MERGED = "merged"
    DELETED = "deleted"


class EventType(str, Enum):
    INCIDENT_CREATED = "incident.created"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_RESOLVED = "incident.resolved"


class IncidentEventData(BaseModel):
    """IncidentEventData schema v1 (version carried in `dataschema`)."""

    model_config = ConfigDict(extra="ignore")

    incident_id: str
    # Optional: manual incidents may be unnamed at emission time (Keep allows
    # name=null until user/AI naming). Consumers MUST tolerate null.
    name: str | None = None
    severity: IncidentSeverity
    status: IncidentStatus
    alerts_count: int
    fingerprint: str | None = None
    assignee: str | None = None
    services: list[str] | None = None
    sources: list[str] | None = None
    is_predicted: bool | None = None


class KeepEventEnvelope(BaseModel):
    """CloudEvents 1.0 structured-mode envelope with Keep extensions."""

    model_config = ConfigDict(extra="ignore")

    specversion: Literal["1.0"]
    id: str  # uuid; idempotency key for consumers
    type: EventType
    source: str
    time: datetime
    tenantid: str  # Keep tenant id (extension attribute)
    subject: str  # domain entity id (incident uuid)
    dataschema: str | None = None
    data: IncidentEventData
