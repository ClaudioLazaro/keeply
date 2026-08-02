"""Console read APIs backing the AIOps UI section: GET /v1/tools, GET /v1/stats."""

import httpx
import respx

from tests.conftest import MCP_CATALOG, MCP_GATEWAY_URL, TENANT_ID, make_event, post_event


# --------------------------------------------------------------------------- #
# GET /v1/tools
# --------------------------------------------------------------------------- #


def test_tools_catalog_annotates_policy_decision(client, mocked_backends):
    response = client.get("/v1/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["gateway_available"] is True
    assert body["error"] is None
    assert [tool["name"] for tool in body["tools"]] == ["get_events", "get_logs", "get_pods"]
    # Every catalog tool is read-class, and the seeded suggest-only policy
    # allows read — so the console shows them as invocable.
    for tool in body["tools"]:
        assert tool["execution_class"] == "read"
        assert tool["decision"] == "allow"
        assert tool["policy_id"] == "m0-suggest-only"


def test_tools_catalog_passes_through_stub_live_mode(client):
    """Dropping `mode` would leave the console unable to tell an operator
    which tools return demo data."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(
            200,
            json=[
                {
                    "name": "get_pods",
                    "description": "d",
                    "execution_class": "read",
                    "input_schema": {},
                    "mode": "live",
                },
                {
                    "name": "dd_list_events",
                    "description": "d",
                    "execution_class": "read",
                    "input_schema": {},
                    "mode": "stub",
                },
            ],
        )
        body = client.get("/v1/tools").json()

    modes = {tool["name"]: tool["mode"] for tool in body["tools"]}
    assert modes == {"get_pods": "live", "dd_list_events": "stub"}


def test_tool_without_declared_mode_is_unknown_not_live(client):
    """Safe default: never imply a tool returns real data."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(
            200,
            json=[{"name": "t", "description": "d", "execution_class": "read", "input_schema": {}}],
        )
        body = client.get("/v1/tools").json()

    assert body["tools"][0]["mode"] == "unknown"


def test_tools_catalog_denies_mutate_class_fail_closed(client):
    """A mutate tool in the catalog must surface as denied, not as invocable."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(
            200,
            json=MCP_CATALOG
            + [
                {
                    "name": "restart_pod",
                    "description": "Restart a pod",
                    "execution_class": "mutate",
                    "input_schema": {},
                }
            ],
        )
        response = client.get("/v1/tools")

    assert response.status_code == 200
    decisions = {tool["name"]: tool["decision"] for tool in response.json()["tools"]}
    assert decisions["restart_pod"] == "deny"
    assert decisions["get_pods"] == "allow"


def test_tools_catalog_unknown_execution_class_is_denied(client):
    """Fail-closed: a class no rule matches falls through to the default deny."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(
            200,
            json=[
                {
                    "name": "weird_tool",
                    "description": "Unknown class",
                    "execution_class": "teleport",
                    "input_schema": {},
                }
            ],
        )
        response = client.get("/v1/tools")

    tool = response.json()["tools"][0]
    assert tool["decision"] == "deny"
    assert tool["policy_id"] is None


def test_tools_catalog_degrades_when_gateway_is_down(client):
    """A dead gateway is a UI state, not a 500 — the page must still render."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        response = client.get("/v1/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["gateway_available"] is False
    assert body["tools"] == []
    assert "ConnectError" in body["error"]


# --------------------------------------------------------------------------- #
# GET /v1/stats
# --------------------------------------------------------------------------- #


def test_stats_on_empty_database_returns_all_status_buckets(client):
    response = client.get("/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["investigations_total"] == 0
    # Every FSM bucket is present even at zero, so the UI never has to guess.
    assert set(body["investigations_by_status"]) == {
        "queued",
        "gathering",
        "hypothesizing",
        "rca_ready",
        "failed",
        "cancelled",
    }
    assert all(count == 0 for count in body["investigations_by_status"].values())
    assert body["evidence_total"] == 0
    assert body["evidence_gaps"] == 0
    assert body["mode"] == "suggest"
    assert body["llm_enabled"] is False


def test_stats_counts_investigation_and_its_evidence(client, mocked_backends):
    post_event(client, make_event())

    response = client.get("/v1/stats")

    body = response.json()
    assert body["investigations_total"] == 1
    assert body["investigations_by_status"]["rca_ready"] == 1
    assert body["investigations_last_24h"] == 1
    assert body["evidence_total"] == len(MCP_CATALOG)
    assert body["evidence_gaps"] == 0


def test_stats_reports_evidence_gaps(client, mocked_backends):
    """A failing tool becomes a gap, and the console surfaces the count."""
    mocked_backends.get_logs_route.mock(side_effect=httpx.ConnectError("gateway down"))

    post_event(client, make_event())

    body = client.get("/v1/stats").json()
    assert body["evidence_gaps"] == 1
    assert body["evidence_total"] == len(MCP_CATALOG)


def test_stats_counts_feedback_by_rating(client, mocked_backends):
    post_event(client, make_event())
    investigation_id = client.get("/v1/investigations").json()[0]["id"]

    client.post(
        f"/v1/investigations/{investigation_id}/feedback",
        json={"rating": "useful", "tenant_id": TENANT_ID},
    )

    body = client.get("/v1/stats").json()
    assert body["feedback_useful"] == 1
    assert body["feedback_not_useful"] == 0


def test_stats_exposes_configured_budget_caps(client, monkeypatch):
    """The console shows the posture without the operator reading the ConfigMap."""
    body = client.get("/v1/stats").json()

    assert body["budget"] == {
        "max_tool_calls": 50,
        "max_wall_time_seconds": 120.0,
        "max_llm_tokens": 200_000,
    }
