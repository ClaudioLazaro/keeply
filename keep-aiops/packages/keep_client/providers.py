"""Read installed Keep providers — the system of record for credentials.

Keep already ships 120+ providers with a catalog, an install UI, secret
storage and scope validation. The AI plane must consume that rather than
keep its own copy: two credential stores means two rotation paths, two
audit surfaces, and an operator configuring Datadog twice.

``GET /providers`` returns installed providers with ``details.authentication``
resolved from Keep's secret manager, so this module is a thin read adapter.
Nothing here writes: installing and editing a provider stays in Keep's UI.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30.0
FETCH_TIMEOUT_SECONDS = 8.0

# Keep's provider `type` -> the AIOps integration it backs. Only the
# mapping is ours; the credentials, catalog and UI belong to Keep.
PROVIDER_TYPE_TO_INTEGRATION: dict[str, str] = {
    "kubernetes": "k8s",
    "prometheus": "prometheus",
    "datadog": "datadog",
    "eks": "eks",
    "argocd": "argocd",
    "jira": "jira",
    "jiraonprem": "jira",
    "slack": "slack",
}

# Providers Keep classifies as AI — candidates for the RCA writer.
AI_PROVIDER_TYPES: tuple[str, ...] = (
    "anthropic",
    "openai",
    "deepseek",
    "gemini",
    "grok",
    "llama_cpp",
    "ollama",
    "vllm",
    # Generic OpenAI-compatible endpoint (self-hosted or unlisted vendors).
    "openai_compatible",
    "litellm",
)

# LiteLLM model prefix per Keep provider type, so a provider installed in
# Keep resolves to a model string without the operator retyping it.
LITELLM_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "deepseek": "deepseek",
    "gemini": "gemini",
    "ollama": "ollama",
    # Generic/self-hosted endpoints take their model verbatim — adding a
    # prefix would only confuse LiteLLM for custom deployments.
    "openai_compatible": "",
    "litellm": "",
}


@dataclass(frozen=True)
class InstalledProvider:
    """One provider installed in Keep, with its resolved authentication."""

    id: str
    type: str
    display_name: str
    authentication: dict[str, Any] = field(default_factory=dict)

    def secret(self, *names: str) -> str:
        """First non-empty auth value among ``names``."""
        for name in names:
            value = self.authentication.get(name)
            if isinstance(value, str) and value:
                return value
        return ""


_cache: list[InstalledProvider] | None = None
_fetched_at: float = 0.0


def _parse(payload: dict[str, Any]) -> list[InstalledProvider]:
    out: list[InstalledProvider] = []
    for item in payload.get("installed_providers") or []:
        details = item.get("details") or {}
        auth = details.get("authentication") or {}
        out.append(
            InstalledProvider(
                id=str(item.get("id") or ""),
                type=str(item.get("type") or ""),
                display_name=str(item.get("display_name") or item.get("type") or ""),
                authentication=auth if isinstance(auth, dict) else {},
            )
        )
    return out


def list_installed(force: bool = False) -> list[InstalledProvider]:
    """Installed providers, cached briefly.

    Returns ``[]`` when Keep is unreachable — callers fall back to their
    own defaults, which are always the safe (stub / disabled) direction.
    """
    global _cache, _fetched_at
    now = time.monotonic()
    if not force and _cache is not None and now - _fetched_at < CACHE_TTL_SECONDS:
        return _cache

    from keep_client.client import KeepClient

    try:
        with KeepClient.from_settings() as keep:
            payload = keep.get_json("/providers", timeout=FETCH_TIMEOUT_SECONDS)
        _cache = _parse(payload)
    except Exception:  # noqa: BLE001 — Keep being down must not break the AI plane
        logger.warning("could not read Keep providers", exc_info=True)
        _cache = []
    _fetched_at = now
    return _cache


def invalidate() -> None:
    global _cache, _fetched_at
    _cache = None
    _fetched_at = 0.0


def for_integration(integration: str) -> InstalledProvider | None:
    """The installed Keep provider backing an AIOps integration, if any."""
    wanted = {
        provider_type
        for provider_type, name in PROVIDER_TYPE_TO_INTEGRATION.items()
        if name == integration
    }
    for provider in list_installed():
        if provider.type in wanted:
            return provider
    return None


def ai_providers() -> list[InstalledProvider]:
    """Installed providers Keep classifies as AI, for LLM routing."""
    return [p for p in list_installed() if p.type in AI_PROVIDER_TYPES]


def default_model_for(provider: InstalledProvider) -> str:
    """A LiteLLM model string for a provider, using its configured model
    when the provider exposes one."""
    configured = provider.secret("model", "deployment_name")
    prefix = LITELLM_PREFIX.get(provider.type, provider.type)
    if configured:
        return configured if "/" in configured else f"{prefix}/{configured}"
    return ""
