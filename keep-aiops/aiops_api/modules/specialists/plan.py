"""Gather by following what the incident reveals, not by sweeping a roster.

The coordinator ran every applicable specialist, in sequence, with the same
arguments, always. That works with three stub tools. With ten real sources it
breaks three ways at once: the wall budget goes on serial timeouts, irrelevant
sources crowd the evidence — and now the prompt too, competing for context —
and the ordering information is thrown away. ArgoCD saying a deploy landed
eight minutes ago changes *which* logs are worth reading, and nothing carried
that between specialists.

The plan implements ADR-0009. Its contract, and the reason it is safe to run
something non-deterministic in a product built on auditability:

    **every step records why it was taken.**

Two runs of the same incident may gather differently. That is the cost. The
recorded reason is what makes the difference inspectable instead of
mysterious — the same move as ``matched_by`` on workload discovery and
``caveat`` on a discounted hypothesis. Without it, adaptive gathering is a
black box, and the black box is exactly what this product exists not to be.

Stages are ordered by what narrows the next, not by what is easy:

    anchor    → what fired, on which service, in which window
    locate    → where that service runs
    trace     → which hop actually failed
    narrow    → logs and state for that hop
    change    → did something deploy into the window
    depend    → the datastores and queues that hop touched
    context   → known issues and planned changes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    ANCHOR = "anchor"
    LOCATE = "locate"
    TRACE = "trace"
    NARROW = "narrow"
    CHANGE = "change"
    DEPEND = "depend"
    CONTEXT = "context"


# Declared once, in the order that narrows. A specialist declares which stage
# it serves; the plan decides whether that stage is still worth entering.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.ANCHOR,
    Stage.LOCATE,
    Stage.TRACE,
    Stage.NARROW,
    Stage.CHANGE,
    Stage.DEPEND,
    Stage.CONTEXT,
)

# Which specialist serves which stage. A specialist absent here runs last, in
# CONTEXT — unknown beats excluded, since a new integration should add
# evidence rather than silently never run.
SPECIALIST_STAGE: dict[str, Stage] = {
    "datadog": Stage.ANCHOR,
    "backstage": Stage.LOCATE,
    "kubernetes": Stage.NARROW,
    "argocd": Stage.CHANGE,
    "bitbucket": Stage.CHANGE,
    "aws_rds": Stage.DEPEND,
    "aws_eks": Stage.DEPEND,
    "prometheus": Stage.DEPEND,
    "jira": Stage.CONTEXT,
    "slack": Stage.CONTEXT,
}


@dataclass
class Step:
    """One decision the plan made, and why.

    ``reason`` is not logging. It is persisted beside the evidence, because an
    operator reading a surprising investigation needs to see the path, not
    just the destination.
    """

    stage: Stage
    specialist: str
    taken: bool
    reason: str
    findings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """The record of an adaptive run."""

    steps: list[Step] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def record(self, stage: Stage, specialist: str, taken: bool, reason: str, **findings) -> None:
        self.steps.append(
            Step(stage=stage, specialist=specialist, taken=taken, reason=reason, findings=findings)
        )

    def narrative(self) -> str:
        """The plan as evidence text, in the order it happened."""
        lines = []
        for step in self.steps:
            verb = "queried" if step.taken else "skipped"
            lines.append(f"{verb} {step.specialist} ({step.stage.value}): {step.reason}")
        return "; ".join(lines)

    @property
    def stages_entered(self) -> list[str]:
        return [s.stage.value for s in self.steps if s.taken]


def stage_of(specialist_name: str) -> Stage:
    return SPECIALIST_STAGE.get(specialist_name, Stage.CONTEXT)


def order_specialists(specialists: tuple[Any, ...]) -> list[Any]:
    """Sort by stage, keeping the registry's order inside a stage.

    Stable within a stage so two runs that take the same branches produce the
    same sequence — the non-determinism should come from the incident, not
    from iteration order.
    """
    position = {stage: index for index, stage in enumerate(STAGE_ORDER)}
    return sorted(specialists, key=lambda s: position[stage_of(s.name)])


def should_enter(
    stage: Stage,
    plan: Plan,
    *,
    budget_used: float,
    budget_limit: float,
) -> tuple[bool, str]:
    """Whether the next stage is still worth entering, and why or why not.

    Two ways to stop. The budget is the hard one. The soft one is having
    already found the failing hop: once a trace names the service that
    errored, sweeping dependency and context sources adds cost and noise
    without changing the conclusion.

    Deliberately conservative — it only skips the *later, broader* stages, and
    only on a positive finding. Stopping early on absence would be the system
    concluding from silence, which is the failure the abstention work exists
    to prevent.
    """
    if budget_limit > 0 and budget_used >= budget_limit:
        return False, (
            f"budget exhausted ({budget_used:.0f}/{budget_limit:.0f} tool calls) "
            "before this stage"
        )

    failing = plan.facts.get("failing_service")
    if failing and stage in (Stage.DEPEND, Stage.CONTEXT):
        return False, (
            f"the trace already named {failing} as the failing hop; broader "
            "sources would add cost without changing the conclusion"
        )

    return True, f"entering {stage.value}"


def absorb(plan: Plan, specialist_name: str, result: Any) -> None:
    """Take from a result the facts that steer later stages.

    Only three, on purpose. A plan that accumulates everything becomes a
    second, undocumented state machine; these are the ones that demonstrably
    change what to do next.
    """
    extra = getattr(result, "extra_evidence", None) or {}

    for call in getattr(result, "calls", []) or []:
        payload = call.result if isinstance(getattr(call, "result", None), dict) else {}

        # Which hop failed — the fact that makes narrowing possible at all.
        failing = payload.get("failing_service")
        if failing and not plan.facts.get("failing_service"):
            plan.facts["failing_service"] = failing
            plan.facts["failing_service_source"] = specialist_name

        # The correlation key that turns "logs of the worst-looking pod" into
        # "logs of the call that failed".
        trace_id = payload.get("trace_id")
        if trace_id and not plan.facts.get("trace_id"):
            plan.facts["trace_id"] = trace_id

    for key in ("pod_name", "pod_namespace"):
        if extra.get(key) and not plan.facts.get(key):
            plan.facts[key] = extra[key]
