"""Policy evaluation engine (ADR-0003): precedence + fail-closed default.

Evaluation order:
1. Enabled policies of the requesting tenant (``tenant_id == tenant``),
2. then enabled global policies (``tenant_id == "*"``),
3. then the fail-closed default: deny.

Within a scope, policies are considered in (created_at, id) order and rules
in list order; the first matching rule decides. A rule matches when its
``execution_class`` equals the request's and both ``tools`` and
``environments`` contain the request value or ``"*"``.
"""

import logging
from dataclasses import dataclass

from sqlmodel import Session, select

from aiops_api.db import session_scope
from aiops_api.modules.policy.models import GLOBAL_TENANT, Policy

logger = logging.getLogger(__name__)

ALLOW = "allow"
DENY = "deny"
APPROVAL_REQUIRED = "approval_required"

DEFAULT_POLICY_ID = "m0-suggest-only"
DEFAULT_POLICY_RULES = [
    {"execution_class": "read", "decision": ALLOW, "tools": ["*"], "environments": ["*"]},
    {"execution_class": "mutate", "decision": DENY, "tools": ["*"], "environments": ["*"]},
]


@dataclass(frozen=True)
class PolicyDecision:
    """``policy_id=None`` means the fail-closed default produced the deny."""

    decision: str
    policy_id: str | None


def _rule_matches(rule: dict, tool_name: str, execution_class: str, environment: str) -> bool:
    if rule.get("execution_class") != execution_class:
        return False
    tools = rule.get("tools") or []
    if "*" not in tools and tool_name not in tools:
        return False
    environments = rule.get("environments") or ["*"]
    return "*" in environments or environment in environments


def evaluate(
    session: Session,
    tenant_id: str,
    tool_name: str,
    execution_class: str,
    environment: str = "*",
) -> PolicyDecision:
    """Evaluate persisted policies; fail closed (deny) when nothing matches."""
    scopes = [GLOBAL_TENANT] if tenant_id == GLOBAL_TENANT else [tenant_id, GLOBAL_TENANT]
    for scope in scopes:
        policies = session.exec(
            select(Policy)
            .where(Policy.tenant_id == scope, Policy.enabled.is_(True))
            .order_by(Policy.created_at, Policy.id)
        ).all()
        for policy in policies:
            for rule in policy.rules:
                if _rule_matches(rule, tool_name, execution_class, environment):
                    return PolicyDecision(decision=rule["decision"], policy_id=policy.id)
    return PolicyDecision(decision=DENY, policy_id=None)


def evaluate_with_session(
    tenant_id: str,
    tool_name: str,
    execution_class: str,
    environment: str = "*",
) -> PolicyDecision:
    """Evaluate opening its own session (callers without one, e.g. the orchestrator gate)."""
    with session_scope() as session:
        return evaluate(session, tenant_id, tool_name, execution_class, environment)


def seed_default_policies() -> None:
    """Idempotently insert the global suggest-only policy (deny mutate, allow read).

    Never crashes startup: if the schema is not there yet (e.g. migrations
    pending in prod), the fail-closed default still governs tool calls.
    """
    try:
        with session_scope() as session:
            if session.get(Policy, DEFAULT_POLICY_ID) is not None:
                return
            session.add(
                Policy(
                    id=DEFAULT_POLICY_ID,
                    tenant_id=GLOBAL_TENANT,
                    description="M0/M1 default posture: suggest-only. Read-class tools allowed, mutate denied.",
                    rules=[dict(rule) for rule in DEFAULT_POLICY_RULES],
                    enabled=True,
                )
            )
        logger.info("seeded default policy", extra={"policy_id": DEFAULT_POLICY_ID})
    except Exception:
        logger.warning("policy seed skipped (schema not ready?)", exc_info=True)
