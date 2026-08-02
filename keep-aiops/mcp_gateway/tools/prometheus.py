"""Prometheus MCP tool server (M0/M2).

``MCP_PROMETHEUS_MODE=stub`` (default) returns realistic canned payloads for
the M0 demo scenario: two firing alerts for ``payment-api`` and an HTTP 5xx
error-rate series that ramps up in the 30 minutes before the incident time
(2026-07-29T10:15:00Z), matching the k8s CrashLoopBackOff/OOMKilled stub data.

``MCP_PROMETHEUS_MODE=live`` queries a real Prometheus server at
``MCP_PROMETHEUS_URL`` (e.g. ``http://prometheus:9090``) via the HTTP API
(``/api/v1/query``, ``/api/v1/query_range``, ``/api/v1/alerts``). Missing URL
or unreachable backend surfaces as :class:`PrometheusBackendUnavailable`,
which the gateway maps to 503 with a retry hint.
"""

from datetime import datetime, timezone
from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class PrometheusBackendUnavailable(BackendUnavailable):
    """Raised when the live Prometheus backend cannot serve a request."""


# Incident anchor time, aligned with the k8s stub payloads (OOMKilled at ~10:14).
_INCIDENT_END = "2026-07-29T10:15:00Z"
_INCIDENT_START = "2026-07-29T09:45:00Z"  # 30 min ramp before the incident


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

_QUERY_PROP: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": "PromQL expression",
}
_START_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Range start, RFC3339 (e.g. 2026-07-29T09:45:00Z); defaults to 30m before the stub incident",
}
_END_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Range end, RFC3339; must be after start; defaults to the stub incident time",
}
_STEP_PROP: dict[str, Any] = {
    "type": "integer",
    "minimum": 1,
    "default": 60,
    "description": "Query resolution step in seconds",
}

PROM_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": _QUERY_PROP},
    "required": ["query"],
    "additionalProperties": False,
}

PROM_QUERY_RANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": _QUERY_PROP,
        "start": _START_PROP,
        "end": _END_PROP,
        "step": _STEP_PROP,
    },
    "required": ["query"],
    "additionalProperties": False,
}

PROM_ALERTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Stub payloads (payment-api elevated 5xx error rate before/during incident)
# ---------------------------------------------------------------------------

_STUB_ALERTS: list[dict[str, Any]] = [
    {
        "labels": {
            "alertname": "HighErrorRate",
            "service": "payment-api",
            "namespace": "payments",
            "severity": "critical",
        },
        "annotations": {
            "summary": "payment-api HTTP 5xx error rate above 10% for 10m",
            "description": "rate(http_requests_total{service=\"payment-api\",status=~\"5..\"}[5m]) is ~0.43 req/s (>40% of traffic).",
        },
        "state": "firing",
        "activeAt": "2026-07-29T09:52:07Z",
        "value": "4.31e-01",
    },
    {
        "labels": {
            "alertname": "PodCrashLooping",
            "pod": "payment-api-7d9f4b6c5-x2vkm",
            "namespace": "payments",
            "severity": "critical",
        },
        "annotations": {
            "summary": "Pod payment-api-7d9f4b6c5-x2vkm is crash looping",
            "description": "kube_pod_container_status_restarts_total increased by 14 in the last hour (last state OOMKilled).",
        },
        "state": "firing",
        "activeAt": "2026-07-29T09:48:31Z",
        "value": "14",
    },
]

# Steady-state vs incident error rates (req/s) per service.
_STUB_ERROR_RATES: list[tuple[dict[str, str], float]] = [
    ({"service": "payment-api", "namespace": "payments", "status": "500"}, 0.312),
    ({"service": "payment-api", "namespace": "payments", "status": "502"}, 0.119),
    ({"service": "api-gateway", "namespace": "payments", "status": "500"}, 0.002),
]


def _parse_rfc3339(value: str) -> float:
    """Parse an RFC3339 timestamp to epoch seconds; raises ValueError when invalid."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp '{value}' must include a timezone (RFC3339)")
    return parsed.timestamp()


def _resolve_range(start: str | None, end: str | None) -> tuple[float, float]:
    start_ts = _parse_rfc3339(start) if start else _parse_rfc3339(_INCIDENT_START)
    end_ts = _parse_rfc3339(end) if end else _parse_rfc3339(_INCIDENT_END)
    if end_ts <= start_ts:
        raise ValueError("argument 'end' must be after 'start'")
    return start_ts, end_ts


def _stub_query(query: str) -> dict[str, Any]:
    payment_only = "payment-api" in query
    series = _STUB_ERROR_RATES[:2] if payment_only else _STUB_ERROR_RATES
    now = _parse_rfc3339(_INCIDENT_END)
    return {
        "backend": "stub",
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": metric, "value": [now, f"{value:.3f}"]} for metric, value in series
            ],
        },
    }


def _stub_query_range(query: str, start_ts: float, end_ts: float, step: int) -> dict[str, Any]:
    # Monotonic ramp from ~0.012 req/s to the incident-level ~0.43 req/s over
    # the requested window (default window covers the 30 min before incident).
    n_points = max(2, int((end_ts - start_ts) // step) + 1)
    lo, hi = 0.012, 0.431
    values = [
        [
            start_ts + i * (end_ts - start_ts) / (n_points - 1),
            f"{lo + (hi - lo) * (i / (n_points - 1)) ** 1.5:.4f}",
        ]
        for i in range(n_points)
    ]
    metric = {"service": "payment-api", "namespace": "payments", "status": "500"}
    return {
        "backend": "stub",
        "status": "success",
        "data": {"resultType": "matrix", "result": [{"metric": metric, "values": values}]},
    }


# ---------------------------------------------------------------------------
# Live backend
# ---------------------------------------------------------------------------


def _live_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = integrations.value("prometheus", "url")
    if not url:
        raise PrometheusBackendUnavailable(
            "MCP_PROMETHEUS_URL not configured; set it or use MCP_PROMETHEUS_MODE=stub"
        )
    try:
        resp = httpx.get(f"{url.rstrip('/')}/api/v1/{path}", params=params, timeout=10.0)
        resp.raise_for_status()
        payload = resp.json()
    except PrometheusBackendUnavailable:
        raise
    except Exception as exc:
        raise PrometheusBackendUnavailable(f"prometheus API call failed: {exc}") from exc
    data = payload.get("data", payload)
    return {"backend": "live", "status": payload.get("status", "success"), "data": data}


# ---------------------------------------------------------------------------
# Tool registrations (all read-class)
# ---------------------------------------------------------------------------


@register_tool(
    name="prom_query",
    description="Run an instant PromQL query and return the current vector (e.g. current HTTP 5xx rate per service).",
    input_schema=PROM_QUERY_SCHEMA,
    mode_setting="prometheus_mode",
)
def prom_query(query: str) -> dict[str, Any]:
    if integrations.mode("prometheus") == "live":
        return _live_get("query", {"query": query})
    return _stub_query(query)


@register_tool(
    name="prom_query_range",
    description="Run a range PromQL query and return a matrix of values over time (start/end RFC3339, step seconds).",
    input_schema=PROM_QUERY_RANGE_SCHEMA,
    mode_setting="prometheus_mode",
)
def prom_query_range(
    query: str,
    start: str | None = None,
    end: str | None = None,
    step: int = 60,
) -> dict[str, Any]:
    start_ts, end_ts = _resolve_range(start, end)
    if integrations.mode("prometheus") == "live":
        return _live_get(
            "query_range",
            {"query": query, "start": start_ts, "end": end_ts, "step": step},
        )
    return _stub_query_range(query, start_ts, end_ts, step)


@register_tool(
    name="prom_alerts",
    description="List currently firing Prometheus alerts (alertname, labels, annotations, state).",
    input_schema=PROM_ALERTS_SCHEMA,
    mode_setting="prometheus_mode",
)
def prom_alerts() -> dict[str, Any]:
    if integrations.mode("prometheus") == "live":
        return _live_get("alerts", {})
    return {"backend": "stub", "status": "success", "data": {"alerts": _STUB_ALERTS}}
