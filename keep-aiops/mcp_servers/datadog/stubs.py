"""Canned Datadog payloads for the demo scenario.

The same payment-api incident the Kubernetes stubs tell, seen from the
observability side: a monitor firing, latency climbing, a trace whose third
hop errors, and the log lines belonging to that trace.

Carried across from ``mcp_gateway/tools/datadog.py`` so the demo keeps telling
one coherent story. Every one of these travels inside a result whose
``backend`` says ``stub``.
"""

from mcp_servers.datadog.models import (
    DatadogEvent,
    LogLine,
    MetricPoint,
    MetricSeries,
    MonitorState,
    SpanSummary,
)

STUB_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"

STUB_MONITORS = [
    MonitorState(
        id=8814,
        name="[payment-api] elevated 5xx rate",
        status="Alert",
        message="error rate 12.4% over 5m (threshold 2%)",
        tags=["service:payment-api", "env:prod", "team:payments"],
        last_triggered="2026-07-29T10:12:03Z",
    ),
    MonitorState(
        id=8815,
        name="[payment-api] p99 latency",
        status="Warn",
        message="p99 3.2s over 10m (threshold 2s)",
        tags=["service:payment-api", "env:prod"],
        last_triggered="2026-07-29T10:09:41Z",
    ),
]

STUB_SERIES = [
    MetricSeries(
        metric="trace.http.request.errors",
        scope="service:payment-api,env:prod",
        points=[
            MetricPoint(timestamp="2026-07-29T10:05:00Z", value=0.4),
            MetricPoint(timestamp="2026-07-29T10:10:00Z", value=6.1),
            MetricPoint(timestamp="2026-07-29T10:15:00Z", value=12.4),
        ],
    )
]

STUB_EVENTS = [
    DatadogEvent(
        title="Deployment payment-api v2.14.0",
        text="Rolled out by argocd at 10:04Z",
        tags=["service:payment-api", "env:prod", "source:argocd"],
        timestamp="2026-07-29T10:04:12Z",
        source="argocd",
    )
]

# The third hop errors. That is the whole value of a trace for RCA: Kubernetes
# reports payment-api as Running, and it is — the failure is downstream.
STUB_SPANS = [
    SpanSummary(service="api-gateway", operation="POST /checkout", duration_ms=3180.0, error=False, status_code="200"),
    SpanSummary(service="payment-api", operation="POST /charge", duration_ms=3120.0, error=False, status_code="200"),
    SpanSummary(
        service="identity-svc",
        operation="GET /verify",
        duration_ms=3001.0,
        error=True,
        status_code="504",
    ),
]

STUB_LOGS = [
    LogLine(
        timestamp="2026-07-29T10:14:51Z",
        service="identity-svc",
        message="upstream timeout after 3000ms calling token-store",
        trace_id=STUB_TRACE_ID,
    ),
    LogLine(
        timestamp="2026-07-29T10:14:52Z",
        service="payment-api",
        message="charge failed: identity verification unavailable (504)",
        trace_id=STUB_TRACE_ID,
    ),
]
