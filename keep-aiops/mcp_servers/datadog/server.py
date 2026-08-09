"""Datadog MCP server — the anchor of an investigation.

Second MCP server, and deliberately the second *shape*. Kubernetes scopes by
cluster and namespace and answers "what state is this workload in". Datadog
scopes by service, environment and time window, and answers a question
Kubernetes cannot: **which hop failed**. A pod can be Running while every call
through it times out.

That difference is why this one was built second rather than another
resource-listing API — it is the sample that proves whether ``Scope``
generalises or whether it was namespace with a generic name.

Same two contracts as the Kubernetes server:

* ``backend`` and ``target`` have no default, so MCP requires them and a result
  cannot omit where it came from.
* the target is explicit — there is no ambient account. Call ``list_targets``.

Credentials come from the Datadog provider installed in Keep (ADR-0008). With
none installed the server runs in stub mode and says so, rather than failing
to start.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from mcp_servers.datadog import stubs, targets
from mcp_servers.datadog.models import (
    EventsResult,
    LogsResult,
    MetricsResult,
    MonitorsResult,
    TargetInfo,
    TargetsResult,
    TraceResult,
)
from mcp_servers.redaction import redact, redact_lines

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="keeply-datadog",
    title="Keeply Datadog",
    version="1.0.0",
    instructions=(
        "Datadog monitors, metrics, traces and logs for incident investigation. "
        "Every tool requires an explicit `target` — call list_targets first. "
        "Start from the monitor that fired, then get_trace to find the failing "
        "hop, then search_logs on that trace_id: each step narrows the next. "
        "Every result reports `backend` (live/stub/gap); treat anything other "
        "than live as unverified."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def _gap(model, target: str, error: str, **fields):
    logger.warning("evidence gap on datadog target %s: %s", target, error)
    return model(backend="gap", target=target, error=error, **fields)


def _transport_security() -> TransportSecuritySettings:
    """Hosts allowed to reach this server (DNS-rebinding protection).

    Same reasoning as the Kubernetes server: the hostname a federating gateway
    dials is not the one you test with locally, and an undeclared host gets a
    bare 421 with nothing to debug from.
    """
    raw = os.environ.get("MCP_DATADOG_ALLOWED_HOSTS", "").strip()
    if not raw:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts],
    )


def _api_get(spec: targets.TargetSpec, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """One authenticated Datadog API call. Raises TargetUnavailable."""
    import httpx

    if not spec.api_key or not spec.app_key:
        raise targets.TargetUnavailable(
            f"target {spec.name!r} has no API/application key; install a Datadog "
            "provider in Keep or set its mode to stub"
        )
    try:
        response = httpx.get(
            f"https://api.{spec.site}{path}",
            params=params,
            headers={"DD-API-KEY": spec.api_key, "DD-APPLICATION-KEY": spec.app_key},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 — every transport failure is a gap
        raise targets.TargetUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc


@mcp.tool(annotations=READ_ONLY, title="List targets",
          description="Datadog accounts this server can query, and whether each serves live or stub data.")
def list_targets() -> TargetsResult:
    return TargetsResult(
        targets=[
            TargetInfo(name=s.name, mode=s.mode, site=s.site, description=s.description)
            for s in sorted(targets.registry().values(), key=lambda s: s.name)
        ]
    )


@mcp.tool(annotations=READ_ONLY, title="Get monitors",
          description=(
              "Monitors and their current state, optionally filtered by tag "
              "(e.g. 'service:payment-api'). This is where an investigation "
              "starts: the monitor that fired names the service, environment "
              "and window everything else is scoped to."
          ))
def get_monitors(target: str, tag: str | None = None) -> MonitorsResult:
    try:
        spec = targets.get(target)
    except targets.UnknownTarget as exc:
        return _gap(MonitorsResult, target, str(exc))

    if spec.mode == "stub":
        monitors = [m for m in stubs.STUB_MONITORS if not tag or tag in m.tags]
        return MonitorsResult(backend="stub", target=target, monitors=monitors)

    try:
        raw = _api_get(spec, "/api/v1/monitor", {"tags": tag} if tag else {})
    except targets.TargetUnavailable as exc:
        return _gap(MonitorsResult, target, str(exc))

    from mcp_servers.datadog.models import MonitorState

    return MonitorsResult(
        backend="live",
        target=target,
        monitors=[
            MonitorState(
                id=m.get("id"),
                name=m.get("name", ""),
                status=m.get("overall_state"),
                message=redact(str(m.get("message") or "")).text,
                tags=list(m.get("tags") or []),
                last_triggered=str(m.get("overall_state_modified") or "") or None,
            )
            for m in (raw if isinstance(raw, list) else [])
        ],
    )


@mcp.tool(annotations=READ_ONLY, title="Query metrics",
          description="Timeseries for a metric query over a window, e.g. 'avg:trace.http.request.errors{service:payment-api}'.")
def query_metrics(target: str, query: str, from_epoch: int = 0, to_epoch: int = 0) -> MetricsResult:
    try:
        spec = targets.get(target)
    except targets.UnknownTarget as exc:
        return _gap(MetricsResult, target, str(exc), query=query)

    if spec.mode == "stub":
        return MetricsResult(backend="stub", target=target, query=query, series=stubs.STUB_SERIES)

    try:
        raw = _api_get(spec, "/api/v1/query", {"query": query, "from": from_epoch, "to": to_epoch})
    except targets.TargetUnavailable as exc:
        return _gap(MetricsResult, target, str(exc), query=query)

    from mcp_servers.datadog.models import MetricPoint, MetricSeries

    series = []
    for item in raw.get("series") or []:
        series.append(
            MetricSeries(
                metric=item.get("metric", query),
                scope=item.get("scope"),
                points=[
                    MetricPoint(timestamp=str(p[0]), value=float(p[1]))
                    for p in (item.get("pointlist") or [])
                    if isinstance(p, (list, tuple)) and len(p) == 2 and p[1] is not None
                ],
            )
        )
    return MetricsResult(backend="live", target=target, query=query, series=series)


@mcp.tool(annotations=READ_ONLY, title="List events",
          description="Deployments, alerts and other events in a window — how an investigation learns whether something changed.")
def list_events(target: str, tag: str | None = None) -> EventsResult:
    try:
        spec = targets.get(target)
    except targets.UnknownTarget as exc:
        return _gap(EventsResult, target, str(exc))

    if spec.mode == "stub":
        events = [e for e in stubs.STUB_EVENTS if not tag or tag in e.tags]
        return EventsResult(backend="stub", target=target, events=events)

    try:
        raw = _api_get(spec, "/api/v1/events", {"tags": tag} if tag else {})
    except targets.TargetUnavailable as exc:
        return _gap(EventsResult, target, str(exc))

    from mcp_servers.datadog.models import DatadogEvent

    return EventsResult(
        backend="live",
        target=target,
        events=[
            DatadogEvent(
                title=e.get("title", ""),
                text=redact(str(e.get("text") or "")).text,
                tags=list(e.get("tags") or []),
                timestamp=str(e.get("date_happened") or "") or None,
                source=e.get("source_type_name"),
            )
            for e in (raw.get("events") or [])
        ],
    )


@mcp.tool(annotations=READ_ONLY, title="Get trace",
          description=(
              "The spans of one distributed trace, and which hop failed. This "
              "is what Kubernetes cannot answer: a pod is Running while every "
              "call through it times out. Use `failing_service` to decide "
              "where to read logs next."
          ))
def get_trace(target: str, trace_id: str) -> TraceResult:
    try:
        spec = targets.get(target)
    except targets.UnknownTarget as exc:
        return _gap(TraceResult, target, str(exc), trace_id=trace_id)

    if spec.mode == "stub":
        spans = stubs.STUB_SPANS
        failing = next((s.service for s in spans if s.error), None)
        return TraceResult(
            backend="stub", target=target, trace_id=stubs.STUB_TRACE_ID,
            spans=spans, failing_service=failing,
        )

    try:
        raw = _api_get(spec, f"/api/v1/trace/{trace_id}", {})
    except targets.TargetUnavailable as exc:
        return _gap(TraceResult, target, str(exc), trace_id=trace_id)

    from mcp_servers.datadog.models import SpanSummary

    spans = [
        SpanSummary(
            service=s.get("service", ""),
            operation=s.get("name"),
            duration_ms=(float(s["duration"]) / 1e6) if s.get("duration") else None,
            error=bool(s.get("error")),
            status_code=str((s.get("meta") or {}).get("http.status_code") or "") or None,
        )
        for s in (raw.get("spans") or [])
    ]
    return TraceResult(
        backend="live", target=target, trace_id=trace_id, spans=spans,
        failing_service=next((s.service for s in spans if s.error), None),
    )


@mcp.tool(annotations=READ_ONLY, title="Search logs",
          description=(
              "Logs matching a query. Prefer scoping by trace_id: it returns "
              "the lines belonging to the call that actually failed, rather "
              "than the lines of whichever pod looked worst."
          ))
def search_logs(target: str, query: str, limit: int = 50) -> LogsResult:
    try:
        spec = targets.get(target)
    except targets.UnknownTarget as exc:
        return _gap(LogsResult, target, str(exc), query=query)

    if spec.mode == "stub":
        return LogsResult(backend="stub", target=target, query=query, lines=stubs.STUB_LOGS[:limit])

    try:
        raw = _api_get(spec, "/api/v2/logs/events", {"filter[query]": query, "page[limit]": limit})
    except targets.TargetUnavailable as exc:
        return _gap(LogsResult, target, str(exc), query=query)

    from mcp_servers.datadog.models import LogLine

    messages = [
        str(((item.get("attributes") or {}).get("message")) or "")
        for item in (raw.get("data") or [])
    ]
    # Redacted before leaving the process: indexed logs carry credentials as
    # readily as container output, and from here this becomes stored evidence
    # and prompt text.
    cleaned, removed = redact_lines(messages)
    if removed:
        logger.info("redacted secrets from datadog logs", extra={"target": target, "count": len(removed)})

    lines = []
    for item, message in zip(raw.get("data") or [], cleaned):
        attrs = item.get("attributes") or {}
        lines.append(
            LogLine(
                timestamp=str(attrs.get("timestamp") or "") or None,
                service=attrs.get("service"),
                message=message,
                trace_id=str((attrs.get("attributes") or {}).get("trace_id") or "") or None,
            )
        )
    return LogsResult(backend="live", target=target, query=query, lines=lines)


app = mcp.streamable_http_app(transport_security=_transport_security())


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("MCP_DATADOG_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_DATADOG_PORT", "8766")),
    )


if __name__ == "__main__":
    main()
