"""Aiming evidence gathering at the incident instead of at the whole cluster."""

import pytest

from aiops_api.modules.specialists.base import Budget, Scope
from aiops_api.modules.specialists.builtin import KubernetesSpecialist
from aiops_api.modules.specialists.scope import from_context_pack
from aiops_api.modules.specialists.tracker import BudgetTracker


def _tracker():
    return BudgetTracker.start(Budget(tool_calls=50, wall_time=60, llm_tokens=0))


# Mirrors what the MCP server publishes: namespace is an accepted argument.
CATALOG = {
    tool: {
        "name": tool,
        "execution_class": "read",
        "input_schema": {
            "properties": {"cluster": {}, "namespace": {}, "pod": {}},
            "required": ["cluster"],
        },
    }
    for tool in ("get_pods", "get_events", "get_logs")
}


# --------------------------------------------------------------------------- #
# Deriving the scope
# --------------------------------------------------------------------------- #


def test_services_come_from_the_incident():
    scope = from_context_pack({"incident": {"services": ["payments", "checkout"]}})
    assert scope.services == ("payments", "checkout")
    assert scope.namespaces == ("payments", "checkout")
    assert scope.derived


def test_alerts_supply_services_when_the_incident_lists_none():
    """Correlation-created incidents often carry no services of their own."""
    scope = from_context_pack(
        {"incident": {}, "alerts": [{"service": "payments"}, {"service": "payments"}]}
    )
    assert scope.services == ("payments",)


def test_namespace_mapping_overrides_the_name_guess(monkeypatch):
    """Service name as namespace is a convention, not a fact."""
    monkeypatch.setenv("AIOPS_SERVICE_NAMESPACE_MAP", '{"payments": "prod-payments"}')
    scope = from_context_pack({"incident": {"services": ["payments", "checkout"]}})
    assert scope.namespaces == ("prod-payments", "checkout")


def test_namespaces_are_capped_so_one_incident_cannot_eat_the_budget():
    services = [f"svc-{i}" for i in range(10)]
    scope = from_context_pack({"incident": {"services": services}}, max_namespaces=3)
    assert len(scope.namespaces) == 3


def test_an_incident_with_nothing_to_aim_at_is_reported_as_underived():
    """The caller has to be able to tell "no scope" from "scope is empty"."""
    assert not from_context_pack({"incident": {}}).derived
    assert not from_context_pack(None).derived


def test_a_broken_namespace_map_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setenv("AIOPS_SERVICE_NAMESPACE_MAP", "{not json")
    scope = from_context_pack({"incident": {"services": ["payments"]}})
    assert scope.namespaces == ("payments",)


# --------------------------------------------------------------------------- #
# Using it
# --------------------------------------------------------------------------- #


def _recording_invoke(calls):
    def invoke(tool, arguments):
        calls.append((tool, arguments))
        if tool == "get_pods":
            return {"backend": "live", "pods": [{"name": "p-1", "namespace": arguments.get("namespace", "x")}]}, None
        if tool == "get_events":
            return {"backend": "live", "events": []}, None
        return {"backend": "live", "lines": ["boom"]}, None

    return invoke


def test_kubernetes_queries_each_namespace_the_incident_points_at():
    """The sweep is what let a stranger's failing pod become our evidence."""
    seen: list = []
    KubernetesSpecialist().gather(
        catalog=CATALOG,
        invoke=_recording_invoke(seen),
        budget=Budget(),
        used=_tracker(),
        scope=Scope(cluster="prod", namespaces=("payments", "checkout")),
    )
    queried = {args.get("namespace") for tool, args in seen if tool == "get_pods"}
    assert queried == {"payments", "checkout"}
    # And never a namespace-less sweep alongside them.
    assert None not in queried


def test_an_unscoped_sweep_is_labelled_so_it_cannot_read_as_targeted():
    """Capability is kept; the weakness is made visible instead of hidden."""
    seen: list = []
    result = KubernetesSpecialist().gather(
        catalog=CATALOG,
        invoke=_recording_invoke(seen),
        budget=Budget(),
        used=_tracker(),
        scope=Scope(cluster="prod"),  # nothing to aim at
    )
    pods = next(c for c in result.calls if c.tool == "get_pods")
    assert "UNSCOPED" in pods.summary
    assert "named no service" in pods.summary


def test_cluster_is_passed_whenever_the_tool_requires_it(monkeypatch):
    monkeypatch.setenv("AIOPS_MCP_DEFAULT_CLUSTER", "prod-eu")
    from aiops_api.settings import get_settings

    get_settings.cache_clear()
    seen: list = []
    KubernetesSpecialist().gather(
        catalog=CATALOG,
        invoke=_recording_invoke(seen),
        budget=Budget(),
        used=_tracker(),
        scope=Scope(namespaces=("payments",)),
    )
    get_settings.cache_clear()
    assert all(args.get("cluster") == "prod-eu" for _tool, args in seen)


def test_legacy_tools_without_a_schema_are_still_scoped():
    """The legacy gateway published no schema but accepted a namespace."""
    seen: list = []
    KubernetesSpecialist().gather(
        catalog={t: {"name": t, "execution_class": "read"} for t in ("get_pods", "get_events", "get_logs")},
        invoke=_recording_invoke(seen),
        budget=Budget(),
        used=_tracker(),
        scope=Scope(namespaces=("payments",)),
    )
    assert any(args.get("namespace") == "payments" for _tool, args in seen)
    # ...and no cluster, since that schema does not require one.
    assert all("cluster" not in args for _tool, args in seen)
