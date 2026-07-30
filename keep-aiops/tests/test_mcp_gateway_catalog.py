"""Catalog and health contract tests for the MCP gateway."""

from fastapi.testclient import TestClient

from mcp_gateway.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_healthz() -> None:
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "mcp-gateway"


def test_catalog_lists_k8s_tools_as_read() -> None:
    resp = _client().get("/v1/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    by_name = {t["name"]: t for t in tools}

    for name in ("get_pods", "get_events", "get_logs"):
        assert name in by_name, f"{name} missing from catalog"
        entry = by_name[name]
        assert entry["execution_class"] == "read"
        assert entry["description"]
        assert entry["input_schema"]["type"] == "object"

    # get_logs requires pod; tail_lines is an optional bounded integer
    logs_schema = by_name["get_logs"]["input_schema"]
    assert "pod" in logs_schema["required"]
    assert logs_schema["properties"]["tail_lines"]["type"] == "integer"
