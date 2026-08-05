"""Tenant auth: Keep-delegated API-key validation and tenant isolation."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import KEEP_API_URL, make_event, post_event

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
KEY_A = "key-for-tenant-a"
KEY_B = "key-for-tenant-b"


@pytest.fixture()
def auth_client(settings_env, monkeypatch):
    """Client with tenant auth ENABLED; identity cache cleared per test."""
    monkeypatch.setenv("AIOPS_AUTH_ENABLED", "true")

    from aiops_api.main import create_app
    from aiops_api.modules.auth import clear_cache
    from aiops_api.settings import get_settings

    get_settings.cache_clear()
    clear_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    clear_cache()


def _mock_whoami(router: respx.MockRouter, key: str, tenant_id: str):
    return router.get(f"{KEEP_API_URL}/whoami", headers={"X-API-KEY": key}).respond(
        200, json={"tenant_id": tenant_id}
    )


def _seed_investigation(tenant_id: str) -> str:
    from aiops_api.db import get_engine
    from aiops_api.modules.orchestrator.models import Evidence, Investigation

    investigation = Investigation(tenant_id=tenant_id, incident_id=f"inc-{tenant_id}")
    with Session(get_engine()) as session:
        session.add(investigation)
        session.commit()
        session.refresh(investigation)
        session.add(Evidence(investigation_id=investigation.id, tool="get_pods", summary="s"))
        session.commit()
        return investigation.id


# --------------------------------------------------------------------------- #
# Credential enforcement
# --------------------------------------------------------------------------- #


def test_missing_api_key_rejected(auth_client):
    response = auth_client.get("/v1/investigations")
    assert response.status_code == 401


def test_bearer_jwt_not_supported(auth_client):
    response = auth_client.get("/v1/investigations", headers={"Authorization": "Bearer abc"})
    assert response.status_code == 401


@respx.mock
def test_invalid_api_key_rejected(auth_client):
    respx.get(f"{KEEP_API_URL}/whoami").respond(401)
    response = auth_client.get("/v1/investigations", headers={"X-API-KEY": "bad-key"})
    assert response.status_code == 401


@respx.mock
def test_keep_unreachable_fails_closed(auth_client):
    respx.get(f"{KEEP_API_URL}/whoami").mock(side_effect=httpx.ConnectError("refused"))
    response = auth_client.get("/v1/investigations", headers={"X-API-KEY": KEY_A})
    assert response.status_code == 503
    assert "retry" in response.json()["detail"].lower()
    assert response.headers.get("retry-after")


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


@respx.mock
def test_cross_tenant_access_returns_404(auth_client):
    _mock_whoami(respx, KEY_A, TENANT_A)
    _mock_whoami(respx, KEY_B, TENANT_B)
    investigation_a = _seed_investigation(TENANT_A)
    investigation_b = _seed_investigation(TENANT_B)

    # List is scoped to the request tenant.
    response = auth_client.get("/v1/investigations", headers={"X-API-KEY": KEY_A})
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert ids == [investigation_a]

    # The tenant_id query param cannot widen scope.
    response = auth_client.get(
        "/v1/investigations", params={"tenant_id": TENANT_B}, headers={"X-API-KEY": KEY_A}
    )
    assert response.status_code == 200
    assert response.json() == []

    # Cross-tenant detail/evidence reads 404 (existence is not leaked).
    for path in (f"/v1/investigations/{investigation_b}", f"/v1/investigations/{investigation_b}/evidence"):
        response = auth_client.get(path, headers={"X-API-KEY": KEY_A})
        assert response.status_code == 404, path

    # Same-tenant reads succeed, evidence included.
    response = auth_client.get(f"/v1/investigations/{investigation_a}", headers={"X-API-KEY": KEY_A})
    assert response.status_code == 200
    assert response.json()["tenant_id"] == TENANT_A
    response = auth_client.get(
        f"/v1/investigations/{investigation_a}/evidence", headers={"X-API-KEY": KEY_A}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


# --------------------------------------------------------------------------- #
# Identity cache
# --------------------------------------------------------------------------- #


@respx.mock
def test_identity_cache_avoids_second_keep_call(auth_client):
    route = _mock_whoami(respx, KEY_A, TENANT_A)
    headers = {"X-API-KEY": KEY_A}
    assert auth_client.get("/v1/investigations", headers=headers).status_code == 200
    assert auth_client.get("/v1/investigations", headers=headers).status_code == 200
    assert route.call_count == 1


# --------------------------------------------------------------------------- #
# Event bridge exemption
# --------------------------------------------------------------------------- #


def test_event_bridge_hmac_endpoint_exempt_from_api_key(auth_client):
    # Server-to-server HMAC endpoint: no X-API-KEY, valid signature still accepted.
    event = make_event("incident.created", severity="low")
    response = post_event(auth_client, event)
    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_identity_cache_stays_bounded(settings_env):
    """Entries were expired on read but never removed, so the dict only grew.

    A caller rotating keys (or probing with keys that authenticate) grew it
    for the life of the process.
    """
    import time as _time

    from aiops_api.modules.auth import clear_cache, middleware

    clear_cache()
    context = middleware.TenantContext(tenant_id=TENANT_A)
    for i in range(middleware._CACHE_MAX_ENTRIES + 50):
        middleware._store(f"hash-{i}", _time.monotonic() + 60, context)

    assert len(middleware._cache) <= middleware._CACHE_MAX_ENTRIES
    clear_cache()
