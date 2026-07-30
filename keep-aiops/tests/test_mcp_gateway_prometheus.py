"""Prometheus MCP tool tests: catalog, stub payloads, validation, live-mode 503."""

import pytest
from fastapi.testclient import TestClient

from mcp_gateway.main import create_app
from mcp_gateway.settings import get_settings

PAYLOAD = {"tenant_id": "tenant-1", "investigation_id": "inv-123", "arguments": {}}


@pytest.fixture()
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_catalog_lists_prometheus_tools_as_read(client: TestClient) -> None:
    resp = client.get("/v1/mcp/tools")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()}

    for name in ("prom_query", "prom_query_range", "prom_alerts"):
        assert name in by_name, f"{name} missing from catalog"
        entry = by_name[name]
        assert entry["execution_class"] == "read"
        assert entry["description"]
        assert entry["input_schema"]["type"] == "object"

    range_schema = by_name["prom_query_range"]["input_schema"]
    assert "query" in range_schema["required"]
    assert range_schema["properties"]["step"]["type"] == "integer"


def test_prom_alerts_stub_has_firing_payment_api_alerts(client: TestClient) -> None:
    resp = client.post("/v1/mcp/tools/prom_alerts:invoke", json=PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["audit_id"]

    alerts = body["result"]["data"]["alerts"]
    firing = {a["labels"]["alertname"]: a for a in alerts if a["state"] == "firing"}
    assert "HighErrorRate" in firing
    assert "PodCrashLooping" in firing
    assert firing["HighErrorRate"]["labels"]["service"] == "payment-api"
    assert "payment-api" in firing["PodCrashLooping"]["labels"]["pod"]


def test_prom_query_stub_returns_elevated_5xx_rate(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query:invoke",
        json={
            **PAYLOAD,
            "arguments": {"query": 'rate(http_requests_total{service="payment-api",status=~"5.."}[5m])'},
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["backend"] == "stub"
    vector = result["data"]["result"]
    assert vector, "expected instant vector results"
    assert any(r["metric"]["service"] == "payment-api" for r in vector)
    payment_rate = max(float(r["value"][1]) for r in vector if r["metric"]["service"] == "payment-api")
    assert payment_rate > 0.1, "stub should show an elevated 5xx rate for payment-api"


def test_prom_query_range_stub_shows_ramp_before_incident(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query_range:invoke",
        json={**PAYLOAD, "arguments": {"query": 'rate(http_requests_total{status=~"5.."}[5m])', "step": 60}},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["data"]["resultType"] == "matrix"
    series = result["data"]["result"][0]
    values = [float(v[1]) for v in series["values"]]
    assert len(values) >= 30, "default 30min window at 60s step should yield ~31 points"
    assert values[-1] > values[0] * 10, "error rate should ramp up into the incident"
    assert all(b >= a for a, b in zip(values, values[1:])), "ramp should be monotonic"


def test_prom_query_missing_query_422(client: TestClient) -> None:
    resp = client.post("/v1/mcp/tools/prom_query:invoke", json=PAYLOAD)
    assert resp.status_code == 422


def test_prom_query_empty_query_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query:invoke",
        json={**PAYLOAD, "arguments": {"query": ""}},
    )
    assert resp.status_code == 422


def test_prom_query_range_bad_step_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query_range:invoke",
        json={**PAYLOAD, "arguments": {"query": "up", "step": 0}},
    )
    assert resp.status_code == 422


def test_prom_query_range_end_before_start_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query_range:invoke",
        json={
            **PAYLOAD,
            "arguments": {
                "query": "up",
                "start": "2026-07-29T10:15:00Z",
                "end": "2026-07-29T09:45:00Z",
            },
        },
    )
    assert resp.status_code == 422


def test_prom_query_range_bad_rfc3339_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/prom_query_range:invoke",
        json={**PAYLOAD, "arguments": {"query": "up", "start": "not-a-timestamp"}},
    )
    assert resp.status_code == 422


def test_live_mode_without_url_503(monkeypatch) -> None:
    monkeypatch.setenv("MCP_PROMETHEUS_MODE", "live")
    monkeypatch.delenv("MCP_PROMETHEUS_URL", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            for tool, args in (
                ("prom_alerts", {}),
                ("prom_query", {"query": "up"}),
                ("prom_query_range", {"query": "up"}),
            ):
                resp = c.post(f"/v1/mcp/tools/{tool}:invoke", json={**PAYLOAD, "arguments": args})
                assert resp.status_code == 503, f"{tool} should be 503 without MCP_PROMETHEUS_URL"
                detail = resp.json()["detail"]
                assert "hint" in detail
                assert "MCP_PROMETHEUS_URL" in detail["error"]
    finally:
        get_settings.cache_clear()
