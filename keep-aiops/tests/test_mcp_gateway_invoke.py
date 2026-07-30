"""Invocation, policy-gate and audit tests for the MCP gateway."""

import json

import pytest
from fastapi.testclient import TestClient

from mcp_gateway.main import create_app
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool, unregister_tool

PAYLOAD = {"tenant_id": "tenant-1", "investigation_id": "inv-123", "arguments": {}}


@pytest.fixture()
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_invoke_get_pods_stub_returns_crashloopbackoff(client: TestClient) -> None:
    resp = client.post("/v1/mcp/tools/get_pods:invoke", json=PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["audit_id"]

    pods = body["result"]["pods"]
    assert len(pods) >= 2
    crashlooping = [p for p in pods if p["state"].get("waiting", {}).get("reason") == "CrashLoopBackOff"]
    assert crashlooping, "expected at least one CrashLoopBackOff pod in stub payload"
    assert any("payment-api" in p["name"] for p in crashlooping)


def test_invoke_get_events_stub_has_warnings(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/get_events:invoke",
        json={**PAYLOAD, "arguments": {"namespace": "payments"}},
    )
    assert resp.status_code == 200
    events = resp.json()["result"]["events"]
    warning_reasons = {e["reason"] for e in events if e["type"] == "Warning"}
    assert "BackOff" in warning_reasons


def test_invoke_get_logs_stub_returns_oom_trace(client: TestClient) -> None:
    resp = client.post(
        "/v1/mcp/tools/get_logs:invoke",
        json={**PAYLOAD, "arguments": {"pod": "payment-api-7d9f4b6c5-x2vkm", "namespace": "payments"}},
    )
    assert resp.status_code == 200
    lines = resp.json()["result"]["lines"]
    assert any("OutOfMemoryError" in line for line in lines)


def test_invoke_invalid_args_422(client: TestClient) -> None:
    # missing required 'pod'
    resp = client.post("/v1/mcp/tools/get_logs:invoke", json=PAYLOAD)
    assert resp.status_code == 422
    # wrong type for 'tail_lines'
    resp = client.post(
        "/v1/mcp/tools/get_logs:invoke",
        json={**PAYLOAD, "arguments": {"pod": "payment-api", "tail_lines": "many"}},
    )
    assert resp.status_code == 422
    # unexpected argument
    resp = client.post(
        "/v1/mcp/tools/get_pods:invoke",
        json={**PAYLOAD, "arguments": {"bogus": 1}},
    )
    assert resp.status_code == 422


def test_invoke_unknown_tool_403(client: TestClient) -> None:
    resp = client.post("/v1/mcp/tools/restart_pod:invoke", json=PAYLOAD)
    assert resp.status_code == 403
    assert "fail-closed" in resp.json()["detail"]


def test_invoke_mutate_tool_403_fail_closed(client: TestClient) -> None:
    @register_tool(
        name="delete_pod",
        description="test-only mutate tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution_class="mutate",
    )
    def _delete_pod() -> dict:  # pragma: no cover - must never run
        raise AssertionError("mutate tool must never be invoked")

    try:
        resp = client.post("/v1/mcp/tools/delete_pod:invoke", json=PAYLOAD)
        assert resp.status_code == 403
    finally:
        unregister_tool("delete_pod")


def test_audit_entry_recorded(client: TestClient, tmp_path, monkeypatch) -> None:
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MCP_AUDIT_LOG_PATH", str(audit_file))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        resp = c.post("/v1/mcp/tools/get_pods:invoke", json=PAYLOAD)
        assert resp.status_code == 200
        audit_id = resp.json()["audit_id"]
        # denied invocations are audited too
        c.post("/v1/mcp/tools/restart_pod:invoke", json=PAYLOAD)

    lines = audit_file.read_text().splitlines()
    assert len(lines) == 2
    success = json.loads(lines[0])
    assert success["audit_id"] == audit_id
    assert success["tool"] == "get_pods"
    assert success["tenant_id"] == "tenant-1"
    assert success["investigation_id"] == "inv-123"
    assert success["outcome"] == "success"
    assert success["args_hash"]
    assert success["duration_ms"] >= 0
    assert "ts" in success

    denied = json.loads(lines[1])
    assert denied["tool"] == "restart_pod"
    assert denied["outcome"] == "policy_denied"
