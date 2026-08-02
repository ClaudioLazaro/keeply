"""Bitbucket MCP tool server (M3, read-only).

``MCP_BITBUCKET_MODE=stub`` (default) returns canned commit and PR data
for the ``payments/payment-api`` repo.

``MCP_BITBUCKET_MODE=live`` uses the Bitbucket REST API at
``MCP_BITBUCKET_URL`` (e.g. ``https://api.bitbucket.org/2.0``) with basic
auth from ``MCP_BITBUCKET_USER`` / ``MCP_BITBUCKET_TOKEN``. Missing
config surfaces as :class:`BitbucketBackendUnavailable`, mapped to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class BitbucketBackendUnavailable(BackendUnavailable):
    """Raised when the live Bitbucket backend cannot serve a request."""


BB_LIST_RECENT_COMMITS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "minLength": 1, "description": "Workspace/repo slug, e.g. 'payments/payment-api'."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": ["repo"],
    "additionalProperties": False,
}

BB_LIST_OPEN_PULL_REQUESTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "minLength": 1},
    },
    "required": ["repo"],
    "additionalProperties": False,
}


_STUB_COMMITS: dict[str, list[dict[str, Any]]] = {
    "payments/payment-api": [
        {"hash": "abc1234", "message": "Raise heap to 2Gi", "author": "alice", "date": "2026-07-28T15:14:00Z"},
        {"hash": "def5678", "message": "Settlement batch: avoid loading 24h window", "author": "bob", "date": "2026-07-27T11:02:00Z"},
    ]
}

_STUB_PRS: dict[str, list[dict[str, Any]]] = {
    "payments/payment-api": [
        {"id": 42, "title": "Tune GC for settlement batch", "state": "OPEN", "author": "carol"},
    ]
}


@register_tool(
    name="bb_list_recent_commits",
    description="List recent commits for a Bitbucket repo (live mode requires API credentials).",
    input_schema=BB_LIST_RECENT_COMMITS_SCHEMA,
    mode_setting="bitbucket_mode",
)
def bb_list_recent_commits(repo: str, max_results: int = 10) -> dict[str, Any]:
    if integrations.mode("bitbucket") == "live":
        settings = get_settings()
        if not getattr(settings, "bitbucket_url", ""):
            raise BitbucketBackendUnavailable("MCP_BITBUCKET_URL not configured; use MCP_BITBUCKET_MODE=stub")
        auth = None
        if getattr(settings, "bitbucket_user", "") and getattr(settings, "bitbucket_token", ""):
            auth = (integrations.value("bitbucket", "user"), integrations.value("bitbucket", "token"))
        try:
            resp = httpx.get(
                f"{integrations.value('bitbucket', 'url').rstrip('/')}/repositories/{repo}/commits",
                params={"pagelen": max_results},
                auth=auth,
                timeout=10.0,
            )
            resp.raise_for_status()
            return {"backend": "live", "commits": resp.json().get("values", [])}
        except BitbucketBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BitbucketBackendUnavailable(f"bitbucket API call failed: {exc}") from exc
    commits = _STUB_COMMITS.get(repo, [])[:max_results]
    return {"backend": "stub", "commits": commits}


@register_tool(
    name="bb_list_open_pull_requests",
    description="List open pull requests for a Bitbucket repo (live mode requires API credentials).",
    input_schema=BB_LIST_OPEN_PULL_REQUESTS_SCHEMA,
    mode_setting="bitbucket_mode",
)
def bb_list_open_pull_requests(repo: str) -> dict[str, Any]:
    if integrations.mode("bitbucket") == "live":
        settings = get_settings()
        if not getattr(settings, "bitbucket_url", ""):
            raise BitbucketBackendUnavailable("MCP_BITBUCKET_URL not configured; use MCP_BITBUCKET_MODE=stub")
        auth = None
        if getattr(settings, "bitbucket_user", "") and getattr(settings, "bitbucket_token", ""):
            auth = (integrations.value("bitbucket", "user"), integrations.value("bitbucket", "token"))
        try:
            resp = httpx.get(
                f"{integrations.value('bitbucket', 'url').rstrip('/')}/repositories/{repo}/pullrequests",
                params={"state": "OPEN"},
                auth=auth,
                timeout=10.0,
            )
            resp.raise_for_status()
            return {"backend": "live", "pull_requests": resp.json().get("values", [])}
        except BitbucketBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BitbucketBackendUnavailable(f"bitbucket API call failed: {exc}") from exc
    prs = _STUB_PRS.get(repo, [])
    return {"backend": "stub", "pull_requests": prs}
