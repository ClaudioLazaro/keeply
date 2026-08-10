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

    # --- Per-function assistant routing -----------------------------------
    # Different AI features have genuinely different needs: drafting a
    # workflow wants a fast cheap model, writing an RCA wants the strongest
    # one available. One global choice forces the expensive model on the
    # cheap job or the weak model on the job that matters.
    #
    # Shape: {"workflow_builder": {"provider": "deepseek",
    #                              "model": "deepseek-chat",
    #                              "thinking": "auto"}}
    #
    # An absent function, or a null field inside one, means "inherit the
    # tenant default above" — same rule as every other column here, so an
    # untouched deployment behaves exactly as it did before.
    assistants: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # --- Cost budget (per investigation) ---------------------------------
    budget_max_tool_calls: int | None = None
    budget_max_wall_time_seconds: float | None = None
    budget_max_llm_tokens: int | None = None

    # --- Context & knowledge ----------------------------------------------
    # How many timeline entries the context pack carries into the RCA.
    context_timeline_limit: int | None = None
    # Embedding model for knowledge retrieval; empty falls back to keyword
    # matching, which works without any model at all.
    llm_embedding_model: str | None = None

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
            "context_timeline_limit": (
                self.context_timeline_limit
                if self.context_timeline_limit is not None
                else settings.context_timeline_limit
            ),
            "llm_embedding_model": (
                self.llm_embedding_model
                if self.llm_embedding_model is not None
                else (settings.llm_embedding_model or None)
            ),
            "auto_investigate_severities": (
                self.auto_investigate_severities
                if self.auto_investigate_severities is not None
                else sorted(settings.auto_investigate_severities)
            ),
            "disabled_specialists": self.disabled_specialists or [],
            "assistants": self.assistants or {},
        }


# Every AI feature that routes to an LLM, and what it is for. Declared here
# rather than inferred from callers so the settings page can list functions
# that exist but have never been configured — a feature the operator cannot
# see is a feature they cannot fix.
ASSISTANT_FUNCTIONS: dict[str, str] = {
    "workflow_builder": "Drafts and edits workflows in the builder chat",
    "incident_chat": "Answers questions about an open incident",
    "ai_summary": "Summarises alerts and incidents on demand",
    "rca": "Writes the root-cause analysis for an investigation",
}

# What an operator may say about thinking mode. `auto` is the default and
# means "find out" — see LlmCapability.
THINKING_MODES: tuple[str, ...] = ("auto", "on", "off")


class LlmCapability(SQLModel, table=True):
    """What a model turned out to actually accept, learned by trying.

    Kept apart from :class:`AgentConfig` on purpose. That table is what the
    operator *chose*; this one is what the system *found out*, and the two
    must never be shown as the same kind of fact. Writing a discovered
    downgrade back into the operator's own settings would make the product
    claim they asked for something they never asked for.

    Every row carries the provider's own error text as the evidence. A
    downgrade with no recorded cause is indistinguishable from a bug, which
    is the same standard the evidence provenance work holds elsewhere.
    """

    __tablename__ = "llm_capability"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(default=GLOBAL_TENANT, index=True)

    provider: str = Field(index=True)
    model: str = Field(index=True)

    # Which compatibility downgrades this model needs, e.g.
    # ["tool_choice", "reasoning_content"]. Empty means it accepted the
    # strong form — a useful fact in itself, and the reason this is not
    # merely an absence of rows.
    downgrades: list[str] | None = Field(default=None, sa_column=Column(JSON))

    # The provider's verbatim 400, so a surprising downgrade can be read
    # rather than guessed at.
    evidence: str | None = None

    observed_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
