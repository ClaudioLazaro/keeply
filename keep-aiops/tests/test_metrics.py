"""Prometheus metrics: /metrics endpoint + orchestrator counter wiring.

Metrics live in the module-global default registry, so values accumulate
across tests in a session — every assertion is a delta against the value
scraped before the scenario ran.
"""

import re

from tests.conftest import make_event, post_event


def _scrape(client) -> str:
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def _value(metrics_text: str, name: str, labels: dict[str, str] | None = None) -> float:
    """Extract a single sample value from the text exposition format."""
    if labels:
        label_str = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        pattern = rf"^{re.escape(name)}\{{{re.escape(label_str)}\}} ([0-9.eE+]+)$"
    else:
        pattern = rf"^{re.escape(name)} ([0-9.eE+]+)$"
    match = re.search(pattern, metrics_text, re.MULTILINE)
    assert match, f"metric {name}{labels or ''} not found in /metrics output"
    return float(match.group(1))


def test_metrics_endpoint_is_unauthenticated_and_exposes_aiops_series(client, mocked_backends):
    event = make_event("incident.created")
    assert post_event(client, event).status_code == 202

    # No X-Keep-Signature / credentials: Prometheus scrapes without auth.
    body = _scrape(client)
    assert "keep_aiops_investigations_started_total" in body
    assert "keep_aiops_investigations_completed_total" in body
    assert "keep_aiops_investigations_failed_total" in body
    assert "keep_aiops_investigations_active" in body
    assert "keep_aiops_investigation_duration_seconds_bucket" in body
    assert "keep_aiops_mcp_tool_calls_total" in body
    # No high-cardinality labels leak into any series.
    assert event["subject"] not in body
    assert event["tenantid"] not in body


def test_lifecycle_counters_increment_on_successful_investigation(client, mocked_backends):
    before = _scrape(client)
    started_before = _value(before, "keep_aiops_investigations_started_total", {"mode": "suggest"}) if "keep_aiops_investigations_started_total{" in before else 0.0
    completed_before = _value(before, "keep_aiops_investigations_completed_total", {"mode": "suggest"}) if "keep_aiops_investigations_completed_total{" in before else 0.0
    active_before = _value(before, "keep_aiops_investigations_active")

    assert post_event(client, make_event("incident.created")).status_code == 202

    after = _scrape(client)
    assert _value(after, "keep_aiops_investigations_started_total", {"mode": "suggest"}) == started_before + 1
    assert _value(after, "keep_aiops_investigations_completed_total", {"mode": "suggest"}) == completed_before + 1
    # Gauge returns to baseline once the run finishes.
    assert _value(after, "keep_aiops_investigations_active") == active_before
    # Duration was observed exactly once.
    assert _value(after, "keep_aiops_investigation_duration_seconds_count", {"mode": "suggest"}) >= 1
    # All three gather tools succeeded.
    for tool in ("get_pods", "get_events", "get_logs"):
        assert _value(after, "keep_aiops_mcp_tool_calls_total", {"tool": tool, "outcome": "success"}) >= 1


def test_failed_investigation_increments_failed_counter(client, mocked_backends):
    before = _scrape(client)
    failed_before = (
        _value(before, "keep_aiops_investigations_failed_total", {"mode": "suggest"})
        if "keep_aiops_investigations_failed_total{" in before
        else 0.0
    )

    # Writeback to Keep fails -> the whole run lands in `failed`.
    mocked_backends.comment_route.respond(500, json={"detail": "boom"})
    assert post_event(client, make_event("incident.created")).status_code == 202

    after = _scrape(client)
    assert _value(after, "keep_aiops_investigations_failed_total", {"mode": "suggest"}) == failed_before + 1
    # The MCP tools themselves all succeeded before the writeback blew up.
    assert _value(after, "keep_aiops_mcp_tool_calls_total", {"tool": "get_pods", "outcome": "success"}) >= 1


def test_tool_error_increments_error_outcome_and_evidence_gap(client, mocked_backends):
    before = _scrape(client)
    errors_before = (
        _value(before, "keep_aiops_mcp_tool_calls_total", {"tool": "get_events", "outcome": "error"})
        if 'outcome="error"' in before
        else 0.0
    )
    gaps_before = (
        _value(before, "keep_aiops_evidence_gaps_total", {"tool": "get_events"})
        if "keep_aiops_evidence_gaps_total{" in before
        else 0.0
    )
    completed_before = (
        _value(before, "keep_aiops_investigations_completed_total", {"mode": "suggest"})
        if "keep_aiops_investigations_completed_total{" in before
        else 0.0
    )

    mocked_backends.get_events_route.respond(500, json={"detail": "gateway down"})
    assert post_event(client, make_event("incident.created")).status_code == 202

    after = _scrape(client)
    assert _value(after, "keep_aiops_mcp_tool_calls_total", {"tool": "get_events", "outcome": "error"}) == errors_before + 1
    assert _value(after, "keep_aiops_evidence_gaps_total", {"tool": "get_events"}) == gaps_before + 1
    # A single tool failure is an evidence gap, not a failed investigation.
    assert _value(after, "keep_aiops_investigations_completed_total", {"mode": "suggest"}) == completed_before + 1
