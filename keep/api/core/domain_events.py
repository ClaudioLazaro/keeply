"""
Thin, upstream-friendly domain event bridge (ADR-0004).

Keep persists domain events (CloudEvents 1.0 envelopes) into a transactional
outbox table written beside incident mutations, and a background dispatcher
delivers them to subscribed consumers via signed webhooks.

Contract: docs/aiops/contracts/event-envelope.mdx

The whole feature is gated behind ``KEEP_DOMAIN_EVENTS_ENABLED`` (default
false); when disabled there are zero outbox writes and zero behavior change.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import requests
from sqlmodel import Session, select

from keep.api.models.db.domain_event import DomainEventOutbox, DomainEventOutboxStatus
from keep.api.models.db.incident import Incident, IncidentSeverity

logger = logging.getLogger(__name__)

# M0 event types (docs/aiops/contracts/event-envelope.mdx)
INCIDENT_CREATED = "incident.created"
INCIDENT_UPDATED = "incident.updated"
INCIDENT_RESOLVED = "incident.resolved"

CLOUDEVENTS_SPECVERSION = "1.0"
CLOUDEVENTS_SOURCE = "keep-api"
CLOUDEVENTS_CONTENT_TYPE = "application/cloudevents+json"
SIGNATURE_HEADER = "X-Keep-Signature"

DOMAIN_EVENTS_POLL_INTERVAL = float(
    os.environ.get("KEEP_DOMAIN_EVENTS_POLL_INTERVAL", 5)
)
DOMAIN_EVENTS_BATCH_SIZE = int(os.environ.get("KEEP_DOMAIN_EVENTS_BATCH_SIZE", 50))
DOMAIN_EVENTS_MAX_ATTEMPTS = int(os.environ.get("KEEP_DOMAIN_EVENTS_MAX_ATTEMPTS", 5))
DOMAIN_EVENTS_REQUEST_TIMEOUT = float(
    os.environ.get("KEEP_DOMAIN_EVENTS_REQUEST_TIMEOUT", 10)
)
DOMAIN_EVENTS_BACKOFF_BASE = float(
    os.environ.get("KEEP_DOMAIN_EVENTS_BACKOFF_BASE", 5)
)
DOMAIN_EVENTS_BACKOFF_MAX = float(
    os.environ.get("KEEP_DOMAIN_EVENTS_BACKOFF_MAX", 300)
)


def is_domain_events_enabled() -> bool:
    """Whether the domain event bridge is enabled (read dynamically)."""
    return os.environ.get("KEEP_DOMAIN_EVENTS_ENABLED", "false").lower() == "true"


def build_incident_event_envelope(
    incident: Incident, event_type: str, tenant_id: str
) -> dict:
    """Build a CloudEvents 1.0 envelope with an IncidentEventData (v1) payload."""
    severity = (
        IncidentSeverity.from_number(incident.severity).value
        if isinstance(incident.severity, int)
        else incident.severity
    )
    data = {
        "incident_id": str(incident.id),
        "name": incident.user_generated_name or incident.ai_generated_name,
        "severity": severity,
        "status": incident.status,
        "alerts_count": incident.alerts_count or 0,
        "services": list(incident.affected_services or []),
        "sources": list(incident.sources or []),
        "is_predicted": bool(incident.is_predicted),
    }
    if incident.fingerprint:
        data["fingerprint"] = incident.fingerprint
    if incident.assignee:
        data["assignee"] = incident.assignee
    return {
        "specversion": CLOUDEVENTS_SPECVERSION,
        "id": str(uuid4()),
        "type": event_type,
        "source": CLOUDEVENTS_SOURCE,
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tenantid": tenant_id,
        "subject": str(incident.id),
        "dataschema": f"keep://events/{event_type}/1",
        "data": data,
    }


def sign_payload(body: bytes, secret: str) -> str:
    """HMAC-SHA256 hex signature over the raw body, per the webhook contract."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def emit_incident_event(
    session: Session,
    tenant_id: str,
    incident: Incident,
    event_type: str,
) -> Optional[DomainEventOutbox]:
    """Persist an incident domain event into the outbox (same db session).

    Never raises into the ingest path: failures are logged and rolled back.
    """
    if not is_domain_events_enabled():
        return None
    try:
        envelope = build_incident_event_envelope(incident, event_type, tenant_id)
        outbox_row = DomainEventOutbox(
            id=UUID(envelope["id"]),
            tenant_id=tenant_id,
            type=event_type,
            subject=envelope["subject"],
            payload=json.dumps(envelope),
            status=DomainEventOutboxStatus.PENDING.value,
            attempts=0,
        )
        session.add(outbox_row)
        session.commit()
        logger.info(
            "Domain event emitted",
            extra={
                "event_id": envelope["id"],
                "event_type": event_type,
                "incident_id": str(incident.id),
                "tenant_id": tenant_id,
            },
        )
        return outbox_row
    except Exception:
        logger.exception(
            "Failed to emit domain event",
            extra={"event_type": event_type, "tenant_id": tenant_id},
        )
        try:
            session.rollback()
        except Exception:
            logger.exception("Failed to rollback session after domain event error")
        return None


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff for the *next* attempt of a row with `attempts` tries."""
    return min(DOMAIN_EVENTS_BACKOFF_BASE * (2 ** max(attempts - 1, 0)), DOMAIN_EVENTS_BACKOFF_MAX)


def _deliver_event(event: DomainEventOutbox, webhook_url: str, secret: str) -> bool:
    """POST a single envelope; returns True on 2xx. Never raises."""
    body = event.payload.encode("utf-8")
    response = requests.post(
        webhook_url,
        data=body,
        headers={
            "Content-Type": CLOUDEVENTS_CONTENT_TYPE,
            SIGNATURE_HEADER: sign_payload(body, secret),
        },
        timeout=DOMAIN_EVENTS_REQUEST_TIMEOUT,
    )
    return 200 <= response.status_code < 300


def dispatch_pending_events(session: Session) -> dict:
    """Deliver due pending outbox rows; mark delivered/failed. Never raises."""
    webhook_url = os.environ.get("KEEP_DOMAIN_EVENTS_WEBHOOK_URL")
    secret = os.environ.get("KEEP_DOMAIN_EVENTS_WEBHOOK_SECRET", "")
    stats = {"delivered": 0, "failed": 0, "retried": 0, "skipped": 0}
    if not webhook_url:
        logger.warning(
            "KEEP_DOMAIN_EVENTS_WEBHOOK_URL is not set, skipping domain event dispatch"
        )
        return stats

    now = datetime.utcnow()
    events = session.exec(
        select(DomainEventOutbox)
        .where(DomainEventOutbox.status == DomainEventOutboxStatus.PENDING.value)
        .order_by(DomainEventOutbox.created_at)
        .limit(DOMAIN_EVENTS_BATCH_SIZE)
    ).all()

    for event in events:
        if event.last_attempt_at is not None:
            elapsed = (now - event.last_attempt_at).total_seconds()
            if elapsed < _backoff_seconds(event.attempts):
                stats["skipped"] += 1
                continue
        event.attempts += 1
        event.last_attempt_at = now
        try:
            delivered = _deliver_event(event, webhook_url, secret)
        except Exception:
            logger.exception(
                "Failed to deliver domain event",
                extra={"event_id": str(event.id), "event_type": event.type},
            )
            delivered = False
        if delivered:
            event.status = DomainEventOutboxStatus.DELIVERED.value
            stats["delivered"] += 1
        elif event.attempts >= DOMAIN_EVENTS_MAX_ATTEMPTS:
            event.status = DomainEventOutboxStatus.FAILED.value
            stats["failed"] += 1
        else:
            stats["retried"] += 1
        session.add(event)

    session.commit()
    return stats


def _dispatch_pending_events_sync() -> dict:
    # imported here to avoid a hard dependency on the engine at import time
    from keep.api.core.db import engine  # pylint: disable=import-outside-toplevel

    with Session(engine) as session:
        return dispatch_pending_events(session)


async def async_domain_events_dispatcher():
    """Background loop delivering pending outbox rows (gated by env var)."""
    logger.info(
        "Starting domain events dispatcher",
        extra={"poll_interval": DOMAIN_EVENTS_POLL_INTERVAL},
    )
    while True:
        try:
            loop = asyncio.get_running_loop()
            stats = await loop.run_in_executor(None, _dispatch_pending_events_sync)
            if stats["delivered"] or stats["failed"]:
                logger.info("Domain events dispatched", extra=stats)
        except Exception:
            logger.exception("Error in domain events dispatcher")
        await asyncio.sleep(DOMAIN_EVENTS_POLL_INTERVAL)
