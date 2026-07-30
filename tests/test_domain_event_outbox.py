"""Unit tests for the domain event outbox (ADR-0004 / event-envelope contract).

These tests are intentionally self-contained: they use an in-memory SQLite
database and mock the HTTP layer, so they can run with only
``sqlmodel``, ``requests`` and ``pytest`` installed:

    python -m pytest tests/test_domain_event_outbox.py --noconftest
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from keep.api.core import domain_events
from keep.api.core.domain_events import (
    INCIDENT_CREATED,
    INCIDENT_RESOLVED,
    INCIDENT_UPDATED,
    SIGNATURE_HEADER,
    build_incident_event_envelope,
    dispatch_pending_events,
    emit_incident_event,
    sign_payload,
)
from keep.api.models.db.domain_event import DomainEventOutbox, DomainEventOutboxStatus
from keep.api.models.db.incident import Incident, IncidentSeverity, IncidentStatus

TENANT_ID = "11111111-2222-3333-4444-555555555555"

CONTRACT_EXAMPLE = (
    Path(__file__).parent.parent
    / "docs"
    / "aiops"
    / "contracts"
    / "examples"
    / "incident.created.json"
)


def _make_incident(**overrides) -> Incident:
    incident = Incident(
        tenant_id=TENANT_ID,
        user_generated_name="Payment API elevated 5xx rate",
        ai_generated_name=None,
        user_summary="summary",
        generated_summary="generated summary",
        severity=IncidentSeverity.CRITICAL.order,
        status=IncidentStatus.FIRING.value,
        alerts_count=3,
        affected_services=["payment-api"],
        sources=["prometheus"],
        fingerprint="fp-9f8e7d6c5b",
        is_predicted=False,
    )
    for key, value in overrides.items():
        setattr(incident, key, value)
    return incident


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[DomainEventOutbox.__table__])
    with Session(engine) as session:
        yield session


def _pending_rows(session):
    return session.exec(
        select(DomainEventOutbox).where(
            DomainEventOutbox.status == DomainEventOutboxStatus.PENDING.value
        )
    ).all()


# ---------------------------------------------------------------------------
# Envelope contract
# ---------------------------------------------------------------------------


def test_envelope_matches_contract_example_shape():
    incident = _make_incident()
    envelope = build_incident_event_envelope(incident, INCIDENT_CREATED, TENANT_ID)

    example = json.loads(CONTRACT_EXAMPLE.read_text())

    # same top-level CloudEvents attributes as the contract example
    assert set(envelope.keys()) == set(example.keys())
    assert envelope["specversion"] == "1.0"
    assert envelope["source"] == "keep-api"
    assert envelope["type"] == INCIDENT_CREATED
    assert envelope["dataschema"] == f"keep://events/{INCIDENT_CREATED}/1"
    assert envelope["time"].endswith("Z")
    assert envelope["tenantid"] == TENANT_ID
    assert envelope["subject"] == str(incident.id)

    # IncidentEventData v1 required fields
    data = envelope["data"]
    assert data["incident_id"] == str(incident.id) == envelope["subject"]
    assert data["name"] == "Payment API elevated 5xx rate"
    assert data["severity"] == "critical"
    assert data["status"] == "firing"
    assert data["alerts_count"] == 3
    # optional fields present in the example keep their names/types
    assert data["fingerprint"] == "fp-9f8e7d6c5b"
    assert data["services"] == ["payment-api"]
    assert data["sources"] == ["prometheus"]
    assert data["is_predicted"] is False


def test_envelope_omits_unset_optional_fields():
    incident = _make_incident(fingerprint=None, assignee=None)
    envelope = build_incident_event_envelope(incident, INCIDENT_UPDATED, TENANT_ID)
    assert "fingerprint" not in envelope["data"]
    assert "assignee" not in envelope["data"]
    assert envelope["dataschema"] == f"keep://events/{INCIDENT_UPDATED}/1"


# ---------------------------------------------------------------------------
# HMAC signature
# ---------------------------------------------------------------------------


def test_sign_payload_matches_known_hmac():
    body = b'{"specversion":"1.0","type":"incident.created"}'
    secret = "topsecret"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_payload(body, secret) == expected


# ---------------------------------------------------------------------------
# Outbox writes (gated by KEEP_DOMAIN_EVENTS_ENABLED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type", [INCIDENT_CREATED, INCIDENT_UPDATED, INCIDENT_RESOLVED]
)
def test_emit_writes_outbox_row_when_enabled(session, monkeypatch, event_type):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_ENABLED", "true")
    incident = _make_incident()

    row = emit_incident_event(session, TENANT_ID, incident, event_type)

    assert row is not None
    rows = session.exec(select(DomainEventOutbox)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id == TENANT_ID
    assert row.type == event_type
    assert row.subject == str(incident.id)
    assert row.status == DomainEventOutboxStatus.PENDING.value
    assert row.attempts == 0
    # payload is the full CloudEvents envelope and the row id is the event id
    envelope = json.loads(row.payload)
    assert envelope["id"] == str(row.id)
    assert envelope["type"] == event_type
    assert envelope["data"]["incident_id"] == str(incident.id)


def test_emit_writes_nothing_when_disabled(session, monkeypatch):
    monkeypatch.delenv("KEEP_DOMAIN_EVENTS_ENABLED", raising=False)
    incident = _make_incident()

    assert emit_incident_event(session, TENANT_ID, incident, INCIDENT_CREATED) is None
    assert session.exec(select(DomainEventOutbox)).all() == []


def test_emit_writes_nothing_when_explicitly_disabled(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_ENABLED", "false")
    incident = _make_incident()

    assert emit_incident_event(session, TENANT_ID, incident, INCIDENT_RESOLVED) is None
    assert session.exec(select(DomainEventOutbox)).all() == []


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _insert_pending_row(session, **overrides):
    incident_id = uuid4()
    envelope = {
        "specversion": "1.0",
        "id": str(uuid4()),
        "type": INCIDENT_CREATED,
        "source": "keep-api",
        "time": "2026-07-29T12:00:00Z",
        "tenantid": TENANT_ID,
        "subject": str(incident_id),
        "dataschema": f"keep://events/{INCIDENT_CREATED}/1",
        "data": {"incident_id": str(incident_id)},
    }
    row = DomainEventOutbox(
        tenant_id=TENANT_ID,
        type=INCIDENT_CREATED,
        subject=str(incident_id),
        payload=json.dumps(envelope),
        status=DomainEventOutboxStatus.PENDING.value,
        attempts=0,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_dispatch_marks_delivered_on_2xx(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", "https://aiops.example.com/v1/events/keep")
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_SECRET", "webhook-secret")
    row = _insert_pending_row(session)

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return _FakeResponse(202)

    monkeypatch.setattr(domain_events.requests, "post", fake_post)

    stats = dispatch_pending_events(session)

    assert stats["delivered"] == 1
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.DELIVERED.value
    assert row.attempts == 1
    assert row.last_attempt_at is not None

    # transport contract: url, content-type and valid HMAC over the raw body
    assert captured["url"] == "https://aiops.example.com/v1/events/keep"
    assert captured["headers"]["Content-Type"] == "application/cloudevents+json"
    expected_sig = "sha256=" + hmac.new(
        b"webhook-secret", captured["data"], hashlib.sha256
    ).hexdigest()
    assert captured["headers"][SIGNATURE_HEADER] == expected_sig
    assert json.loads(captured["data"].decode())["id"] == json.loads(row.payload)["id"]


def test_dispatch_retries_on_non_2xx_and_keeps_pending(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", "https://aiops.example.com/v1/events/keep")
    row = _insert_pending_row(session)

    monkeypatch.setattr(
        domain_events.requests, "post", lambda *a, **k: _FakeResponse(500)
    )

    stats = dispatch_pending_events(session)

    assert stats["retried"] == 1
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.PENDING.value
    assert row.attempts == 1


def test_dispatch_marks_failed_after_max_attempts(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", "https://aiops.example.com/v1/events/keep")
    row = _insert_pending_row(
        session,
        attempts=domain_events.DOMAIN_EVENTS_MAX_ATTEMPTS - 1,
        # old enough that the exponential backoff has elapsed
        last_attempt_at=datetime.utcnow() - timedelta(hours=1),
    )

    monkeypatch.setattr(
        domain_events.requests, "post", lambda *a, **k: _FakeResponse(500)
    )

    stats = dispatch_pending_events(session)

    assert stats["failed"] == 1
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.FAILED.value
    assert row.attempts == domain_events.DOMAIN_EVENTS_MAX_ATTEMPTS


def test_dispatch_marks_failed_after_max_attempts_on_exception(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", "https://aiops.example.com/v1/events/keep")
    row = _insert_pending_row(
        session,
        attempts=domain_events.DOMAIN_EVENTS_MAX_ATTEMPTS - 1,
        last_attempt_at=datetime.utcnow() - timedelta(hours=1),
    )

    def boom(*a, **k):
        raise ConnectionError("aiops plane is down")

    monkeypatch.setattr(domain_events.requests, "post", boom)

    # must not raise into the caller
    stats = dispatch_pending_events(session)

    assert stats["failed"] == 1
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.FAILED.value


def test_dispatch_respects_backoff(session, monkeypatch):
    monkeypatch.setenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", "https://aiops.example.com/v1/events/keep")
    row = _insert_pending_row(
        session, attempts=1, last_attempt_at=datetime.utcnow()
    )

    called = []

    def fake_post(*a, **k):
        called.append(True)
        return _FakeResponse(202)

    monkeypatch.setattr(domain_events.requests, "post", fake_post)

    stats = dispatch_pending_events(session)

    # within the backoff window -> not attempted
    assert called == []
    assert stats["skipped"] == 1
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.PENDING.value
    assert row.attempts == 1


def test_dispatch_noop_without_webhook_url(session, monkeypatch):
    monkeypatch.delenv("KEEP_DOMAIN_EVENTS_WEBHOOK_URL", raising=False)
    row = _insert_pending_row(session)

    stats = dispatch_pending_events(session)

    assert stats == {"delivered": 0, "failed": 0, "retried": 0, "skipped": 0}
    session.refresh(row)
    assert row.status == DomainEventOutboxStatus.PENDING.value
    assert row.attempts == 0
