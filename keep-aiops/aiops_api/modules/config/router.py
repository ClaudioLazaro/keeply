"""Agent configuration API: GET/PUT /v1/config, POST /v1/config/llm:test.

This module owns only what is genuinely the AI plane's: which installed
Keep provider to route the LLM through, the model, cost ceilings, which
severities auto-investigate, and which specialists run.

**It stores no credentials.** LLM keys live in Keep's provider system
(`/providers`), alongside every other integration credential — one place
to install, rotate and audit. ``llm_provider`` is a pointer to a provider
`type`; ``llm_api_key_env`` remains only as an escape hatch for
deployments injecting the key by environment.
"""

import logging
import re

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from aiops_api.db import session_scope
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.config.models import GLOBAL_TENANT, AgentConfig, _utcnow
from aiops_api.modules.config.service import (
    get_effective_config,
    invalidate_cache,
    key_env_status,
)
from aiops_api.modules.specialists.registry import default_specialists

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/config", tags=["config"])

ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# Anything that looks like a live credential must never reach the database.
SECRET_LOOKING = re.compile(r"^(sk-|xox[baprs]-|ghp_|AKIA|Bearer\s)", re.IGNORECASE)

VALID_SEVERITIES = {"critical", "high", "warning", "info", "low"}


class AgentConfigUpdate(BaseModel):
    """Partial update. A field left unset is untouched; explicit null resets
    it to "inherit the env default"."""

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key_env: str | None = None
    budget_max_tool_calls: int | None = Field(default=None, ge=1, le=10_000)
    budget_max_wall_time_seconds: float | None = Field(default=None, ge=1, le=3600)
    budget_max_llm_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    context_timeline_limit: int | None = Field(default=None, ge=1, le=1000)
    llm_embedding_model: str | None = None
    auto_investigate_severities: list[str] | None = None
    disabled_specialists: list[str] | None = None

    @field_validator("llm_api_key_env")
    @classmethod
    def _must_be_env_var_name(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if SECRET_LOOKING.match(value) or not ENV_VAR_PATTERN.match(value):
            raise ValueError(
                "llm_api_key_env must be the NAME of an environment variable "
                "(e.g. ANTHROPIC_API_KEY), never the key itself"
            )
        return value

    @field_validator("auto_investigate_severities")
    @classmethod
    def _known_severities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - VALID_SEVERITIES)
        if unknown:
            raise ValueError(f"unknown severities: {unknown}; valid: {sorted(VALID_SEVERITIES)}")
        return value

    @field_validator("disabled_specialists")
    @classmethod
    def _known_specialists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        known = {spec.name for spec in default_specialists()}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(f"unknown specialists: {unknown}; valid: {sorted(known)}")
        return value


class LlmKeyStatus(BaseModel):
    """Never carries the credential — only whether one resolves, where from,
    and a masked tail so the operator can tell which key is stored."""

    env_var: str | None
    present: bool
    source: str
    masked: str
    # The installed Keep provider supplying the credential, so the UI can
    # link straight to /providers instead of asking for a key.
    provider_id: str | None
    provider_type: str | None


class AgentConfigResponse(BaseModel):
    """Effective config (row layered over env) plus what can be chosen."""

    tenant_id: str
    llm_provider: str | None
    llm_model: str | None
    llm_enabled: bool
    llm_api_key: LlmKeyStatus
    budget_max_tool_calls: int
    budget_max_wall_time_seconds: float
    budget_max_llm_tokens: int
    context_timeline_limit: int
    llm_embedding_model: str | None
    auto_investigate_severities: list[str]
    disabled_specialists: list[str]
    available_specialists: list[str]
    available_severities: list[str]


def _resolve_tenant(context: TenantContext | None) -> str:
    return context.tenant_id if context is not None else GLOBAL_TENANT


def _response(tenant_id: str) -> AgentConfigResponse:
    config = get_effective_config(tenant_id)
    return AgentConfigResponse(
        tenant_id=tenant_id,
        llm_provider=config.llm_provider,
        llm_model=config.llm_model,
        llm_enabled=config.llm_enabled,
        llm_api_key=LlmKeyStatus(**key_env_status(config)),
        budget_max_tool_calls=config.budget_max_tool_calls,
        budget_max_wall_time_seconds=config.budget_max_wall_time_seconds,
        budget_max_llm_tokens=config.budget_max_llm_tokens,
        context_timeline_limit=config.context_timeline_limit,
        llm_embedding_model=config.llm_embedding_model,
        auto_investigate_severities=config.auto_investigate_severities,
        disabled_specialists=config.disabled_specialists,
        available_specialists=sorted(spec.name for spec in default_specialists()),
        available_severities=sorted(VALID_SEVERITIES),
    )


@router.get("")
def get_config(
    context: TenantContext | None = Depends(get_tenant_context),
) -> AgentConfigResponse:
    return _response(_resolve_tenant(context))


@router.put("")
def update_config(
    body: AgentConfigUpdate,
    context: TenantContext | None = Depends(get_tenant_context),
) -> AgentConfigResponse:
    tenant_id = _resolve_tenant(context)
    # Only fields the caller actually sent are touched; everything else
    # keeps whatever it had (including "inherit env").
    changes = body.model_dump(exclude_unset=True)

    with session_scope() as session:
        row = session.exec(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)).first()
        if row is None:
            row = AgentConfig(tenant_id=tenant_id)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = _utcnow()
        session.add(row)

    invalidate_cache()
    logger.info(
        "agent config updated",
        # Field NAMES only — never values, one of which may be a credential.
        extra={"tenant_id": tenant_id, "fields": sorted(changes)},
    )
    return _response(tenant_id)


class LlmTestRequest(BaseModel):
    """Probe the configured provider and discover its models.

    No credential is accepted here — it comes from the installed Keep
    provider. The browser never handles a key, so there is nothing to send.
    """

    llm_model: str | None = None
    api_base: str | None = None


class LlmTestResponse(BaseModel):
    ok: bool
    detail: str
    models: list[str] = []
    model_tested: str | None = None


# OpenAI-compatible /models endpoints, by provider prefix. Providers not
# listed simply skip discovery — the completion probe still validates.
_MODELS_ENDPOINT = {
    "deepseek": "https://api.deepseek.com/models",
    "openai": "https://api.openai.com/v1/models",
    "ollama": None,
}


def _discover_models(provider: str, api_key: str, api_base: str | None) -> list[str]:
    base = api_base or _MODELS_ENDPOINT.get(provider)
    if not base:
        return []
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(base, headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001 — discovery is best-effort
        logger.info("model discovery failed for provider %s", provider, exc_info=True)
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return sorted(str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id"))


@router.post("/llm:test")
def test_llm(
    body: LlmTestRequest,
    context: TenantContext | None = Depends(get_tenant_context),
) -> LlmTestResponse:
    """Probe the configured provider with a real call.

    Runs a short completion so the answer is authoritative rather than a
    guess about whether the key format looks right. Errors are returned as
    ``ok: false`` with the provider's message — never raised — so the
    settings page can show them inline.
    """
    tenant_id = _resolve_tenant(context)
    config = get_effective_config(tenant_id)

    api_key = config.llm_api_key
    model = (body.llm_model or "").strip() or config.llm_model
    provider = (config.llm_provider or (model.split("/")[0] if "/" in model else "")).strip()

    if not model:
        return LlmTestResponse(ok=False, detail="No model configured.")
    if not api_key and provider != "ollama":
        return LlmTestResponse(
            ok=False,
            detail=(
                f"No credential for '{provider or 'the selected provider'}'. "
                "Install it at Providers first."
            ),
        )

    models = _discover_models(provider, api_key, body.api_base)

    try:
        import litellm

        response = litellm.completion(
            model=model,
            api_key=api_key or None,
            messages=[{"role": "user", "content": "Reply with: ok"}],
            # Reasoning models spend tokens before emitting content, so a
            # tight ceiling would look like a failure. See RCA_MAX_TOKENS.
            max_tokens=512,
            temperature=0,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 — the provider error IS the answer
        return LlmTestResponse(
            ok=False,
            detail=f"{type(exc).__name__}: {exc}"[:400],
            models=models,
            model_tested=model,
        )

    usage = getattr(response, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return LlmTestResponse(
        ok=True,
        detail=f"Connected. Model answered in {tokens} tokens." if tokens else "Connected.",
        models=models,
        model_tested=model,
    )


@router.get("/llm-providers")
def list_llm_providers() -> dict:
    """AI providers **installed in Keep**, ready to route the RCA writer to.

    Not a hardcoded catalog: Keep's provider system is the source of truth
    for what exists and what is configured. An operator installs an AI
    provider at `/providers` and it shows up here — no second credential
    form, no second place to rotate a key.
    """
    from keep_client.providers import ai_providers, default_model_for

    installed = [
        {
            "id": provider.id,
            "type": provider.type,
            "label": provider.display_name,
            "configured": bool(provider.secret("api_key", "token", "access_token")),
            "suggested_model": default_model_for(provider),
        }
        for provider in ai_providers()
    ]
    return {
        "providers": installed,
        # Where to go when nothing is installed yet.
        "install_url": "/providers",
    }
