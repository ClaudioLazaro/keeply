"""Builds the context pack stored on ``investigation.context_pack`` (M2).

Pack shape (contract):

.. code-block:: json

    {
      "incident": {...},
      "alerts": [...],
      "topology": [...],
      "timeline": [...],
      "built_at": "<iso8601>",
      "errors": [{"section": "...", "error": "..."}]
    }

Partial-failure tolerant: each section is fetched independently; a failing
section degrades to its empty value (``{}`` for incident, ``[]`` for the
lists) and appends an entry to ``errors``. ``build_context_pack`` never
raises — the orchestrator persists whatever comes back.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from keep_client import KeepClient

from aiops_api.settings import Settings, get_settings
from aiops_api.telemetry import get_tracer

logger = logging.getLogger(__name__)

T = TypeVar("T")


def build_context_pack(
    tenant_id: str,
    incident_id: str,
    *,
    settings: Settings | None = None,
    client: KeepClient | None = None,
) -> dict[str, Any]:
    """Assemble the context pack for one incident. Never raises."""
    settings = settings or get_settings()
    errors: list[dict[str, str]] = []
    pack: dict[str, Any] = {"incident": {}, "alerts": [], "topology": [], "timeline": []}
    tracer = get_tracer()
    with tracer.start_as_current_span("context_pack.build") as span:
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("incident_id", incident_id)
        owns_client = client is None
        try:
            keep = client or KeepClient.from_settings(settings)
            try:
                pack["incident"] = _section(
                    "incident", errors, {}, lambda: keep.get_incident(incident_id).model_dump(mode="json")
                )
                pack["alerts"] = _section(
                    "alerts",
                    errors,
                    [],
                    lambda: [
                        alert.model_dump(mode="json") for alert in keep.get_incident_alerts(incident_id).items
                    ],
                )
                pack["topology"] = _section(
                    "topology",
                    errors,
                    [],
                    lambda: [entry.model_dump(mode="json") for entry in keep.get_incident_topology(incident_id)],
                )
                pack["timeline"] = _section(
                    "timeline",
                    errors,
                    [],
                    lambda: [
                        entry.model_dump(mode="json")
                        for entry in keep.get_incident_timeline(
                            incident_id, limit=settings.context_timeline_limit
                        )
                    ],
                )
            finally:
                if owns_client:
                    keep.close()
        except Exception as exc:  # noqa: BLE001 — contract: never raise
            logger.warning(
                "context pack build failed",
                extra={"tenant_id": tenant_id, "incident_id": incident_id, "error": str(exc)},
            )
            errors.append({"section": "client", "error": f"{type(exc).__name__}: {exc}"})
        span.set_attribute("context_pack.errors", len(errors))
    pack["built_at"] = datetime.now(timezone.utc).isoformat()
    pack["errors"] = errors
    logger.info(
        "context pack built",
        extra={
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "alerts": len(pack["alerts"]),
            "topology": len(pack["topology"]),
            "timeline": len(pack["timeline"]),
            "errors": len(errors),
        },
    )
    return pack


def _section(name: str, errors: list[dict[str, str]], empty: T, fetch: Callable[[], T]) -> T:
    """Fetch one section; on failure record the error and return ``empty``."""
    try:
        return fetch()
    except Exception as exc:  # noqa: BLE001 — partial failure is expected
        logger.warning("context pack section failed", extra={"section": name, "error": str(exc)})
        errors.append({"section": name, "error": f"{type(exc).__name__}: {exc}"})
        return empty
