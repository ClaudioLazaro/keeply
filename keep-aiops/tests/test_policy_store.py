"""Persisted policy store + policy API (M1).

Covers: startup seeding, default suggest-only posture (deny mutate / allow
read), tenant-over-global precedence, disabled policies, dry-run matched
policy id, PUT validation (422), upsert semantics, tenant-scoped listing,
the fail-closed static fallback, and the alembic revision chain.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlmodel import Session, select

from aiops_api.db import get_engine, init_db, session_scope
from aiops_api.modules.policy import PolicyDenied, assert_tool_allowed
from aiops_api.modules.policy.engine import DEFAULT_POLICY_ID, seed_default_policies
from aiops_api.modules.policy.models import GLOBAL_TENANT, Policy

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _evaluate(client, **overrides) -> dict:
    body = {
        "tenant_id": TENANT_A,
        "tool_name": "get_pods",
        "execution_class": "read",
        "environment": "prod",
    }
    body.update(overrides)
    response = client.post("/v1/policies:evaluate", json=body)
    assert response.status_code == 200
    return response.json()


def _put_policy(client, policy_id: str, **overrides):
    body = {
        "tenant_id": TENANT_A,
        "description": "",
        "rules": [
            {"execution_class": "mutate", "decision": "allow", "tools": ["restart_pod"], "environments": ["*"]}
        ],
        "enabled": True,
    }
    body.update(overrides)
    return client.put(f"/v1/policies/{policy_id}", json=body)


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def test_seed_policy_present_after_startup(client):
    response = client.get("/v1/policies", params={"tenant_id": TENANT_A})
    assert response.status_code == 200
    seed = [p for p in response.json() if p["id"] == DEFAULT_POLICY_ID]
    assert len(seed) == 1
    assert seed[0]["tenant_id"] == GLOBAL_TENANT
    assert seed[0]["enabled"] is True
    decisions = {(rule["execution_class"], rule["decision"]) for rule in seed[0]["rules"]}
    assert ("read", "allow") in decisions
    assert ("mutate", "deny") in decisions


def test_seed_is_idempotent(settings_env):
    init_db()
    seed_default_policies()
    seed_default_policies()
    with Session(get_engine()) as session:
        policies = session.exec(select(Policy)).all()
    assert [p.id for p in policies] == [DEFAULT_POLICY_ID]


# --------------------------------------------------------------------------- #
# Default posture + evaluation precedence
# --------------------------------------------------------------------------- #


def test_default_deny_mutate_allow_read(client):
    allowed = _evaluate(client, tool_name="get_pods", execution_class="read")
    assert allowed == {"decision": "allow", "policy_id": DEFAULT_POLICY_ID}

    denied = _evaluate(client, tool_name="delete_pods", execution_class="mutate")
    assert denied == {"decision": "deny", "policy_id": DEFAULT_POLICY_ID}


def test_tenant_override_beats_global(client):
    response = _put_policy(client, "tenant-a-allow-restart")
    assert response.status_code == 200

    overridden = _evaluate(client, tool_name="restart_pod", execution_class="mutate")
    assert overridden == {"decision": "allow", "policy_id": "tenant-a-allow-restart"}

    # Other tenants still governed by the global default.
    other = _evaluate(client, tenant_id=TENANT_B, tool_name="restart_pod", execution_class="mutate")
    assert other == {"decision": "deny", "policy_id": DEFAULT_POLICY_ID}


def test_disabled_policy_ignored(client):
    response = _put_policy(client, "disabled-allow", enabled=False)
    assert response.status_code == 200

    outcome = _evaluate(client, tool_name="restart_pod", execution_class="mutate")
    assert outcome == {"decision": "deny", "policy_id": DEFAULT_POLICY_ID}


def test_evaluate_fail_closed_when_no_rule_matches(client):
    outcome = _evaluate(client, tool_name="get_pods", execution_class="admin")
    assert outcome == {"decision": "deny", "policy_id": None}


def test_environment_matching(client):
    response = _put_policy(
        client,
        "deny-logs-in-prod",
        rules=[{"execution_class": "read", "decision": "deny", "tools": ["get_logs"], "environments": ["prod"]}],
    )
    assert response.status_code == 200

    prod = _evaluate(client, tool_name="get_logs", execution_class="read", environment="prod")
    assert prod == {"decision": "deny", "policy_id": "deny-logs-in-prod"}

    # Rule skipped on environment mismatch; falls through to the global allow.
    dev = _evaluate(client, tool_name="get_logs", execution_class="read", environment="dev")
    assert dev == {"decision": "allow", "policy_id": DEFAULT_POLICY_ID}


# --------------------------------------------------------------------------- #
# PUT upsert + listing + validation
# --------------------------------------------------------------------------- #


def test_put_upsert_replaces_existing(client):
    assert _put_policy(client, "upserted", description="v1").status_code == 200
    assert _put_policy(client, "upserted", description="v2", enabled=False).status_code == 200

    policies = client.get("/v1/policies", params={"tenant_id": TENANT_A}).json()
    upserted = [p for p in policies if p["id"] == "upserted"]
    assert len(upserted) == 1
    assert upserted[0]["description"] == "v2"
    assert upserted[0]["enabled"] is False


def test_list_scoped_to_tenant_plus_global(client):
    assert _put_policy(client, "policy-a", tenant_id=TENANT_A).status_code == 200
    assert _put_policy(client, "policy-b", tenant_id=TENANT_B).status_code == 200

    ids = {p["id"] for p in client.get("/v1/policies", params={"tenant_id": TENANT_A}).json()}
    assert ids == {DEFAULT_POLICY_ID, "policy-a"}


@pytest.mark.parametrize(
    "body",
    [
        # invalid decision
        {"tenant_id": "t", "rules": [{"execution_class": "read", "decision": "maybe", "tools": ["*"]}]},
        # invalid execution_class
        {"tenant_id": "t", "rules": [{"execution_class": "write", "decision": "allow", "tools": ["*"]}]},
        # no rules
        {"tenant_id": "t", "rules": []},
        # missing tenant_id
        {"rules": [{"execution_class": "read", "decision": "allow", "tools": ["*"]}]},
        # empty tool set
        {"tenant_id": "t", "rules": [{"execution_class": "read", "decision": "allow", "tools": []}]},
    ],
)
def test_put_validation_errors_are_422(client, body):
    response = client.put("/v1/policies/some-policy", json=body)
    assert response.status_code == 422


def test_evaluate_validation_error_is_422(client):
    response = client.post("/v1/policies:evaluate", json={"tenant_id": TENANT_A})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# assert_tool_allowed: persisted backing + fail-closed fallback
# --------------------------------------------------------------------------- #


def test_assert_tool_allowed_backed_by_persisted_policies(settings_env):
    init_db()
    seed_default_policies()

    assert_tool_allowed("get_pods", "read")
    with pytest.raises(PolicyDenied):
        assert_tool_allowed("restart_pod", "mutate")

    # Replace the seed posture: disable it, then allow one mutate tool.
    with session_scope() as session:
        seed = session.get(Policy, DEFAULT_POLICY_ID)
        seed.enabled = False
        session.add(seed)
        session.add(
            Policy(
                id="allow-restart",
                tenant_id=GLOBAL_TENANT,
                rules=[
                    {"execution_class": "mutate", "decision": "allow", "tools": ["restart_pod"], "environments": ["*"]}
                ],
            )
        )

    assert_tool_allowed("restart_pod", "mutate")  # allowed by the persisted policy
    with pytest.raises(PolicyDenied):
        assert_tool_allowed("delete_namespace", "mutate")  # fail-closed: no rule matches


def test_assert_tool_allowed_static_default_when_store_unavailable(settings_env):
    # No init_db: the policy table does not exist, so the static M0 default
    # (allow read, deny everything else) must govern.
    assert_tool_allowed("get_pods", "read")
    with pytest.raises(PolicyDenied):
        assert_tool_allowed("restart_pod", "mutate")


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #


def test_policy_migration_file_imports_cleanly():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0002_policy_tables.py"
    spec = importlib.util.spec_from_file_location("policy_migration_0002", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0002_policy_tables"
    assert module.down_revision == "0001_initial_schema"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
