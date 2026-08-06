"""Derive what an investigation is about from its context pack.

Gathering used to run before the context pack was built, so specialists went
looking for evidence before anything knew what the incident was. Reversing
that order is what makes this module possible at all.

Namespace derivation is a documented guess, not a lookup. Keep records
*services*; Kubernetes has *namespaces*; nothing maps one to the other. The
common convention is that they share a name, so a service is treated as a
namespace candidate, and ``AIOPS_SERVICE_NAMESPACE_MAP`` overrides that
wherever the convention does not hold.

Guessing is acceptable here only because a wrong guess is safe: querying a
namespace that does not exist returns nothing, which is a visible empty result
rather than someone else's telemetry. The dangerous direction — no scope at
all — is the one the caller is told about, via ``Scope.derived``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from aiops_api.modules.specialists.base import Scope

logger = logging.getLogger(__name__)

# A handful is enough to cover an incident's blast radius, and each one costs
# a tool call against the budget.
MAX_NAMESPACES = 3


def _service_namespace_map() -> dict[str, str]:
    raw = os.environ.get("AIOPS_SERVICE_NAMESPACE_MAP", "").strip()
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("AIOPS_SERVICE_NAMESPACE_MAP is not valid JSON; ignoring", exc_info=True)
        return {}
    return {str(k).strip().lower(): str(v).strip() for k, v in mapping.items() if k and v}


def _services_from(pack: dict[str, Any] | None) -> list[str]:
    """Affected services, incident first, then whatever the alerts named.

    Alerts are included because an incident's own ``services`` list is often
    empty on correlation-created incidents, and the alerts that formed it
    still carry the signal.
    """
    if not isinstance(pack, dict):
        return []
    found: list[str] = []

    incident = pack.get("incident")
    if isinstance(incident, dict):
        for key in ("services", "affected_services"):
            value = incident.get(key)
            if isinstance(value, list):
                found.extend(str(v) for v in value if v)
            elif isinstance(value, str) and value:
                found.append(value)

    alerts = pack.get("alerts")
    if isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            value = alert.get("service")
            if isinstance(value, str) and value:
                found.append(value)

    # Stable order, no duplicates: the namespaces we query should not change
    # between runs of the same incident.
    seen: set[str] = set()
    ordered: list[str] = []
    for service in found:
        key = service.strip().lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(service.strip())
    return ordered


def from_context_pack(
    pack: dict[str, Any] | None,
    *,
    cluster: str = "",
    max_namespaces: int = MAX_NAMESPACES,
) -> Scope:
    """Build the scope specialists aim at. Never raises."""
    services = _services_from(pack)
    mapping = _service_namespace_map()

    namespaces: list[str] = []
    for service in services:
        candidate = mapping.get(service.lower(), service)
        if candidate not in namespaces:
            namespaces.append(candidate)
        if len(namespaces) >= max_namespaces:
            break

    scope = Scope(
        cluster=cluster,
        services=tuple(services),
        namespaces=tuple(namespaces),
    )
    if not scope.derived:
        logger.info("incident carried no services; gathering cannot be scoped to it")
    return scope
