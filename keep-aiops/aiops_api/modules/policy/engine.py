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
import threading
import time
from dataclasses import dataclass

from sqlalchemy import event
from sqlmodel import Session, select

from aiops_api.db import session_scope
from aiops_api.modules.policy.models import GLOBAL_TENANT, Policy

logger = logging.getLogger(__name__)

# Scope -> (expires_at_monotonic, [(policy_id, rules), ...]) in decision order.
#
# Every tool call an investigation makes evaluates policy, and each
# evaluation ran its own SELECT — up to 50 connection checkouts per
# investigation, issued from a thread that already holds one. Policies
# change on operator action, not on the hot path, so a short TTL costs
# nothing and removes the checkouts. Rules are cached as plain dicts:
# detached ORM rows would raise once their session closed.
_POLICY_CACHE: dict[str, tuple[float, list[tuple[str, list[dict]]]]] = {}
_POLICY_CACHE_LOCK = threading.Lock()
_POLICY_CACHE_TTL_SECONDS = 10.0


def invalidate_cache() -> None:
    """Drop the cached policy sets."""
    with _POLICY_CACHE_LOCK:
        _POLICY_CACHE.clear()


@event.listens_for(Policy, "after_insert")
@event.listens_for(Policy, "after_update")
@event.listens_for(Policy, "after_delete")
def _invalidate_on_write(_mapper, _connection, _target) -> None:
    """Clear the cache whenever any Policy row is written.

    Hooked at the ORM rather than at the API handler on purpose: the router
    is only one writer. The startup seed and any future code path write
    through a session too, and a policy change that failed to invalidate
    would leave tool calls governed by rules the operator already replaced
    — the kind of staleness that stops being cosmetic the moment mutate
    tools are gated by this. Over-invalidating (on a later rollback) only
    costs one re-read.

    This is process-local. A second replica keeps serving its own snapshot
    until the TTL expires, which is the honest limit of an in-process
    cache and why the v2 design moves this to a watched KV bundle.
    """
    invalidate_cache()


def _policies_for_scope(session: Session, scope: str) -> list[tuple[str, list[dict]]]:
    """Enabled policies for one scope, in (created_at, id) order, cached."""
    now = time.monotonic()
    with _POLICY_CACHE_LOCK:
        hit = _POLICY_CACHE.get(scope)
        if hit is not None and hit[0] > now:
            return hit[1]

    loaded = [
        (policy.id, [dict(rule) for rule in policy.rules])
        for policy in session.exec(
            select(Policy)
            .where(Policy.tenant_id == scope, Policy.enabled.is_(True))
            .order_by(Policy.created_at, Policy.id)
        ).all()
    ]
    with _POLICY_CACHE_LOCK:
        _POLICY_CACHE[scope] = (now + _POLICY_CACHE_TTL_SECONDS, loaded)
    return loaded

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
        for policy_id, rules in _policies_for_scope(session, scope):
            for rule in rules:
                if _rule_matches(rule, tool_name, execution_class, environment):
                    return PolicyDecision(decision=rule["decision"], policy_id=policy_id)
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
