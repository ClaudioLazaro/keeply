"""The adaptive plan, and the contract that makes it acceptable.

Gathering adaptively means two runs of the same incident may collect
different things. For a product whose whole discipline is auditability that
is expensive, and the recorded reason is what buys it back.
"""

import pytest

from aiops_api.modules.specialists.plan import (
    STAGE_ORDER,
    Plan,
    Stage,
    absorb,
    order_specialists,
    should_enter,
    stage_of,
)


class _Spec:
    def __init__(self, name):
        self.name = name


def test_specialists_run_in_the_order_that_narrows():
    """Anchor before locate before narrow: each step decides the next."""
    ordered = order_specialists(tuple(_Spec(n) for n in ["jira", "kubernetes", "datadog", "argocd"]))
    assert [s.name for s in ordered] == ["datadog", "kubernetes", "argocd", "jira"]


def test_an_unmapped_specialist_runs_last_rather_than_never():
    """A new integration should add evidence, not silently disappear."""
    assert stage_of("something-new") is Stage.CONTEXT
    ordered = order_specialists(tuple(_Spec(n) for n in ["something-new", "datadog"]))
    assert [s.name for s in ordered] == ["datadog", "something-new"]


def test_every_step_records_why_it_was_taken():
    """The contract from ADR-0009. Without it this is a black box."""
    plan = Plan()
    ok, why = should_enter(Stage.ANCHOR, plan, budget_used=0, budget_limit=50)
    plan.record(Stage.ANCHOR, "datadog", taken=ok, reason=why)
    assert plan.steps[0].reason
    assert "datadog" in plan.narrative()


def test_a_skipped_source_is_recorded_not_silent():
    """A source that was skipped is a hole in the evidence; the operator has
    to be able to see it and the reason."""
    plan = Plan()
    plan.facts["failing_service"] = "identity-svc"
    ok, why = should_enter(Stage.CONTEXT, plan, budget_used=0, budget_limit=50)
    assert ok is False
    assert "identity-svc" in why
    plan.record(Stage.CONTEXT, "jira", taken=False, reason=why)
    assert "skipped jira" in plan.narrative()


def test_the_budget_stops_the_plan_and_says_so():
    plan = Plan()
    ok, why = should_enter(Stage.NARROW, plan, budget_used=50, budget_limit=50)
    assert ok is False
    assert "budget exhausted" in why


def test_a_known_failing_hop_only_skips_the_broader_later_stages():
    """Narrowing must still happen — that is where the answer is."""
    plan = Plan()
    plan.facts["failing_service"] = "identity-svc"
    assert should_enter(Stage.NARROW, plan, budget_used=0, budget_limit=50)[0] is True
    assert should_enter(Stage.TRACE, plan, budget_used=0, budget_limit=50)[0] is True
    assert should_enter(Stage.DEPEND, plan, budget_used=0, budget_limit=50)[0] is False


def test_nothing_is_skipped_on_absence_of_findings():
    """Stopping early because we found nothing would be concluding from
    silence — the failure the abstention work exists to prevent."""
    plan = Plan()
    for stage in STAGE_ORDER:
        assert should_enter(stage, plan, budget_used=0, budget_limit=50)[0] is True


def test_the_plan_absorbs_the_facts_that_steer_later_stages():
    class _Call:
        def __init__(self, result):
            self.result = result

    class _Result:
        calls = [_Call({"failing_service": "identity-svc", "trace_id": "abc123"})]
        extra_evidence = {"pod_name": "payment-api-7d9f"}

    plan = Plan()
    absorb(plan, "datadog", _Result())
    assert plan.facts["failing_service"] == "identity-svc"
    assert plan.facts["trace_id"] == "abc123"
    assert plan.facts["failing_service_source"] == "datadog"


def test_the_first_source_to_name_the_failing_hop_wins():
    """Later sources must not overwrite a fact that already steered a branch,
    or the recorded reason would stop matching what happened."""
    class _Call:
        def __init__(self, result):
            self.result = result

    class _Result:
        def __init__(self, service):
            self.calls = [_Call({"failing_service": service})]
            self.extra_evidence = {}

    plan = Plan()
    absorb(plan, "datadog", _Result("identity-svc"))
    absorb(plan, "kubernetes", _Result("something-else"))
    assert plan.facts["failing_service"] == "identity-svc"
