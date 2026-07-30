"""Tenant auth middleware — identity delegation to Keep.

Every ``/v1/**`` request must carry a Keep API key in ``X-API-KEY``. The key
is validated by calling Keep's ``GET {AIOPS_KEEP_API_URL}/whoami`` (which
accepts the same ``X-API-KEY`` header and returns the caller identity, e.g.
``{"tenant_id": ...}``; in Keep NO_AUTH mode any key maps to tenant
``keep``). The identity is cached in-process for
``AIOPS_AUTH_CACHE_TTL_SECONDS`` (keyed by the SHA-256 of the key) and
injected into ``request.state.tenant_context`` as a :class:`TenantContext`.

Posture (fail-closed):

- missing key            -> 401
- Keep rejects the key   -> 401
- Keep unreachable/error -> 503 with a retry hint (never silently unauthenticated)

Exemptions:

- ``/healthz``, ``/readyz``, ``/metrics`` are outside ``/v1/**`` and untouched.
- ``POST /v1/events/keep`` is a server-to-server endpoint authenticated by the
  ``X-Keep-Signature`` HMAC over the raw body (shared webhook secret); Keep's
  outbox dispatcher does not hold a per-tenant user API key, so API-key auth
  does not apply there.

Bearer JWT (``Authorization: Bearer``) is a documented follow-up: validation
needs a settings-provided HS256 secret or RS256 JWKS URL wired through pyjwt.
Until then bearer tokens are rejected with 401 and API keys are the only
supported credential.
"""

import hashlib
import logging
import threading
import time

import httpx
from fastapi import Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from aiops_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Server-to-server HMAC endpoint: exempt from API-key auth (see module docstring).
_EXEMPT_ROUTES = {("POST", "/v1/events/keep")}


class TenantContext(BaseModel):
    """Identity injected into request.state for authenticated requests."""

    tenant_id: str
    email: str | None = None
    role: str | None = None


class _InvalidKey(Exception):
    pass


class _KeepUnavailable(Exception):
    pass


# In-process TTL cache: sha256(api_key) -> (expires_at_monotonic, TenantContext).
_cache: dict[str, tuple[float, TenantContext]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Drop every cached identity (tests / key revocation flush)."""
    with _cache_lock:
        _cache.clear()


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _authenticate(api_key: str, settings: Settings) -> TenantContext:
    """Validate an API key against Keep /whoami, with a short TTL cache."""
    key_hash = _key_hash(api_key)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key_hash)
        if hit is not None and hit[0] > now:
            return hit[1]

    try:
        async with httpx.AsyncClient(base_url=settings.keep_api_url, timeout=2.0) as client:
            response = await client.get("/whoami", headers={"X-API-KEY": api_key})
    except httpx.HTTPError as exc:
        logger.warning("keep /whoami unreachable", extra={"error": str(exc)})
        raise _KeepUnavailable from exc

    if response.status_code in (401, 403):
        raise _InvalidKey
    if response.status_code >= 400:
        logger.warning("keep /whoami unexpected status", extra={"status_code": response.status_code})
        raise _KeepUnavailable

    payload = response.json()
    context = TenantContext(
        tenant_id=payload["tenant_id"],
        email=payload.get("email"),
        role=payload.get("role"),
    )
    with _cache_lock:
        _cache[key_hash] = (now + settings.auth_cache_ttl_seconds, context)
    return context


def _error(status_code: int, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Enforces Keep-delegated API-key auth on /v1/** and injects TenantContext."""

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        path = request.url.path
        if (
            not settings.auth_enabled
            or not path.startswith("/v1/")
            or (request.method, path) in _EXEMPT_ROUTES
        ):
            return await call_next(request)

        api_key = request.headers.get("x-api-key")
        if not api_key:
            if request.headers.get("authorization", "").lower().startswith("bearer "):
                return _error(401, "bearer JWT validation is not yet supported; use X-API-KEY")
            return _error(401, "missing X-API-KEY header")

        try:
            context = await _authenticate(api_key, settings)
        except _InvalidKey:
            return _error(401, "invalid API key")
        except _KeepUnavailable:
            return _error(
                503,
                "authentication backend (Keep) unavailable; retry shortly",
                headers={"Retry-After": "5"},
            )

        request.state.tenant_context = context
        return await call_next(request)


def get_tenant_context(request: Request) -> TenantContext | None:
    """FastAPI dependency: the request tenant, or None when auth is disabled.

    Read paths MUST filter by ``context.tenant_id`` when a context is present
    and return 404 (never 403) on cross-tenant access. When auth is disabled
    (AIOPS_AUTH_ENABLED=false, dev/test only) None is returned and the legacy
    unfiltered behavior applies.
    """
    return getattr(request.state, "tenant_context", None)
