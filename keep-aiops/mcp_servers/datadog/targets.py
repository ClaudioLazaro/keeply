"""Which Datadog accounts this server may query.

Per ADR-0008 credentials belong to the Keep provider system, not here. This
resolves a *named target* to the credential Keep already holds, falling back
to environment configuration only where no provider is installed — so a fresh
deployment still runs, in stub mode, and says so.

The registry deliberately mirrors ``mcp_servers.k8s.clusters``. Two servers
with the same target shape is what lets the coordinator treat them uniformly;
two servers each inventing their own model is how the parallel credential
store came back the first time.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_SITE = "datadoghq.com"


class TargetUnavailable(Exception):
    """The target exists but its credentials could not be resolved."""


class UnknownTarget(Exception):
    """The named target is not registered."""


@dataclass(frozen=True)
class TargetSpec:
    name: str
    mode: str  # "live" | "stub"
    site: str = DEFAULT_SITE
    api_key: str = ""
    app_key: str = ""
    keep_provider_id: str | None = None
    description: str | None = None


def _from_keep_providers() -> list[TargetSpec]:
    """Datadog providers installed in Keep, one target each.

    Two accounts are two providers with distinct ids, which is what makes
    "use *this* Datadog" expressible. The type-level mapping it replaces could
    only say that some Datadog existed.
    """
    try:
        from keep_client.providers import list_installed
    except ImportError:
        return []
    try:
        installed = list_installed()
    except Exception:  # noqa: BLE001 — an unreachable Keep is a stub deployment, not a crash
        logger.info("could not read Keep providers; datadog targets fall back to env", exc_info=True)
        return []

    targets: list[TargetSpec] = []
    for provider in installed:
        if getattr(provider, "type", "") != "datadog":
            continue
        auth = getattr(provider, "authentication", None) or {}
        api_key = auth.get("api_key") or ""
        app_key = auth.get("app_key") or auth.get("application_key") or ""
        name = getattr(provider, "name", None) or getattr(provider, "id", "datadog")
        targets.append(
            TargetSpec(
                name=str(name),
                # A provider installed without both keys is registered and
                # honest about being unusable, rather than absent and puzzling.
                mode="live" if api_key and app_key else "stub",
                site=auth.get("domain") or auth.get("site") or DEFAULT_SITE,
                api_key=api_key,
                app_key=app_key,
                keep_provider_id=str(getattr(provider, "id", "")) or None,
                description="Datadog provider installed in Keep",
            )
        )
    return targets


def _from_environment() -> list[TargetSpec]:
    raw = os.environ.get("MCP_DATADOG_TARGETS", "").strip()
    if not raw:
        mode = os.environ.get("MCP_DATADOG_MODE", "stub").strip().lower()
        return [
            TargetSpec(
                name="default",
                mode="live" if mode == "live" else "stub",
                site=os.environ.get("MCP_DATADOG_SITE", DEFAULT_SITE),
                api_key=os.environ.get("MCP_DATADOG_API_KEY", ""),
                app_key=os.environ.get("MCP_DATADOG_APP_KEY", ""),
                description="Default target; install a Datadog provider in Keep to go live.",
            )
        ]
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("MCP_DATADOG_TARGETS is not valid JSON; falling back to one stub target")
        return [TargetSpec(name="default", mode="stub")]
    out: list[TargetSpec] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        out.append(
            TargetSpec(
                name=entry["name"],
                mode="live" if str(entry.get("mode", "stub")).lower() == "live" else "stub",
                site=entry.get("site", DEFAULT_SITE),
                api_key=entry.get("api_key", ""),
                app_key=entry.get("app_key", ""),
                description=entry.get("description"),
            )
        )
    return out or [TargetSpec(name="default", mode="stub")]


_registry: dict[str, TargetSpec] | None = None
_lock = threading.Lock()


def registry() -> dict[str, TargetSpec]:
    global _registry
    with _lock:
        if _registry is None:
            targets = _from_keep_providers() or _from_environment()
            _registry = {t.name: t for t in targets}
        return _registry


def reset_registry() -> None:
    """Drop the cached registry (tests, and after a provider is installed)."""
    global _registry
    with _lock:
        _registry = None


def get(name: str) -> TargetSpec:
    spec = registry().get(name)
    if spec is None:
        known = ", ".join(sorted(registry())) or "none configured"
        raise UnknownTarget(f"unknown datadog target {name!r}; registered targets: {known}")
    return spec
