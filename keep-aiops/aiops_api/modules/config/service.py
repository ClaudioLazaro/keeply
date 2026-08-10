"""Effective-configuration resolution.

The runtime never reads `AgentConfig` rows directly — it asks for an
:class:`EffectiveConfig`, which is the persisted row layered over the env
defaults. That keeps a deployment that never touches the API behaving
exactly as it did before this module existed.

Reads go through a short TTL cache because the orchestrator resolves config
once per investigation and a DB round trip per incident is wasted work.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from aiops_api.db import get_engine, session_scope
from aiops_api.modules.config.models import GLOBAL_TENANT, AgentConfig
from aiops_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 10.0

_cache: dict[str, tuple[float, "EffectiveConfig"]] = {}


# Provider names LiteLLM recognises as a routing prefix.
#
# Closed on purpose. Keep's provider types and LiteLLM's provider names
# mostly coincide but not always — `openai_compatible` is a Keep concept
# with no LiteLLM meaning, and prefixing with it would produce a model
# string that resolves to nothing. An unlisted provider is left exactly as
# the operator typed it, which is the behaviour that existed before this
# normalisation did.
LITELLM_PROVIDERS = frozenset(
    {
        "openai",
        "anthropic",
        "deepseek",
        "gemini",
        "groq",
        "mistral",
        "ollama",
        "vertex_ai",
        "xai",
    }
)


def canonical_model(model: str | None, provider: str | None) -> str | None:
    """The model's own name, with any routing prefix removed.

    **This is the canonical form**, and the only one this service hands out.

    The prefix is not part of a model's name — it says where to send the
    call, which ``provider`` already says. Keeping it inside ``model`` stores
    the same fact twice, and the two copies then disagree: that is exactly
    how a bare name reached LiteLLM and a prefixed one reached DeepSeek's
    own endpoint, each rejected by the side it was wrong for.

    So the prefix is stripped on the way out of storage and re-composed only
    by the consumer that needs it. Every other consumer — the settings page,
    the frontend assistants — gets a name it can use directly, and needs no
    translation logic of its own.

    Stripped only when the prefix is exactly this provider's own name.
    Models legitimately contain slashes (``meta-llama/llama-3``), and
    cutting at the first one would rename them into something that does not
    exist.
    """
    if not model or not provider:
        return model
    prefix = f"{provider.lower()}/"
    return model[len(prefix) :] if model.lower().startswith(prefix) else model


def to_litellm_model(model: str | None, provider: str | None) -> str | None:
    """Model name in the form LiteLLM routes on.

    The single place a routing prefix is ever added, and the inverse of
    :func:`canonical_model`. Only LiteLLM needs this spelling; nothing else
    should call it.
    """
    if not model:
        return model
    if "/" in model:
        # Already prefixed, or a model whose real name contains a slash
        # (`meta-llama/llama-3`). Either way, not ours to rewrite.
        return model
    if provider in LITELLM_PROVIDERS:
        return f"{provider}/{model}"
    return model


@dataclass(frozen=True)
class AssistantRouting:
    """The resolved answer for one AI feature."""

    function: str
    provider: str | None
    model: str | None
    thinking: str
    #: Fields that fell through to a default rather than being chosen here.
    inherited: list[str] = field(default_factory=list)

    @property
    def litellm_model(self) -> str | None:
        """This function's model, spelled the way LiteLLM expects."""
        return to_litellm_model(self.model, self.provider)


@dataclass(frozen=True)
class EffectiveConfig:
    """What the agents actually run with, after merging row over env."""

    llm_provider: str | None
    llm_model: str | None
    llm_api_key_env: str | None
    budget_max_tool_calls: int
    budget_max_wall_time_seconds: float
    budget_max_llm_tokens: int
    context_timeline_limit: int
    llm_embedding_model: str | None
    auto_investigate_severities: list[str] = field(default_factory=list)
    disabled_specialists: list[str] = field(default_factory=list)
    assistants: dict[str, Any] = field(default_factory=dict)

    def for_function(self, name: str) -> "AssistantRouting":
        """How one AI feature should be routed, and where each value came from.

        Layered: the function's own setting, then the tenant default, then
        env. ``inherited`` names the fields that fell through, because a
        settings page that shows an inherited value as though it were chosen
        makes the operator think they set something they did not — and the
        next person to change the default is then surprised.
        """
        override = (self.assistants or {}).get(name) or {}
        if not isinstance(override, dict):
            override = {}

        inherited: list[str] = []

        def pick(key: str, fallback: Any) -> Any:
            value = override.get(key)
            if value in (None, ""):
                inherited.append(key)
                return fallback
            return value

        provider = pick("provider", self.llm_provider)
        return AssistantRouting(
            function=name,
            provider=provider,
            # Canonical: whatever spelling is stored, callers get the
            # model's own name. Only LiteLLM re-adds the prefix.
            model=canonical_model(pick("model", self.llm_model), provider),
            # Thinking has no tenant-level default: `auto` means the system
            # works it out, which is the right answer when nobody has an
            # opinion.
            thinking=override.get("thinking") or "auto",
            inherited=sorted(inherited),
        )

    @property
    def llm_api_key(self) -> str:
        """Resolve the credential at use time.

        **Keep's provider system is the system of record.** An AI provider
        installed in Keep (`/providers`) supplies the key; the AI plane
        keeps no copy. The remaining paths exist only for bootstrap and
        for deployments that predate provider-based routing.

        1. the installed Keep provider selected by ``llm_provider``
        2. referenced env var
        3. ``AIOPS_LLM_API_KEY`` bootstrap default
        """
        provider = self.keep_provider
        if provider is not None:
            key = provider.secret("api_key", "token", "access_token")
            if key:
                return key
        if self.llm_api_key_env:
            resolved = os.environ.get(self.llm_api_key_env, "")
            if resolved:
                return resolved
        return get_settings().llm_api_key

    @property
    def keep_provider(self):
        """The installed Keep AI provider backing this config, if any."""
        if not self.llm_provider:
            return None
        from keep_client.providers import ai_providers

        for provider in ai_providers():
            if provider.type == self.llm_provider:
                return provider
        return None

    @property
    def llm_api_key_source(self) -> str:
        """Where the active credential comes from — shown in the UI."""
        provider = self.keep_provider
        if provider is not None and provider.secret("api_key", "token", "access_token"):
            return f"Keep provider ({provider.display_name})"
        if self.llm_api_key_env and os.environ.get(self.llm_api_key_env):
            return "environment variable"
        if get_settings().llm_api_key:
            return "AIOPS_LLM_API_KEY default"
        return "not configured"

    @property
    def llm_enabled(self) -> bool:
        """A model alone is not enough — the credential has to resolve."""
        return bool(self.llm_model) and bool(self.llm_api_key)


def _row_for(session: Session, tenant_id: str) -> AgentConfig | None:
    scopes = [tenant_id, GLOBAL_TENANT] if tenant_id != GLOBAL_TENANT else [GLOBAL_TENANT]
    for scope in scopes:
        row = session.exec(select(AgentConfig).where(AgentConfig.tenant_id == scope)).first()
        if row is not None:
            return row
    return None


def _build(row: AgentConfig | None, settings: Settings) -> EffectiveConfig:
    merged = (row or AgentConfig()).merged(settings)
    return EffectiveConfig(
        llm_provider=merged["llm_provider"],
        # Canonicalised here so it is true for every reader, including rows
        # written before the prefix stopped being stored inside the name.
        # A migration would fix the rows; this fixes the rows *and* anything
        # an operator pastes tomorrow, in one place.
        llm_model=canonical_model(merged["llm_model"], merged["llm_provider"]),
        llm_api_key_env=merged["llm_api_key_env"],
        budget_max_tool_calls=merged["budget_max_tool_calls"],
        budget_max_wall_time_seconds=merged["budget_max_wall_time_seconds"],
        budget_max_llm_tokens=merged["budget_max_llm_tokens"],
        context_timeline_limit=merged["context_timeline_limit"],
        llm_embedding_model=merged["llm_embedding_model"],
        auto_investigate_severities=list(merged["auto_investigate_severities"]),
        disabled_specialists=list(merged["disabled_specialists"]),
        assistants=dict(merged["assistants"]),
    )


def get_effective_config(tenant_id: str = GLOBAL_TENANT) -> EffectiveConfig:
    """Effective config for a tenant; env-only when the table is unavailable.

    A missing table (migrations pending) must not take the investigation
    path down — it degrades to the env defaults, which is the pre-existing
    behaviour.
    """
    now = time.monotonic()
    cached = _cache.get(tenant_id)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    settings = get_settings()
    try:
        with Session(get_engine()) as session:
            config = _build(_row_for(session, tenant_id), settings)
    except Exception:  # noqa: BLE001 — config must never break investigations
        logger.warning("agent config unavailable, using env defaults", exc_info=True)
        config = _build(None, settings)

    _cache[tenant_id] = (now, config)
    return config


def model_for(config: EffectiveConfig, function: str, settings: Settings) -> str | None:
    """The model a given AI feature should call, ready for LiteLLM.

    One place, so the two translations that exist — per-function override
    and LiteLLM spelling — are applied together everywhere rather than at
    each call site, where one of them would eventually be forgotten.
    """
    routing = config.for_function(function)
    return routing.litellm_model or to_litellm_model(
        settings.llm_model or None, routing.provider or config.llm_provider
    )


def invalidate_cache() -> None:
    """Drop cached config (called after a write, and by tests)."""
    _cache.clear()


def get_or_create_row(tenant_id: str = GLOBAL_TENANT) -> AgentConfig:
    """Fetch the tenant's config row, creating an all-inherit row if absent."""
    with session_scope() as session:
        row = session.exec(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)).first()
        if row is None:
            row = AgentConfig(tenant_id=tenant_id)
            session.add(row)
            session.flush()
        session.expunge(row)
        return row


def _mask(value: str) -> str:
    """Last 4 characters only — enough to identify a key, not to rebuild it."""
    if not value:
        return ""
    return f"••••••••{value[-4:]}" if len(value) > 4 else "••••••••"


def key_env_status(config: EffectiveConfig) -> dict[str, Any]:
    """Credential status for the UI — presence and origin, never the value."""
    provider = config.keep_provider
    return {
        "env_var": config.llm_api_key_env,
        "present": bool(config.llm_api_key),
        "source": config.llm_api_key_source,
        "masked": _mask(config.llm_api_key),
        # Which installed Keep provider supplies it, so the settings page
        # can deep-link to /providers instead of asking for a key.
        "provider_id": provider.id if provider is not None else None,
        "provider_type": provider.type if provider is not None else None,
    }
