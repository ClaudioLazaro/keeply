"""SQLModel persistence for tenant-scoped tool policies (ADR-0003).

A policy is an ordered list of rules. Each rule maps an ``execution_class``
(``read`` | ``mutate``) to a ``decision`` (``allow`` | ``deny`` |
``approval_required``) for a set of tools and environments; ``"*"`` wildcards.
``tenant_id="*"`` marks the global default policy that applies when no
tenant-specific rule matches.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

GLOBAL_TENANT = "*"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Policy(SQLModel, table=True):
    __tablename__ = "policy"

    id: str = Field(primary_key=True)  # e.g. 'm0-suggest-only'
    tenant_id: str = Field(index=True)  # '*' = global default
    description: str = ""
    # JSON list of {execution_class, decision, tools: [...], environments: [...]}.
    # First matching rule in evaluation order decides.
    rules: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
