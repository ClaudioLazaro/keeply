"""Jira MCP tool server (M3, read-only).

``MCP_JIRA_MODE=stub`` (default) returns canned issues mentioning
``payment-api``.

``MCP_JIRA_MODE=live`` uses the Jira REST API at ``MCP_JIRA_URL`` with a
bearer token from ``MCP_JIRA_TOKEN``. Missing URL/token surfaces as
:class:`JiraBackendUnavailable`, mapped to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class JiraBackendUnavailable(BackendUnavailable):
    """Raised when the live Jira backend cannot serve a request."""


JIRA_SEARCH_ISSUES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "jql": {"type": "string", "minLength": 1, "description": "JQL query string."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": ["jql"],
    "additionalProperties": False,
}


_STUB_ISSUES: list[dict[str, Any]] = [
    {"key": "PAY-1024", "summary": "payment-api heap usage spikes on settlement load", "status": "Open", "priority": "High"},
    {"key": "PAY-1019", "summary": "Add JVM heap alerts for payment-api", "status": "In Progress", "priority": "Medium"},
]


@register_tool(
    name="jira_search_issues",
    description="Search Jira issues by JQL (live mode requires MCP_JIRA_URL + token).",
    input_schema=JIRA_SEARCH_ISSUES_SCHEMA,
    mode_setting="jira_mode",
)
def jira_search_issues(jql: str, max_results: int = 10) -> dict[str, Any]:
    if integrations.mode("jira") == "live":
        settings = get_settings()
        if not getattr(settings, "jira_url", ""):
            raise JiraBackendUnavailable("MCP_JIRA_URL not configured; use MCP_JIRA_MODE=stub")
        headers: dict[str, str] = {"Accept": "application/json"}
        if getattr(settings, "jira_token", ""):
            headers["Authorization"] = f"Bearer {integrations.value('jira', 'token')}"
        try:
            resp = httpx.get(
                f"{integrations.value('jira', 'url').rstrip('/')}/rest/api/3/search",
                params={"jql": jql, "maxResults": max_results},
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            return {"backend": "live", "issues": resp.json().get("issues", [])}
        except JiraBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise JiraBackendUnavailable(f"jira API call failed: {exc}") from exc
    needle = "payment-api"
    matches = [i for i in _STUB_ISSUES if needle in i["summary"]][:max_results]
    return {"backend": "stub", "issues": matches}
