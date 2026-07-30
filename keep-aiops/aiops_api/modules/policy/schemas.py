"""Pydantic schemas for the policy API (validation boundary for PUT/evaluate)."""

from typing import Literal

from pydantic import BaseModel, Field

ExecutionClass = Literal["read", "mutate"]
Decision = Literal["allow", "deny", "approval_required"]


class PolicyRule(BaseModel):
    """One rule: exact execution_class match, wildcard-capable tool/env sets."""

    execution_class: ExecutionClass
    decision: Decision
    tools: list[str] = Field(min_length=1)  # ['*'] or tool names
    environments: list[str] = Field(default=["*"], min_length=1)


class PolicyUpsert(BaseModel):
    """Body for PUT /v1/policies/{id} (idempotent upsert)."""

    tenant_id: str = Field(min_length=1)  # '*' = global default
    description: str = ""
    rules: list[PolicyRule] = Field(min_length=1)
    enabled: bool = True


class PolicyEvaluateRequest(BaseModel):
    """Body for POST /v1/policies:evaluate (dry-run, no side effects)."""

    tenant_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    execution_class: str = Field(min_length=1)
    environment: str = "*"


class PolicyEvaluation(BaseModel):
    """Dry-run result: the decision and the policy whose rule produced it.

    ``policy_id=None`` means no rule matched and the fail-closed default
    (deny) applied.
    """

    decision: Decision
    policy_id: str | None = None
