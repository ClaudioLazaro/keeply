"""Human feedback API: upsert semantics, validation, tenant isolation."""

from datetime import datetime, timedelta, timezone

import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import KEEP_API_URL

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
KEY_A = "key-for-tenant-a"
KEY_B = "key-for-tenant-b"


def _seed_investigation(tenant_id: str = TENANT_A) -> str:
    from aiops_api.db import get_engine
    from aiops_api.modules.orchestrator.models import Investigation

    investigation = Investigation(tenant_id=tenant_id, incident_id=f"inc-{tenant_id}")
    with Session(get_engine()) as session:
        session.add(investigation)
        session.commit()
        session.refresh(investigation)
        return investigation.id


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


# --------------------------------------------------------------------------- #
# Create + read
# --------------------------------------------------------------------------- #


def test_create_feedback_then_get_returns_it(client):
    investigation_id = _seed_investigation()

    response = client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "useful", "comment": "spot on"},
    )
    assert response.status_code == 200
    created = response.json()
    assert created["investigation_id"] == investigation_id
    assert created["tenant_id"] == TENANT_A
    assert created["rating"] == "useful"
    assert created["comment"] == "spot on"
    assert created["created_at"] and created["updated_at"]

    fetched = client.get(f"/v1/investigations/{investigation_id}/feedback")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_create_feedback_without_comment(client):
    investigation_id = _seed_investigation()
    response = client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "not_useful"},
    )
    assert response.status_code == 200
    assert response.json()["comment"] is None


def test_get_feedback_not_found(client):
    investigation_id = _seed_investigation()
    response = client.get(f"/v1/investigations/{investigation_id}/feedback")
    assert response.status_code == 404
    assert response.json()["detail"] == "feedback not found"


# --------------------------------------------------------------------------- #
# Upsert
# --------------------------------------------------------------------------- #


def test_repeated_post_replaces_feedback(client, monkeypatch):
    investigation_id = _seed_investigation()
    first = client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "useful"},
    ).json()

    # Force a distinguishable updated_at for the upsert.
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr("aiops_api.modules.feedback.router._utcnow", lambda: later)

    response = client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "not_useful", "comment": "wrong root cause"},
    )
    assert response.status_code == 200
    second = response.json()
    assert second["id"] == first["id"]
    assert second["rating"] == "not_useful"
    assert second["comment"] == "wrong root cause"
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] > first["updated_at"]

    # Still exactly one feedback row for the investigation.
    fetched = client.get(f"/v1/investigations/{investigation_id}/feedback")
    assert fetched.json()["id"] == first["id"]


# --------------------------------------------------------------------------- #
# Validation + missing resources
# --------------------------------------------------------------------------- #


def test_unknown_investigation_returns_404(client):
    response = client.post(
        "/v1/investigations/does-not-exist/feedback",
        json={"rating": "useful"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "investigation not found"
    assert client.get("/v1/investigations/does-not-exist/feedback").status_code == 404


def test_invalid_rating_returns_422(client):
    investigation_id = _seed_investigation()
    response = client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "meh"},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


@respx.mock
def test_cross_tenant_feedback_returns_404(auth_client):
    _mock_whoami(respx, KEY_A, TENANT_A)
    _mock_whoami(respx, KEY_B, TENANT_B)
    investigation_b = _seed_investigation(TENANT_B)

    # Cross-tenant POST and GET both 404 (existence is not leaked).
    response = auth_client.post(
        f"/v1/investigations/{investigation_b}/feedback",
        json={"rating": "useful"},
        headers={"X-API-KEY": KEY_A},
    )
    assert response.status_code == 404
    response = auth_client.get(
        f"/v1/investigations/{investigation_b}/feedback",
        headers={"X-API-KEY": KEY_A},
    )
    assert response.status_code == 404

    # The owning tenant can still give feedback on its own investigation.
    response = auth_client.post(
        f"/v1/investigations/{investigation_b}/feedback",
        json={"rating": "useful"},
        headers={"X-API-KEY": KEY_B},
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == TENANT_B
