"""Integration state, derived from Keep's provider system.

Keep already owns integration credentials: 120+ providers with an install
UI, a secret manager, scope validation and rotation. The AI plane keeps no
second copy — this module only answers two questions Keep does not:

1. which Keep provider backs which MCP tool group
2. whether that tool group is running live or on stub demo data

`mode` is the one piece of genuinely AI-plane state, and even that is
derived: an integration goes live when the Keep provider backing it is
installed. Installing Datadog in Keep is what turns the Datadog specialist
into a real one — there is no second switch to forget.
"""

from __future__ import annotations

import logging
from typing import Any

from aiops_api.modules.integrations.catalog import INTEGRATIONS

logger = logging.getLogger(__name__)


def _effective_modes() -> dict[str, str]:
    """Modes the gateway is actually running, keyed by integration name.

    The gateway catalog resolves the effective mode per tool, so it is the
    operational truth — reporting anything else risks showing a live
    backend as stub, the exact misleading provenance this work removes.
    """
    import httpx

    from aiops_api.settings import get_settings

    gateway = get_settings().mcp_gateway_url.rstrip("/")
    tool_to_integration = {tool: spec.name for spec in INTEGRATIONS for tool in spec.tools}
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{gateway}/v1/mcp/tools")
            response.raise_for_status()
            catalog = response.json()
    except Exception:  # noqa: BLE001 — a dead gateway is a UI state
        logger.info("could not read gateway catalog for effective modes", exc_info=True)
        return {}

    modes: dict[str, str] = {}
    for tool in catalog:
        name = tool_to_integration.get(tool.get("name", ""))
        mode = tool.get("mode")
        if name and mode in ("stub", "live"):
            # Any live tool makes the integration live — they share a mode.
            if modes.get(name) != "live":
                modes[name] = mode
    return modes


def describe_all() -> list[dict[str, Any]]:
    """One row per integration: which Keep provider backs it, and its mode.

    Read-only by design. Configuring an integration means installing its
    provider in Keep; this page exists to show the consequence of that for
    the AI agents, not to offer a second place to type credentials.
    """
    from keep_client.providers import PROVIDER_TYPE_TO_INTEGRATION, list_installed

    effective = _effective_modes()
    installed = list_installed()
    by_integration: dict[str, Any] = {}
    for provider in installed:
        name = PROVIDER_TYPE_TO_INTEGRATION.get(provider.type)
        if name and name not in by_integration:
            by_integration[name] = provider

    out: list[dict[str, Any]] = []
    for spec in INTEGRATIONS:
        provider = by_integration.get(spec.name)
        out.append(
            {
                "name": spec.name,
                "label": spec.label,
                "mode": effective.get(spec.name, "stub"),
                "tools": list(spec.tools),
                "notes": spec.notes,
                # Which Keep provider supplies this integration, if installed.
                "provider": (
                    {
                        "id": provider.id,
                        "type": provider.type,
                        "display_name": provider.display_name,
                    }
                    if provider is not None
                    else None
                ),
                # Provider types Keep offers for this integration, so the
                # page can link straight to the right install flow.
                "provider_types": sorted(
                    ptype
                    for ptype, integration in PROVIDER_TYPE_TO_INTEGRATION.items()
                    if integration == spec.name
                ),
                "ambient_credentials": not spec.live_requires_config,
            }
        )
    return out


def resolved_for_gateway() -> dict[str, Any]:
    """Credentials the MCP gateway pulls, sourced from Keep providers.

    The gateway has no database and no Keep API key of its own; aiops-api
    resolves provider credentials here and hands over only what the tools
    need. An integration whose provider is not installed is absent, which
    leaves the gateway on its env default — always the stub direction.
    """
    from keep_client.providers import PROVIDER_TYPE_TO_INTEGRATION, list_installed

    # Field names the tools expect, per integration, mapped from whatever
    # the Keep provider calls them.
    FIELD_SOURCES: dict[str, dict[str, tuple[str, ...]]] = {
        "prometheus": {"url": ("url", "host_url", "prometheus_url")},
        "datadog": {
            "url": ("domain", "api_url", "url"),
            "api_key": ("api_key",),
            "app_key": ("app_key", "application_key"),
        },
        "argocd": {"url": ("host", "url"), "token": ("token", "api_token")},
        "jira": {
            "url": ("host", "url", "jira_host"),
            "token": ("api_token", "token", "personal_access_token"),
        },
        "slack": {"token": ("access_token", "token", "bot_token")},
    }

    resolved: dict[str, Any] = {}
    for provider in list_installed():
        name = PROVIDER_TYPE_TO_INTEGRATION.get(provider.type)
        if not name or name in resolved:
            continue
        values: dict[str, Any] = {}
        for field, candidates in FIELD_SOURCES.get(name, {}).items():
            value = provider.secret(*candidates)
            if value:
                values[field] = value
        # An installed provider is the operator saying "use the real thing".
        resolved[name] = {"mode": "live", "values": values}
    return {"integrations": resolved}
