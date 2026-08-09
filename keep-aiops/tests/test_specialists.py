"""M3 specialists: each specialist invokes its declared tools in order,
records evidence gaps when a tool raises, and never raises itself.
"""

from types import SimpleNamespace

import pytest

from aiops_api.modules.specialists.base import Budget, Scope
from aiops_api.modules.specialists.builtin import (
    ArgoCdSpecialist,
    AwsEksSpecialist,
    AwsRdsSpecialist,
    BackstageSpecialist,
    BitbucketSpecialist,
    DatadogSpecialist,
    JiraSpecialist,
    KubernetesSpecialist,
    PrometheusSpecialist,
    SlackSpecialist,
)
from aiops_api.modules.specialists.tracker import BudgetTracker


def _tracker() -> BudgetTracker:
    return BudgetTracker.start(Budget(tool_calls=1000, wall_time=1000.0, llm_tokens=1_000_000))


def _invoke_ok(result):
    def _f(tool, args):
        return result, "audit-test"
    return _f


def _invoke_raise(message: str):
    def _f(tool, args):
        raise RuntimeError(message)
    return _f


def test_kubernetes_specialist_chains_pod_to_logs():
    calls: list[tuple[str, dict]] = []

    def invoke(tool, args):
        calls.append((tool, args))
        if tool == "get_pods":
            return {"pods": [{"name": "payment-api-7d9f"}]}, "audit"
        if tool == "get_events":
            return {"events": [{"reason": "BackOff"}]}, "audit"
        if tool == "get_logs":
            return {"lines": ["OutOfMemoryError"]}, "audit"
        raise AssertionError(f"unexpected tool {tool}")

    spec = KubernetesSpecialist()
    result = spec.gather(catalog={}, invoke=invoke, budget=Budget(), used=_tracker(), scope=Scope())
    assert [c[0] for c in calls] == ["get_pods", "get_events", "get_logs"]
    assert result.extra_evidence["pod_name"] == "payment-api-7d9f"
    assert all(not call.is_gap for call in result.calls)
    assert "payment-api-7d9f" in result.calls[2].arguments["pod"]


def test_specialist_converts_tool_failure_to_evidence_gap():
    spec = KubernetesSpecialist()
    result = spec.gather(
        catalog={},
        invoke=_invoke_raise("boom"),
        budget=Budget(),
        used=_tracker(),
            scope=Scope(),
    )
    assert all(call.is_gap for call in result.calls)
    assert "boom" in result.calls[0].error


def test_specialist_charges_tool_call_budget():
    spec = JiraSpecialist()
    tracker = _tracker()
    spec.gather(catalog={}, invoke=_invoke_ok({"issues": []}), budget=Budget(), used=tracker, scope=Scope())
    assert tracker.tool_calls == len(spec.tools)


@pytest.mark.parametrize(
    "spec,expected_tools",
    [
        (KubernetesSpecialist(), ("get_pods", "get_events", "get_logs", "find_workload")),
        (PrometheusSpecialist(), ("prom_alerts", "prom_query", "prom_query_range")),
        (DatadogSpecialist(), ("dd_query_metrics", "dd_list_events")),
        (AwsEksSpecialist(), ("eks_list_clusters", "eks_describe_nodegroups")),
        (AwsRdsSpecialist(), ("rds_list_instances", "rds_describe_instance_status")),
        (ArgoCdSpecialist(), ("argocd_list_apps", "argocd_get_app")),
        (JiraSpecialist(), ("jira_search_issues",)),
        (SlackSpecialist(), ("slack_search_messages",)),
        (BitbucketSpecialist(), ("bb_list_recent_commits", "bb_list_open_pull_requests")),
        (BackstageSpecialist(), ("backstage_get_entity",)),
    ],
)
def test_specialist_declares_expected_tools(spec, expected_tools):
    assert spec.tools == expected_tools
    # Stable name, used as the span attribute on the coordinator.
    assert isinstance(spec.name, str) and spec.name


def test_specialist_summary_present_on_success():
    spec = BackstageSpecialist()
    result = spec.gather(
        catalog={},
        invoke=_invoke_ok({"entity": {"name": "payment-api"}}),
        budget=Budget(),
        used=_tracker(),
            scope=Scope(),
    )
    assert result.calls[0].summary is not None
    assert "backstage_get_entity" in result.calls[0].summary
