"""ArgoCD MCP tool server (M3, read-only).

``MCP_ARGOCD_MODE=stub`` (default) returns canned Applications / drift data
aligned with the ``payment-api`` service.

``MCP_ARGOCD_MODE=live`` queries an ArgoCD server at
``MCP_ARGOCD_URL`` (e.g. ``https://argocd.example.com``) using a bearer
token from ``MCP_ARGOCD_TOKEN``. Missing URL or token surfaces as
:class:`ArgoCdBackendUnavailable`, mapped to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class ArgoCdBackendUnavailable(BackendUnavailable):
    """Raised when the live ArgoCD backend cannot serve a request."""


ARGOCD_LIST_APPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

ARGOCD_GET_APP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1}},
    "required": ["name"],
    "additionalProperties": False,
}


_STUB_APPS: list[dict[str, Any]] = [
    {
        "metadata": {"name": "payment-api"},
        "spec": {"source": {"repoURL": "git@bitbucket.org:payments/payment-api.git"}, "destination": {"server": "https://kubernetes.default.svc"}},
        "status": {"health": {"status": "Degraded"}, "sync": {"status": "Synced"}},
    },
    {
        "metadata": {"name": "api-gateway"},
        "spec": {"source": {"repoURL": "git@bitbucket.org:payments/api-gateway.git"}, "destination": {"server": "https://kubernetes.default.svc"}},
        "status": {"health": {"status": "Healthy"}, "sync": {"status": "Synced"}},
    },
]


def _live_get(path: str) -> dict[str, Any]:
    settings = get_settings()
    if not getattr(settings, "argocd_url", ""):
        raise ArgoCdBackendUnavailable(
            "MCP_ARGOCD_URL not configured; set it or use MCP_ARGOCD_MODE=stub"
        )
    headers: dict[str, str] = {}
    if getattr(settings, "argocd_token", ""):
        headers["Authorization"] = f"Bearer {integrations.value('argocd', 'token')}"
    try:
        resp = httpx.get(
            f"{integrations.value('argocd', 'url').rstrip('/')}/api/v1/{path}",
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        return {"backend": "live", "data": resp.json()}
    except ArgoCdBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArgoCdBackendUnavailable(f"argo API call failed: {exc}") from exc


@register_tool(
    name="argocd_list_apps",
    description="List ArgoCD Applications and their health/sync status.",
    input_schema=ARGOCD_LIST_APPS_SCHEMA,
    mode_setting="argocd_mode",
)
def argocd_list_apps() -> dict[str, Any]:
    if integrations.mode("argocd") == "live":
        return _live_get("applications")
    return {"backend": "stub", "applications": _STUB_APPS}


@register_tool(
    name="argocd_get_app",
    description="Fetch one ArgoCD Application by name (status, source, destination, history).",
    input_schema=ARGOCD_GET_APP_SCHEMA,
    mode_setting="argocd_mode",
)
def argocd_get_app(name: str) -> dict[str, Any]:
    if integrations.mode("argocd") == "live":
        return _live_get(f"applications/{name}")
    for app in _STUB_APPS:
        if app["metadata"]["name"] == name:
            return {"backend": "stub", "application": app}
    return {"backend": "stub", "error": "not found", "name": name}
