"""Shared fixtures: isolated settings + SQLite DB, respx mocks, event helpers."""

import hashlib
import hmac
import json
import uuid

import pytest
import respx
from fastapi.testclient import TestClient

WEBHOOK_SECRET = "test-webhook-secret"
KEEP_API_URL = "http://keep.test"
MCP_GATEWAY_URL = "http://mcp.test"
TENANT_ID = "11111111-2222-3333-4444-555555555555"

MCP_CATALOG = [
    {"name": "get_pods", "description": "List pods", "execution_class": "read", "input_schema": {}},
    {"name": "get_events", "description": "List events", "execution_class": "read", "input_schema": {}},
    {"name": "get_logs", "description": "Pod logs", "execution_class": "read", "input_schema": {}},
]

PODS_RESULT = {"pods": [{"name": "payment-api-7d9f"}]}
EVENTS_RESULT = {"events": [{"reason": "BackOff", "message": "Back-off restarting failed container"}]}
LOGS_RESULT = {"logs": "line1\nline2"}


@pytest.fixture()
def settings_env(tmp_path, monkeypatch):
    """Point settings at throwaway infra before the app is (re)built."""
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite:///{tmp_path}/aiops-test.db")
    monkeypatch.setenv("AIOPS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("AIOPS_KEEP_API_URL", KEEP_API_URL)
    monkeypatch.setenv("AIOPS_KEEP_API_KEY", "test-api-key")
    monkeypatch.setenv("AIOPS_MCP_GATEWAY_URL", MCP_GATEWAY_URL)
    monkeypatch.setenv("AIOPS_AUTO_INVESTIGATE_SEVERITIES", '["critical", "high"]')
    # Existing M0 tests exercise unauthenticated paths; tenant auth has its own suite.
    monkeypatch.setenv("AIOPS_AUTH_ENABLED", "false")

    from aiops_api import db
    from aiops_api.settings import get_settings

    get_settings.cache_clear()
    db.reset_engine()
    yield
    db.reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def client(settings_env):
    from aiops_api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def make_event(event_type: str = "incident.created", incident_id: str | None = None, **data_overrides) -> dict:
    incident_id = incident_id or str(uuid.uuid4())
    data = {
        "incident_id": incident_id,
        "name": "Payment API elevated 5xx rate",
        "severity": "critical",
        "status": "firing",
        "alerts_count": 3,
        "fingerprint": "fp-9f8e7d6c5b",
        "services": ["payment-api"],
        "sources": ["prometheus"],
        "is_predicted": False,
    }
    data.update(data_overrides)
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "type": event_type,
        "source": "keep-api",
        "time": "2026-07-29T12:00:00Z",
        "tenantid": TENANT_ID,
        "subject": incident_id,
        "dataschema": f"keep://events/{event_type}/1",
        "data": data,
    }


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_event(client: TestClient, event: dict, secret: str = WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return client.post(
        "/v1/events/keep",
        content=body,
        headers={
            "Content-Type": "application/cloudevents+json",
            "X-Keep-Signature": sign(body, secret),
        },
    )


@pytest.fixture
def mocked_backends():
    """MCP gateway (all-read catalog) + Keep API writeback endpoints.

    Yields the respx router with named route handles attached for assertions.
    """
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(200, json=MCP_CATALOG)
        router.get_pods_route = router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_pods:invoke").respond(
            200, json={"result": PODS_RESULT, "audit_id": "audit-pods"}
        )
        router.get_events_route = router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_events:invoke").respond(
            200, json={"result": EVENTS_RESULT, "audit_id": "audit-events"}
        )
        router.get_logs_route = router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_logs:invoke").respond(
            200, json={"result": LOGS_RESULT, "audit_id": "audit-logs"}
        )
        router.get(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+$").respond(
            200,
            json={
                "id": "irrelevant",
                "status": "firing",
                "severity": "critical",
                "user_generated_name": "Payment API elevated 5xx rate",
                "alerts_count": 3,
            },
        )
        router.comment_route = router.post(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+/comment$").respond(
            200, json={"id": 1}
        )
        router.enrich_route = router.post(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+/enrich$").respond(
            202, json={}
        )
        yield router
