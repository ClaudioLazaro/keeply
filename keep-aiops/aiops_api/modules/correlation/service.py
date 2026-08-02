"""Correlation run: pull alerts from Keep, group them, write incidents back.

Implements the client half of Keep's external-AI contract. Keep reminds us
about a tenant; we hold the issued back-API key and do the work on our own
schedule, reading alerts and writing incidents through Keep's public API —
the same API a human uses, so nothing here depends on Keep internals.

Ungrouped alerts are left completely alone. This algorithm only ever
*joins* alerts that Keep already delivered; it never deletes an incident,
and an alert that correlates with nothing keeps whatever Keep did with it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from aiops_api.db import get_engine, session_scope
from aiops_api.modules.correlation.grouping import CorrelationGroup, group_alerts
from aiops_api.modules.correlation.models import (
    CorrelationClient,
    CorrelationDecision,
    _utcnow,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20.0

# Defaults mirror config_default in keep/api/models/db/ai_external.py. They
# only apply until Keep hands us the tenant's actual settings.
DEFAULT_SETTINGS: dict[str, Any] = {
    # Off until an operator turns it on: correlation joins alerts into
    # shared incidents automatically, which should never start happening
    # as a side effect of deploying a new version.
    "Enabled": False,
    "Correlation Window (minutes)": 10.0,
    "Similarity Threshold": 0.6,
    "Auto-merge Confidence": 0.8,
    "Max Alerts Per Incident": 20.0,
}

ALGORITHM_ID = "Keeply Alert Correlation_1"


def register_client(tenant_id: str, back_api_url: str, back_api_key: str) -> None:
    """Record (or refresh) a tenant Keep asked us to correlate for."""
    with session_scope() as session:
        client = session.get(CorrelationClient, tenant_id)
        if client is None:
            client = CorrelationClient(
                tenant_id=tenant_id,
                back_api_url=back_api_url,
                back_api_key=back_api_key,
            )
            logger.info("correlation client registered", extra={"tenant_id": tenant_id})
        else:
            # Keep rotates the key; always take the latest.
            client.back_api_url = back_api_url
            client.back_api_key = back_api_key
            client.last_reminded_at = _utcnow()
        session.add(client)


def active_clients() -> list[CorrelationClient]:
    with Session(get_engine()) as session:
        clients = session.exec(
            select(CorrelationClient).where(CorrelationClient.enabled.is_(True))
        ).all()
        for client in clients:
            session.expunge(client)
        return list(clients)


def _client_http(client: CorrelationClient) -> httpx.Client:
    return httpx.Client(
        base_url=client.back_api_url.rstrip("/"),
        headers={"X-API-KEY": client.back_api_key, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )


def fetch_settings(client: CorrelationClient) -> dict[str, Any]:
    """The tenant's settings as configured on the AI page.

    Falls back to defaults when Keep is unreachable or the algorithm has no
    stored config — running with defaults beats not running at all, and the
    defaults are the conservative end of every range.
    """
    settings = dict(DEFAULT_SETTINGS)
    try:
        with _client_http(client) as http:
            response = http.get("/ai/stats")
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001
        logger.warning("could not read correlation settings, using defaults", exc_info=True)
        return settings

    for config in payload.get("algorithm_configs") or []:
        if config.get("algorithm_id") != ALGORITHM_ID:
            continue
        for item in config.get("settings") or []:
            name, value = item.get("name"), item.get("value")
            if name not in settings:
                continue
            if isinstance(value, bool):
                settings[name] = value
            elif isinstance(value, (int, float)):
                settings[name] = float(value)
    return settings


def fetch_recent_alerts(client: CorrelationClient, window_minutes: float) -> list[dict[str, Any]]:
    """Alerts that arrived inside the correlation window.

    The window is widened slightly so an alert near the boundary can still
    join a group formed just before it.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes * 2)
    try:
        with _client_http(client) as http:
            response = http.get("/alerts", params={"limit": 500})
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001
        logger.warning("could not read alerts for correlation", exc_info=True)
        return []

    alerts = payload if isinstance(payload, list) else payload.get("items") or []
    fresh: list[dict[str, Any]] = []
    for alert in alerts:
        raw = alert.get("lastReceived") or alert.get("last_received")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            fresh.append(alert)
    return fresh


def _already_correlated(session: Session, tenant_id: str, fingerprints: list[str]) -> bool:
    """Whether this exact set was already acted on.

    Without this the loop would re-apply the same grouping on every run,
    spamming the incident with duplicate comments.
    """
    key = sorted(fingerprints)
    recent = session.exec(
        select(CorrelationDecision)
        .where(CorrelationDecision.tenant_id == tenant_id)
        .order_by(CorrelationDecision.created_at.desc())
        .limit(200)
    ).all()
    return any(sorted(decision.alert_fingerprints or []) == key for decision in recent)


def _incident_of(alert: dict[str, Any]) -> str | None:
    value = alert.get("incident") or alert.get("incident_id")
    return str(value) if value else None


def apply_group(
    client: CorrelationClient,
    group: CorrelationGroup,
    settings: dict[str, float],
) -> str:
    """Act on one correlation. Returns the outcome recorded.

    Below the auto-merge confidence the grouping is written as a comment
    rather than executed — the operator decides. At or above it the alerts
    are joined into a single incident.
    """
    fingerprints = group.fingerprints()
    if len(fingerprints) < 2:
        return "skipped"

    threshold = settings["Auto-merge Confidence"]
    snapshot = dict(settings)

    with session_scope() as session:
        if _already_correlated(session, client.tenant_id, fingerprints):
            return "skipped"

    # Reuse an incident the alerts already belong to; otherwise create one.
    existing = next((_incident_of(alert) for alert in group.alerts if _incident_of(alert)), None)
    outcome = "applied" if group.confidence >= threshold else "suggested"
    incident_id: str | None = existing

    try:
        with _client_http(client) as http:
            if outcome == "applied":
                if incident_id is None:
                    created = http.post(
                        "/incidents",
                        json={
                            "incident_name": _incident_name(group),
                            "severity": _incident_severity(group),
                            "user_summary": group.explain(),
                        },
                    )
                    created.raise_for_status()
                    incident_id = str(created.json()["id"])

                joined = http.post(
                    f"/incidents/{incident_id}/alerts",
                    params={"is_created_by_ai": True},
                    json=fingerprints,
                )
                joined.raise_for_status()

            if incident_id:
                # The audit trail an operator reads when a grouping looks
                # wrong: what was joined, how sure, and on what evidence.
                verb = "Correlated" if outcome == "applied" else "Suggested correlation"
                http.post(
                    f"/incidents/{incident_id}/comment",
                    json={
                        "status": "firing",
                        "comment": (
                            f"**{verb} by Keeply Alert Correlation**\n\n"
                            f"{group.explain()}\n\n"
                            f"Alerts: {', '.join(fingerprints)}\n"
                            f"Auto-merge threshold: {threshold:.0%}"
                        ),
                    },
                )
    except Exception as exc:  # noqa: BLE001 — a failed write must not kill the run
        logger.warning(
            "correlation write-back failed",
            extra={"tenant_id": client.tenant_id, "error": f"{type(exc).__name__}: {exc}"},
        )
        outcome = "skipped"

    with session_scope() as session:
        session.add(
            CorrelationDecision(
                tenant_id=client.tenant_id,
                outcome=outcome,
                confidence=group.confidence,
                explanation=group.explain(),
                alert_fingerprints=fingerprints,
                incident_id=incident_id,
                settings_snapshot=snapshot,
            )
        )
    return outcome


def _incident_name(group: CorrelationGroup) -> str:
    services = sorted(
        {
            str(service)
            for alert in group.alerts
            for service in _as_list(alert.get("service") or alert.get("services"))
        }
    )
    subject = ", ".join(services[:3]) if services else "multiple services"
    return f"{subject}: {group.size} correlated alerts"


def _incident_severity(group: CorrelationGroup) -> str:
    order = ["critical", "high", "warning", "info", "low"]
    severities = [str(alert.get("severity") or "").lower() for alert in group.alerts]
    for level in order:
        if level in severities:
            return level
    return "info"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def run_for_client(client: CorrelationClient) -> dict[str, int]:
    """One correlation pass for one tenant. Never raises."""
    settings = fetch_settings(client)

    # The toggle is authoritative. Keep reminds us about the tenant
    # regardless of it, so this is the only place that decides whether
    # anything actually happens.
    if not settings.get("Enabled"):
        logger.info(
            "correlation disabled for tenant, skipping run",
            extra={"tenant_id": client.tenant_id},
        )
        return {"applied": 0, "suggested": 0, "skipped": 0}

    alerts = fetch_recent_alerts(client, settings["Correlation Window (minutes)"])

    groups = group_alerts(
        alerts,
        window_minutes=settings["Correlation Window (minutes)"],
        similarity_threshold=settings["Similarity Threshold"],
        max_group_size=int(settings["Max Alerts Per Incident"]),
    )

    tally = {"applied": 0, "suggested": 0, "skipped": 0}
    for group in groups:
        tally[apply_group(client, group, settings)] += 1

    with session_scope() as session:
        stored = session.get(CorrelationClient, client.tenant_id)
        if stored is not None:
            stored.last_run_at = _utcnow()
            session.add(stored)

    logger.info(
        "correlation run complete",
        extra={"tenant_id": client.tenant_id, "alerts": len(alerts), **tally},
    )
    return tally
