"""M2 context builder: pack shape, partial-failure tolerance, timeline limit,
migration 0003 importability + DDL, keep_client topology/timeline 404s."""

import importlib.util
from pathlib import Path

import pytest
import respx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from aiops_api.settings import get_settings
from keep_client import KeepClient

from tests.conftest import KEEP_API_URL, TENANT_ID, make_event, post_event

REPO_AIOPS_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_AIOPS_DIR / "alembic.ini"
MIGRATION_0003 = REPO_AIOPS_DIR / "alembic" / "versions" / "0003_investigation_context_pack.py"

INCIDENT_ID = "inc-1"

INCIDENT_PAYLOAD = {
    "id": INCIDENT_ID,
    "status": "firing",
    "severity": "critical",
    "user_generated_name": "Payment API elevated 5xx rate",
    "alerts_count": 2,
    "services": ["payment-api"],
    "alert_sources": ["prometheus"],
}

ALERTS_PAYLOAD = {
    "items": [
        {"id": "a1", "name": "Http5xxHigh", "severity": "critical", "fingerprint": "fp-1"},
        {"id": "a2", "name": "LatencyHigh", "severity": "high", "fingerprint": "fp-2"},
    ],
    "count": 2,
    "limit": 25,
    "offset": 0,
}

TOPOLOGY_PAYLOAD = [
    {
        "id": "svc-1",
        "service": "payment-api",
        "display_name": "Payment API",
        "environment": "production",
        "dependencies": [{"id": 10, "serviceId": "svc-2", "protocol": "tcp"}],
        "application_ids": [],
        "updated_at": None,
    },
    {
        "id": "svc-2",
        "service": "postgres",
        "display_name": "Postgres",
        "environment": "production",
        "dependencies": [],
        "application_ids": [],
        "updated_at": None,
    },
    # Unrelated service the (unfiltered) server might return; must be dropped.
    {
        "id": "svc-3",
        "service": "billing",
        "display_name": "Billing",
        "environment": "production",
        "dependencies": [],
        "application_ids": [],
        "updated_at": None,
    },
]

AUDIT_PAYLOAD = [
    {
        "id": "1",
        "timestamp": "2026-07-29T12:03:00Z",
        "fingerprint": "fp-1",
        "action": "alert status change",
        "user_id": "keep-bot",
        "description": "Alert status changed to firing",
    },
    {
        "id": "2",
        "timestamp": "2026-07-29T12:02:00Z",
        "fingerprint": "fp-1",
        "action": "incident assigned",
        "user_id": "operator@example.com",
        "description": "Incident assigned to operator@example.com",
    },
    {
        "id": "3",
        "timestamp": "2026-07-29T12:01:00Z",
        "fingerprint": "fp-2",
        "action": "alert created",
        "user_id": "keep-bot",
        "description": "Alert created",
    },
]


def mock_keep_full(router: respx.MockRouter) -> None:
    router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}").respond(200, json=INCIDENT_PAYLOAD)
    router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}/alerts").respond(200, json=ALERTS_PAYLOAD)
    router.get(f"{KEEP_API_URL}/topology").respond(200, json=TOPOLOGY_PAYLOAD)
    router.post(f"{KEEP_API_URL}/alerts/audit").respond(200, json=AUDIT_PAYLOAD)


@pytest.fixture()
def settings(settings_env):
    get_settings.cache_clear()
    return get_settings()


def test_full_pack_matches_contract_shape(settings):
    from aiops_api.modules.context_builder import build_context_pack

    with respx.mock(assert_all_called=False) as router:
        mock_keep_full(router)
        pack = build_context_pack(TENANT_ID, INCIDENT_ID, settings=settings)

    assert set(pack) >= {"incident", "alerts", "topology", "timeline", "built_at", "errors"}
    assert pack["errors"] == []
    assert pack["incident"]["id"] == INCIDENT_ID
    assert pack["incident"]["services"] == ["payment-api"]
    assert [alert["name"] for alert in pack["alerts"]] == ["Http5xxHigh", "LatencyHigh"]
    # Incident service plus its direct dependency neighbour; unrelated dropped.
    assert {entry["service"] for entry in pack["topology"]} == {"payment-api", "postgres"}
    assert len(pack["timeline"]) == 3
    first = pack["timeline"][0]
    assert set(first) == {"timestamp", "action", "description", "actor"}
    assert first["actor"] == "keep-bot"
    assert first["action"] == "alert status change"
    assert isinstance(pack["built_at"], str) and pack["built_at"]


def test_topology_query_is_scoped_to_incident_services(settings):
    from aiops_api.modules.context_builder import build_context_pack

    with respx.mock(assert_all_called=False) as router:
        mock_keep_full(router)
        build_context_pack(TENANT_ID, INCIDENT_ID, settings=settings)
        topology_request = router.get(f"{KEEP_API_URL}/topology").calls.last.request
    assert topology_request.url.params["services"] == "payment-api"


def test_section_failures_degrade_to_empty_with_errors(settings):
    from aiops_api.modules.context_builder import build_context_pack

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}").respond(200, json=INCIDENT_PAYLOAD)
        router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}/alerts").respond(500, text="boom")
        router.get(f"{KEEP_API_URL}/topology").respond(500, text="boom")
        router.post(f"{KEEP_API_URL}/alerts/audit").respond(200, json=AUDIT_PAYLOAD)
        pack = build_context_pack(TENANT_ID, INCIDENT_ID, settings=settings)

    assert pack["incident"]["id"] == INCIDENT_ID
    assert pack["alerts"] == []
    assert pack["topology"] == []
    assert len(pack["timeline"]) == 3
    failed_sections = {entry["section"] for entry in pack["errors"]}
    assert failed_sections == {"alerts", "topology"}
    assert all(entry["error"] for entry in pack["errors"])


def test_incident_failure_degrades_everything_but_never_raises(settings):
    from aiops_api.modules.context_builder import build_context_pack

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}").respond(404, json={"detail": "nope"})
        router.get(f"{KEEP_API_URL}/incidents/{INCIDENT_ID}/alerts").respond(404, json={"detail": "nope"})
        router.post(f"{KEEP_API_URL}/alerts/audit").respond(404, json={"detail": "Alert not found"})
        pack = build_context_pack(TENANT_ID, INCIDENT_ID, settings=settings)

    assert pack["incident"] == {}
    assert pack["alerts"] == []
    # 404 is a tolerated absence for topology/timeline, not an error entry.
    assert pack["topology"] == []
    assert pack["timeline"] == []
    assert {entry["section"] for entry in pack["errors"]} == {"incident", "alerts"}


def test_timeline_limit_honored(settings, monkeypatch):
    from aiops_api.modules.context_builder import build_context_pack

    monkeypatch.setenv("AIOPS_CONTEXT_TIMELINE_LIMIT", "1")
    get_settings.cache_clear()
    try:
        with respx.mock(assert_all_called=False) as router:
            mock_keep_full(router)
            pack = build_context_pack(TENANT_ID, INCIDENT_ID, settings=get_settings())
    finally:
        get_settings.cache_clear()

    assert len(pack["timeline"]) == 1
    assert pack["timeline"][0]["description"] == "Alert status changed to firing"


# --------------------------------------------------------------------------- #
# Orchestrator integration: gathering phase persists the pack on the row
# --------------------------------------------------------------------------- #


def test_fsm_persists_context_pack_on_investigation(client, mocked_backends):
    event = make_event("incident.created")
    response = post_event(client, event)
    assert response.status_code == 202

    investigations = client.get("/v1/investigations", params={"incident_id": event["subject"]}).json()
    assert len(investigations) == 1
    pack = investigations[0]["context_pack"]
    assert pack is not None
    # Incident fetch is mocked; the unmocked sections degrade with error entries.
    assert pack["incident"]["status"] == "firing"
    assert set(pack) >= {"incident", "alerts", "topology", "timeline", "built_at", "errors"}


# --------------------------------------------------------------------------- #
# keep_client 404/empty tolerance (direct, without the builder)
# --------------------------------------------------------------------------- #


def test_client_topology_404_and_empty_tolerated():
    client = KeepClient(base_url=KEEP_API_URL, api_key="k")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/incidents/gone").respond(404, json={"detail": "nope"})
        assert client.get_incident_topology("gone") == []
        router.get(f"{KEEP_API_URL}/incidents/no-services").respond(
            200, json={"id": "no-services", "services": []}
        )
        assert client.get_incident_topology("no-services") == []


def test_client_timeline_404_and_empty_tolerated():
    client = KeepClient(base_url=KEEP_API_URL, api_key="k")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{KEEP_API_URL}/alerts/audit").respond(404, json={"detail": "Alert not found"})
        assert client.get_incident_timeline("inc-x") == []
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{KEEP_API_URL}/alerts/audit").respond(200, json=[])
        assert client.get_incident_timeline("inc-x") == []
    import json as _json

    assert _json.loads(route.calls.last.request.content) == ["inc-x"]


# --------------------------------------------------------------------------- #
# Migration 0003
# --------------------------------------------------------------------------- #


def test_migration_0003_imports_cleanly():
    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION_0003)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0003_investigation_context_pack"
    assert module.down_revision == "0002_policy_tables"
    assert callable(module.upgrade) and callable(module.downgrade)


def test_migration_0003_adds_and_drops_context_pack(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migrated.db"
    monkeypatch.setenv("AIOPS_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config(str(ALEMBIC_INI))
        command.upgrade(cfg, "0003_investigation_context_pack")
        engine = create_engine(url)
        try:
            columns = {col["name"] for col in inspect(engine).get_columns("investigation")}
            assert "context_pack" in columns
        finally:
            engine.dispose()

        command.downgrade(cfg, "0002_policy_tables")
        engine = create_engine(url)
        try:
            columns = {col["name"] for col in inspect(engine).get_columns("investigation")}
            assert "context_pack" not in columns
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
