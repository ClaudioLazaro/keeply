"""M3 catalog: the new read-only tools (datadog / eks / rds / argocd / jira /
slack / bitbucket / backstage) are registered and all read-class.

The test inverts the previous AC6 contract: every new tool is read-class
(ADR-0003); mutate tools remain denied.
"""

import pytest
from fastapi.testclient import TestClient

from mcp_gateway.main import create_app
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool, unregister_tool


@pytest.fixture()
def client():
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


NEW_TOOLS = {
    "dd_query_metrics": {"query": "avg:http.request.error_rate{service:payment-api}"},
    "dd_list_events": {},
    "eks_list_clusters": {},
    "eks_describe_nodegroups": {"cluster_name": "payments-prod"},
    "rds_list_instances": {},
    "rds_describe_instance_status": {"instance_id": "payments-db"},
    "argocd_list_apps": {},
    "argocd_get_app": {"name": "payment-api"},
    "jira_search_issues": {"jql": "project = PAY"},
    "slack_search_messages": {"query": "payment-api"},
    "bb_list_recent_commits": {"repo": "payments/payment-api"},
    "bb_list_open_pull_requests": {"repo": "payments/payment-api"},
    "backstage_get_entity": {"kind": "Component", "name": "payment-api"},
}


def test_m3_tools_in_catalog_as_read(client):
    resp = client.get("/v1/mcp/tools")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()}

    for tool in NEW_TOOLS:
        assert tool in by_name, f"{tool} missing from catalog"
        assert by_name[tool]["execution_class"] == "read"
        assert by_name[tool]["input_schema"]["type"] == "object"


def test_m3_tools_invoke_in_stub_mode(client):
    for tool, args in NEW_TOOLS.items():
        resp = client.post(
            f"/v1/mcp/tools/{tool}:invoke",
            json={"tenant_id": "tenant-1", "investigation_id": "inv-1", "arguments": args},
        )
        assert resp.status_code == 200, f"{tool} failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert "result" in body
        assert "audit_id" in body


def test_m3_mutate_tool_is_403_fail_closed(client):
    @register_tool(
        name="restart_eks_nodegroup",
        description="Mutate: would restart a nodegroup (M3 policy test).",
        input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        execution_class="mutate",
    )
    def _restart():
        return {"ok": True}

    try:
        resp = client.post(
            "/v1/mcp/tools/restart_eks_nodegroup:invoke",
            json={"tenant_id": "tenant-1", "investigation_id": "inv-1", "arguments": {}},
        )
        assert resp.status_code == 403
        assert "fail-closed" in resp.json()["detail"]
    finally:
        unregister_tool("restart_eks_nodegroup")


def test_live_mode_without_url_returns_503(client, monkeypatch):
    for mode_var, tool, args in [
        ("MCP_DATADOG_MODE", "dd_query_metrics", {"query": "avg:foo"}),
        ("MCP_ARGOCD_MODE", "argocd_list_apps", {}),
        ("MCP_JIRA_MODE", "jira_search_issues", {"jql": "project = PAY"}),
        ("MCP_SLACK_MODE", "slack_search_messages", {"query": "foo"}),
        ("MCP_BITBUCKET_MODE", "bb_list_recent_commits", {"repo": "x/y"}),
        ("MCP_BACKSTAGE_MODE", "backstage_get_entity", {"kind": "Component", "name": "x"}),
    ]:
        monkeypatch.setenv(mode_var, "live")
        get_settings.cache_clear()
        try:
            resp = client.post(
                f"/v1/mcp/tools/{tool}:invoke",
                json={"tenant_id": "tenant-1", "investigation_id": "inv-1", "arguments": args},
            )
            assert resp.status_code == 503, f"{tool} live-mode expected 503 got {resp.status_code}"
            assert "retry" in resp.json()["detail"].get("hint", "")
        finally:
            monkeypatch.delenv(mode_var, raising=False)
            get_settings.cache_clear()
