"""Synchronous httpx client for the Keep REST API.

Auth: ``X-API-KEY`` header. M0 usage: read incident/alerts and suggest-only
writeback (comment + enrichment). Typed errors on any non-2xx response.
"""

from typing import Any

import httpx

from keep_client.dtos import AlertsPage, IncidentDto, TimelineEntryDto, TopologyServiceDto
from keep_client.errors import KeepApiError, KeepClientError, KeepNotFoundError

DEFAULT_TIMEOUT = 10.0


class KeepClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-KEY": api_key},
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls, settings: Any = None) -> "KeepClient":
        """Build from aiops_api settings (base_url + api key)."""
        if settings is None:
            from aiops_api.settings import get_settings

            settings = get_settings()
        return cls(base_url=settings.keep_api_url, api_key=settings.keep_api_key)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KeepClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise KeepClientError(f"Keep API {method} {path} transport error: {exc}") from exc
        if response.status_code == 404:
            raise KeepNotFoundError(404, response.text, method, path)
        if not 200 <= response.status_code < 300:
            raise KeepApiError(response.status_code, response.text, method, path)
        return response

    # -- endpoints (keep/api/routes/incidents.py) ---------------------------

    def get_incident(self, incident_id: str) -> IncidentDto:
        """GET /incidents/{incident_id}."""
        response = self._request("GET", f"/incidents/{incident_id}")
        return IncidentDto.model_validate(response.json())

    def get_incident_alerts(self, incident_id: str) -> AlertsPage:
        """GET /incidents/{incident_id}/alerts."""
        response = self._request("GET", f"/incidents/{incident_id}/alerts")
        return AlertsPage.model_validate(response.json())

    def add_comment(self, incident_id: str, comment: str, status: str | None = None) -> None:
        """POST /incidents/{incident_id}/comment.

        Body is IncidentStatusChangeDto: ``status`` is schema-required (validated
        against the IncidentStatus enum) but ignored by the comment handler, so
        when not given we read the incident's current status to stay truthful.
        """
        if status is None:
            status = self.get_incident(incident_id).status
        self._request(
            "POST",
            f"/incidents/{incident_id}/comment",
            json={"status": status, "comment": comment, "tagged_users": []},
        )

    def enrich_incident(self, incident_id: str, enrichments: dict[str, Any]) -> None:
        """POST /incidents/{incident_id}/enrich (EnrichIncidentRequestBody)."""
        self._request(
            "POST",
            f"/incidents/{incident_id}/enrich",
            json={"enrichments": enrichments, "force": False},
        )

    # -- topology / timeline -------------------------------------------------

    def get_incident_topology(self, incident_id: str) -> list[TopologyServiceDto]:
        """Topology services relevant to an incident.

        Keep has no per-incident topology route (keep/api/routes/topology.py),
        so this uses GET /topology?services=<csv> — which returns each matched
        service plus its direct dependency neighbours — and applies the same
        filter client-side as a backstop in case the server ignores the param.
        Tolerates 404/empty (no topology configured, incident without
        services) by returning [].
        """
        try:
            incident = self.get_incident(incident_id)
        except KeepNotFoundError:
            return []
        services = [service for service in incident.services if service]
        if not services:
            return []
        try:
            response = self._request("GET", "/topology", params={"services": ",".join(services)})
        except KeepNotFoundError:
            return []
        payload = response.json() or []
        entries = [TopologyServiceDto.model_validate(item) for item in payload]
        return _filter_to_neighbourhood(entries, set(services))

    def get_incident_timeline(self, incident_id: str, limit: int = 50) -> list[TimelineEntryDto]:
        """Alert-audit timeline for an incident.

        POST /alerts/audit with [incident_id]; Keep answers 404 when there are
        no audit rows, which maps to []. Entries are newest-first; `limit` is
        applied client-side (the route does not expose a limit parameter).
        """
        try:
            response = self._request("POST", "/alerts/audit", json=[incident_id])
        except KeepNotFoundError:
            return []
        rows = response.json() or []
        return [
            TimelineEntryDto(
                timestamp=row.get("timestamp"),
                action=row.get("action"),
                description=row.get("description"),
                actor=row.get("user_id"),
            )
            for row in rows[:limit]
            if isinstance(row, dict)
        ]


def _filter_to_neighbourhood(
    entries: list[TopologyServiceDto], services: set[str]
) -> list[TopologyServiceDto]:
    """Keep incident services plus their direct dependency targets."""
    matched = [entry for entry in entries if entry.service in services]
    neighbours = {
        dependency.serviceId
        for entry in matched
        for dependency in entry.dependencies
        if dependency.serviceId
    }
    return [entry for entry in entries if entry.service in services or entry.id in neighbours or entry.service in neighbours]
