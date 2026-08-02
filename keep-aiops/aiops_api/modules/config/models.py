"""Persisted agent configuration.

One row per tenant (plus a global `*` row). Everything here is
operator-tunable at runtime; env vars remain the bootstrap defaults, so an
untouched deployment behaves exactly as before this module existed.

**No secret is ever stored here.** LLM credentials live in Keep's provider
system (`/providers`) — the same place every other integration credential
lives. This table only points at which installed provider to route
through; `llm_api_key_env` remains as an escape hatch for deployments that
inject the key by environment instead.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

GLOBAL_TENANT = "*"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentConfig(SQLModel, table=True):
    """Runtime knobs for the investigation agents.

    A NULL/None field means "inherit the env default" — that is how an
    upgrade stays behaviour-preserving: the seeded row is all-None until
    an operator changes something.
    """

    __tablename__ = "agent_config"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(default=GLOBAL_TENANT, unique=True, index=True)

    # --- LLM routing (LiteLLM, ADR-0007) ---------------------------------
    # provider is informational (UI grouping); llm_model is what LiteLLM
    # receives, e.g. "anthropic/claude-sonnet-4-5".
    # Keep provider `type` (e.g. "deepseek") whose installed credential
    # the RCA writer uses. Keep's provider system owns the secret; this is
    # only a pointer to which installed provider to route through.
    llm_provider: str | None = None
    llm_model: str | None = None
    # Escape hatch for deployments injecting the key by environment
    # instead of installing a Keep provider. Nothing secret is stored.
    llm_api_key_env: str | None = None

    # --- Cost budget (per investigation) ---------------------------------
    budget_max_tool_calls: int | None = None
    budget_max_wall_time_seconds: float | None = None
    budget_max_llm_tokens: int | None = None

    # --- Orchestration ----------------------------------------------------
    # Incident severities that auto-start an investigation.
    auto_investigate_severities: list[str] | None = Field(default=None, sa_column=Column(JSON))
    # Specialist names explicitly disabled. Absent = all enabled (subject to
    # the live-catalog check the coordinator already does).
    disabled_specialists: list[str] | None = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})

    def merged(self, settings: Any) -> dict[str, Any]:
        """Effective configuration: this row's values over env defaults."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model if self.llm_model is not None else (settings.llm_model or None),
            "llm_api_key_env": self.llm_api_key_env,
            "budget_max_tool_calls": (
                self.budget_max_tool_calls
                if self.budget_max_tool_calls is not None
                else settings.budget_max_tool_calls
            ),
            "budget_max_wall_time_seconds": (
                self.budget_max_wall_time_seconds
                if self.budget_max_wall_time_seconds is not None
                else settings.budget_max_wall_time_seconds
            ),
            "budget_max_llm_tokens": (
                self.budget_max_llm_tokens
                if self.budget_max_llm_tokens is not None
                else settings.budget_max_llm_tokens
            ),
            "auto_investigate_severities": (
                self.auto_investigate_severities
                if self.auto_investigate_severities is not None
                else sorted(settings.auto_investigate_severities)
            ),
            "disabled_specialists": self.disabled_specialists or [],
        }
