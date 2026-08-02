"""Backstage MCP tool server (M3, read-only).

``MCP_BACKSTAGE_MODE=stub`` (default) returns a canned Component entity for
``payment-api`` with owner, on-call, and links.

``MCP_BACKSTAGE_MODE=live`` uses the Backstage Catalog API at
``MCP_BACKSTAGE_URL`` (e.g. ``https://backstage.example.com/api/catalog``).
Missing URL surfaces as :class:`BackstageBackendUnavailable`, mapped to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import httpx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class BackstageBackendUnavailable(BackendUnavailable):
    """Raised when the live Backstage backend cannot serve a request."""


BACKSTAGE_GET_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "minLength": 1, "description": "Entity kind (e.g. Component, API, Resource)."},
        "name": {"type": "string", "minLength": 1, "description": "Entity name (e.g. 'payment-api')."},
        "namespace": {"type": "string", "default": "default"},
    },
    "required": ["kind", "name"],
    "additionalProperties": False,
}


_STUB_ENTITIES: dict[tuple[str, str], dict[str, Any]] = {
    ("Component", "payment-api"): {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {
            "name": "payment-api",
            "namespace": "default",
            "owner": "team-payments",
        },
        "spec": {
            "type": "service",
            "lifecycle": "production",
            "owner": "team-payments",
            "system": "payments",
        },
        "relations": [
            {"type": "ownedBy", "targetRef": "Group/team-payments"},
            {"type": "dependsOn", "targetRef": "Component/settlements-db"},
        ],
        "annotations": {
            "backstage.io/oncall": "sre-oncall@example.com",
            "github.com/project-slug": "payments/payment-api",
        },
    }
}


@register_tool(
    name="backstage_get_entity",
    description="Read a Backstage catalog entity (Component / API / Resource) by kind+name (live mode requires MCP_BACKSTAGE_URL).",
    input_schema=BACKSTAGE_GET_ENTITY_SCHEMA,
    mode_setting="backstage_mode",
)
def backstage_get_entity(kind: str, name: str, namespace: str = "default") -> dict[str, Any]:
    if integrations.mode("backstage") == "live":
        settings = get_settings()
        if not getattr(settings, "backstage_url", ""):
            raise BackstageBackendUnavailable("MCP_BACKSTAGE_URL not configured; use MCP_BACKSTAGE_MODE=stub")
        try:
            resp = httpx.get(
                f"{integrations.value('backstage', 'url').rstrip('/')}/entities/by-name/{kind}/{namespace}/{name}",
                timeout=10.0,
            )
            resp.raise_for_status()
            return {"backend": "live", "entity": resp.json()}
        except BackstageBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackstageBackendUnavailable(f"backstage API call failed: {exc}") from exc
    entity = _STUB_ENTITIES.get((kind, name))
    if entity is None:
        return {"backend": "stub", "error": "not found", "kind": kind, "name": name}
    return {"backend": "stub", "entity": entity}
