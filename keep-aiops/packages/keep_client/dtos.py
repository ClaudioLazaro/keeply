"""Pydantic v2 DTOs for the Keep REST API fields used by the AIOps plane.

Keep payloads carry far more fields than the AIOps plane needs; unknown fields
are ignored (compatible-evolution rule: consumers MUST ignore unknown fields).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class IncidentDto(BaseModel):
    """Subset of keep.api.models.incident.IncidentDto."""

    model_config = ConfigDict(extra="ignore")

    id: str
    status: str = "firing"
    severity: str | None = None
    user_generated_name: str | None = None
    user_summary: str | None = None
    alerts_count: int = 0
    assignee: str | None = None
    services: list[str] = []
    alert_sources: list[str] = []
    is_predicted: bool = False


class AlertDto(BaseModel):
    """Subset of an alert attached to an incident."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None
    severity: str | None = None
    status: str | None = None
    source: list[str] = []
    fingerprint: str | None = None


class AlertsPage(BaseModel):
    """AlertWithIncidentLinkMetadataPaginatedResultsDto from GET /incidents/{id}/alerts."""

    model_config = ConfigDict(extra="ignore")

    items: list[AlertDto] = []
    count: int = 0
    limit: int = 0
    offset: int = 0


class TopologyDependencyDto(BaseModel):
    """Subset of TopologyServiceDependencyDto nested in TopologyServiceDtoOut."""

    model_config = ConfigDict(extra="ignore")

    serviceId: str | None = None
    protocol: str | None = None


class TopologyServiceDto(BaseModel):
    """Subset of keep.api.models.db.topology.TopologyServiceDtoOut."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    service: str
    display_name: str | None = None
    environment: str | None = None
    description: str | None = None
    dependencies: list[TopologyDependencyDto] = []


class TimelineEntryDto(BaseModel):
    """One alert-audit row, normalized for the context pack timeline.

    Source: AlertAuditDto from POST /alerts/audit (actor = user_id).
    """

    model_config = ConfigDict(extra="ignore")

    timestamp: datetime | None = None
    action: str | None = None
    description: str | None = None
    actor: str | None = None


class CommentResponse(BaseModel):
    """Audit comment row returned by POST /incidents/{id}/comment."""

    model_config = ConfigDict(extra="ignore")

    id: Any = None
