"""Slack MCP tool server (M3, read-only).

``MCP_SLACK_MODE=stub`` (default) returns canned message search hits.

``MCP_SLACK_MODE=live`` uses the Slack ``search.messages`` API at
``MCP_SLACK_URL`` (e.g. ``https://slack.com/api``) with a bearer token
from ``MCP_SLACK_TOKEN``. Missing URL/token surfaces as
:class:`SlackBackendUnavailable`, mapped to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class SlackBackendUnavailable(BackendUnavailable):
    """Raised when the live Slack backend cannot serve a request."""


SLACK_SEARCH_MESSAGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "window_hours": {"type": "integer", "minimum": 1, "maximum": 720, "default": 24},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": ["query"],
    "additionalProperties": False,
}


_STUB_MESSAGES: list[dict[str, Any]] = [
    {"channel": "#oncall-payments", "user": "U01", "text": "seeing OOMKills on payment-api since 10:14 UTC", "ts": "1730000010.000100"},
    {"channel": "#incidents", "user": "U02", "text": "fyi, settlement job heap is pegged — anyone else?", "ts": "1730000042.000200"},
]


@register_tool(
    name="slack_search_messages",
    description="Search recent Slack messages by query (live mode requires MCP_SLACK_URL + token).",
    input_schema=SLACK_SEARCH_MESSAGES_SCHEMA,
    mode_setting="slack_mode",
)
def slack_search_messages(query: str, window_hours: int = 24, max_results: int = 10) -> dict[str, Any]:
    if integrations.mode("slack") == "live":
        settings = get_settings()
        if not getattr(settings, "slack_url", ""):
            raise SlackBackendUnavailable("MCP_SLACK_URL not configured; use MCP_SLACK_MODE=stub")
        headers: dict[str, str] = {}
        if getattr(settings, "slack_token", ""):
            headers["Authorization"] = f"Bearer {integrations.value('slack', 'token')}"
        try:
            resp = httpx.get(
                f"{integrations.value('slack', 'url').rstrip('/')}/search.messages",
                params={"query": query, "count": max_results},
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            return {"backend": "live", "messages": resp.json().get("messages", {}).get("matches", [])}
        except SlackBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SlackBackendUnavailable(f"slack API call failed: {exc}") from exc
    matches = [m for m in _STUB_MESSAGES if query.lower() in m["text"].lower()][:max_results]
    return {"backend": "stub", "messages": matches}
