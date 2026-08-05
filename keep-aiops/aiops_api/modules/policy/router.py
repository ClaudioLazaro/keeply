"""Policy API: GET /v1/policies, PUT /v1/policies/{id}, POST /v1/policies:evaluate.

PUT is an idempotent upsert validated by pydantic (422 on invalid bodies).
The evaluate endpoint is a dry-run: it persists nothing and returns the
decision plus the id of the policy whose rule produced it (null when the
fail-closed default applied).
"""

from fastapi import APIRouter, Query
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.modules.policy import engine
from aiops_api.modules.policy.models import GLOBAL_TENANT, Policy, _utcnow
from aiops_api.modules.policy.schemas import PolicyEvaluateRequest, PolicyEvaluation, PolicyUpsert

router = APIRouter(prefix="/v1/policies", tags=["policy"])


@router.get("")
def list_policies(tenant_id: str | None = Query(default=None)) -> list[Policy]:
    """Policies visible to a tenant: its own plus the global '*' defaults."""
    with Session(get_engine()) as session:
        statement = select(Policy).order_by(Policy.created_at, Policy.id)
        if tenant_id is not None:
            statement = statement.where(Policy.tenant_id.in_([tenant_id, GLOBAL_TENANT]))
        return list(session.exec(statement).all())


@router.put("/{policy_id}")
def upsert_policy(policy_id: str, body: PolicyUpsert) -> Policy:
    rules = [rule.model_dump() for rule in body.rules]
    with Session(get_engine()) as session:
        policy = session.get(Policy, policy_id)
        if policy is None:
            policy = Policy(id=policy_id, tenant_id=body.tenant_id, rules=rules)
        else:
            policy.tenant_id = body.tenant_id
            policy.rules = rules
            policy.updated_at = _utcnow()
        policy.description = body.description
        policy.enabled = body.enabled
        session.add(policy)
        session.commit()
        session.refresh(policy)
        # Cache invalidation is hooked on the Policy mapper (policy/engine.py),
        # so it already fired on this write — deliberately not repeated here,
        # so there is one mechanism rather than two that can disagree.
        return policy


@router.post(":evaluate")
def evaluate_policy(body: PolicyEvaluateRequest) -> PolicyEvaluation:
    with Session(get_engine()) as session:
        outcome = engine.evaluate(
            session,
            tenant_id=body.tenant_id,
            tool_name=body.tool_name,
            execution_class=body.execution_class,
            environment=body.environment,
        )
    return PolicyEvaluation(decision=outcome.decision, policy_id=outcome.policy_id)
