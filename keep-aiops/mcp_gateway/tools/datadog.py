"""Datadog MCP tool server (M3, read-only).

``MCP_DATADOG_MODE=stub`` (default) returns canned demo data aligned with
the M0/M2 ``payment-api`` incident: a high error-rate series and a handful
of recent events.

``MCP_DATADOG_MODE=live`` queries the Datadog HTTP API at
``MCP_DATADOG_URL`` (e.g. ``https://api.datadoghq.com``) using
``MCP_DATADOG_API_KEY`` / ``MCP_DATADOG_APP_KEY`` headers. Missing URL or
unreachable backend surfaces as :class:`DatadogBackendUnavailable`, which
the gateway maps to 503 with a retry hint.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class DatadogBackendUnavailable(BackendUnavailable):
    """Raised when the live Datadog backend cannot serve a request."""


# --------------------------------------------------------------------------- #
# Input schemas
# --------------------------------------------------------------------------- #

DD_QUERY_METRICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "description": "Datadog metrics query string."},
        "window": {
            "type": "string",
            "default": "15m",
            "description": "Time window (e.g. 5m, 1h, 1d) for instant queries.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

DD_LIST_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "string",
            "default": "",
            "description": "Comma-separated tag filter (e.g. 'service:payment-api').",
        },
        "window": {
            "type": "string",
            "default": "1h",
            "description": "Look-back window (e.g. 30m, 1h).",
        },
    },
    "required": [],
    "additionalProperties": False,
}


_STUB_METRICS: list[dict[str, Any]] = [
    {
        "metric": "http.request.error_rate",
        "tags": ["service:payment-api", "env:prod"],
        "points": [[1_730_000_000, 0.42], [1_730_000_060, 0.45], [1_730_000_120, 0.43]],
    }
]

_STUB_EVENTS: list[dict[str, Any]] = [
    {
        "id": "evt-1",
        "title": "payment-api HTTP 5xx above 10%",
        "text": "Error rate spiked to 42% on payment-api at 10:14 UTC.",
        "tags": ["service:payment-api", "severity:critical"],
        "timestamp": 1_730_000_000,
    },
    {
        "id": "evt-2",
        "title": "OOMKilled payment-api-7d9f4b6c5-x2vkm",
        "text": "Container payment-api OOMKilled; heap usage 1Gi/1Gi.",
        "tags": ["service:payment-api", "k8s:oom"],
        "timestamp": 1_730_000_120,
    },
]


def _live_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not getattr(settings, "datadog_url", ""):
        raise DatadogBackendUnavailable(
            "MCP_DATADOG_URL not configured; set it or use MCP_DATADOG_MODE=stub"
        )
    headers: dict[str, str] = {}
    if getattr(settings, "datadog_api_key", ""):
        headers["DD-API-KEY"] = integrations.value("datadog", "api_key")
    if getattr(settings, "datadog_app_key", ""):
        headers["DD-APPLICATION-KEY"] = integrations.value("datadog", "app_key")
    try:
        resp = httpx.get(
            f"{integrations.value('datadog', 'url').rstrip('/')}/api/v1/{path}",
            params=params,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        return {"backend": "live", "data": resp.json()}
    except DatadogBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatadogBackendUnavailable(f"datadog API call failed: {exc}") from exc


@register_tool(
    name="dd_query_metrics",
    description="Query a Datadog metrics series (returns point list; live mode requires MCP_DATADOG_URL/API keys).",
    input_schema=DD_QUERY_METRICS_SCHEMA,
    mode_setting="datadog_mode",
)
def dd_query_metrics(query: str, window: str = "15m") -> dict[str, Any]:
    if integrations.mode("datadog") == "live":
        return _live_get("query", {"query": query, "window": window})
    return {"backend": "stub", "status": "success", "series": _STUB_METRICS}


@register_tool(
    name="dd_list_events",
    description="List recent Datadog events filtered by tags (live mode requires API keys).",
    input_schema=DD_LIST_EVENTS_SCHEMA,
    mode_setting="datadog_mode",
)
def dd_list_events(tags: str = "", window: str = "1h") -> dict[str, Any]:
    if integrations.mode("datadog") == "live":
        return _live_get("events", {"tags": tags, "start": int(0) - 3600, "end": int(0)})
    filtered = _STUB_EVENTS
    if tags:
        wanted = {t.strip() for t in tags.split(",") if t.strip()}
        filtered = [e for e in _STUB_EVENTS if wanted.issubset({t for t in e["tags"]})]
    return {"backend": "stub", "status": "success", "events": filtered}
