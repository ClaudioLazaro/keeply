"""Resilience: individual tool failures become evidence gaps, run continues."""

import respx

from tests.conftest import (
    EVENTS_RESULT,
    KEEP_API_URL,
    MCP_CATALOG,
    MCP_GATEWAY_URL,
    PODS_RESULT,
    make_event,
    post_event,
)


def test_tool_failure_recorded_as_evidence_gap(client):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(200, json=MCP_CATALOG)
        router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_pods:invoke").respond(
            200, json={"result": PODS_RESULT, "audit_id": "audit-pods"}
        )
        router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_events:invoke").respond(500, text="boom")
        router.post(f"{MCP_GATEWAY_URL}/v1/mcp/tools/get_logs:invoke").respond(
            200, json={"result": {"logs": "x"}, "audit_id": "audit-logs"}
        )
        router.get(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+$").respond(
            200, json={"id": "x", "status": "firing"}
        )
        router.post(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+/comment$").respond(200, json={"id": 1})
        router.post(url__regex=rf"{KEEP_API_URL}/incidents/[^/]+/enrich$").respond(202, json={})

        event = make_event("incident.created")
        assert post_event(client, event).status_code == 202

    investigation = client.get("/v1/investigations").json()[0]
    assert investigation["status"] == "rca_ready"
    evidence = client.get(f"/v1/investigations/{investigation['id']}/evidence").json()
    assert len(evidence) == 3
    gap = next(item for item in evidence if item["tool"] == "get_events")
    assert "evidence gap" in gap["summary"]
    assert "boom" in gap["payload"]["error"] or "500" in gap["payload"]["error"]
    assert investigation["rca_draft"] is not None
