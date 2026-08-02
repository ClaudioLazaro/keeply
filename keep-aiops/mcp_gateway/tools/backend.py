"""Shared base class for live-backend "unavailable" errors.

Every MCP tool that talks to a live backend (k8s, prometheus, datadog,
eks, rds, argocd, jira, slack, bitbucket, backstage) raises a subclass of
:class:`BackendUnavailable` when the upstream is unreachable or
unconfigured. The gateway maps that to ``503 + retry hint`` for callers
(orchestrator + curl smoke tests).
"""

from __future__ import annotations


class BackendUnavailable(RuntimeError):
    """Raised when a live MCP backend cannot serve a request.
    The gateway maps this to HTTP 503 with a retry hint."""

    retry_after_seconds: int = 5
