"""Runtime integration config for the gateway, pulled from aiops-api.

Why pull instead of reading the database directly: the gateway is a
security boundary (ADR-0002) and is deliberately the thinnest process in
the system. Giving it Postgres credentials would widen the blast radius of
the one component most exposed to tool payloads. Instead it fetches
resolved config over HTTP on a short TTL, and aiops-api stays the single
owner of configuration state.

Failure is always safe: if aiops-api is unreachable, or has nothing stored
for an integration, the gateway falls back to its environment settings —
which default to ``stub``. A control-plane outage can therefore never
silently promote a backend to ``live``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from mcp_gateway.settings import get_settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 15.0
FETCH_TIMEOUT_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[str, Any] = {}
# Separate from the cache contents: an empty result and "never fetched"
# are different states. Keying the TTL off truthiness would re-fetch on
# every call whenever the control plane is down or has nothing stored —
# paying the HTTP timeout once per tool invocation.
_have_fetched: bool = False
_fetched_at: float = 0.0
_last_error: str | None = None


def _fetch() -> dict[str, Any]:
    settings = get_settings()
    base = (settings.aiops_api_url or "").rstrip("/")
    if not base:
        return {}
    headers = {"Accept": "application/json"}
    if settings.aiops_api_key:
        headers["X-API-KEY"] = settings.aiops_api_key
    with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = client.get(f"{base}/v1/integrations/resolved", headers=headers)
        response.raise_for_status()
        payload = response.json()
    return payload.get("integrations") or {}


def _current() -> dict[str, Any]:
    global _cache, _fetched_at, _have_fetched, _last_error
    now = time.monotonic()
    with _lock:
        if _have_fetched and now - _fetched_at < CACHE_TTL_SECONDS:
            return _cache
        try:
            _cache = _fetch()
            _last_error = None
        except Exception as exc:  # noqa: BLE001 — fall back to env, never fail a tool
            _cache = {}
            _last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("integration config pull failed, using env defaults: %s", _last_error)
        # Marked fetched either way: a failing control plane must be
        # retried on the TTL, not on every single tool call.
        _have_fetched = True
        _fetched_at = now
        return _cache


def invalidate() -> None:
    """Drop the cache (tests, and after a known config change)."""
    global _cache, _fetched_at, _have_fetched
    with _lock:
        _cache = {}
        _have_fetched = False
        _fetched_at = 0.0


def mode(integration: str) -> str:
    """Effective stub/live mode: stored config over env default."""
    entry = _current().get(integration)
    if isinstance(entry, dict):
        value = entry.get("mode")
        if value in ("stub", "live"):
            return value
    return getattr(get_settings(), f"{integration}_mode", "stub")


def value(integration: str, field: str, default: str = "") -> str:
    """Effective value for one field: stored config over env default.

    ``field`` is the catalog field name (``url``, ``api_key``, …); the env
    fallback is the matching gateway setting (``datadog_url``, …).
    """
    entry = _current().get(integration)
    if isinstance(entry, dict):
        stored = (entry.get("values") or {}).get(field)
        if isinstance(stored, str) and stored:
            return stored
    return getattr(get_settings(), f"{integration}_{field}", default) or default


def status() -> dict[str, Any]:
    """Diagnostics for /healthz: is the pull working, and what is live."""
    current = _current()
    return {
        "source": "aiops-api" if current else "env defaults",
        "integrations_overridden": sorted(current),
        "last_error": _last_error,
    }
