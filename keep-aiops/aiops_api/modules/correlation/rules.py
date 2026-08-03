"""Turn observed alert groupings into Keep correlation rules.

Keep already has a correlation engine: `/rules` runs CEL expressions
synchronously on the ingestion path, with grouping criteria, timeframes,
approval gating, auto-resolution and incident name templates. It is
strictly better at *executing* correlation than anything bolted on
afterwards — the one thing it cannot do is tell you which rule to write.

So that is the only job left here: watch the alert history, find the
groupings that keep recurring, and express them as rules an operator can
review. Execution stays entirely in Keep's engine, which means there is
exactly one path that creates incidents.

A proposal is only worth showing if the pattern repeated: a single
coincidence is not a rule.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from aiops_api.modules.correlation.grouping import CorrelationGroup

# A pattern seen fewer times than this is a coincidence, not a rule.
MIN_OCCURRENCES = 2

# CEL string literals are single-quoted; anything that could break out of
# one has no business in a generated rule.
_CEL_SAFE = re.compile(r"^[A-Za-z0-9._\-/ ]+$")


@dataclass
class RuleProposal:
    """A correlation rule derived from observed groupings."""

    name: str
    cel: str
    grouping_criteria: list[str] = field(default_factory=list)
    timeframe_seconds: int = 600
    # How many times this pattern was observed in the analysed history.
    occurrences: int = 0
    # Alerts the pattern covered, so an operator can judge the blast radius.
    alerts_covered: int = 0
    # Plain-language justification shown next to the proposal.
    rationale: str = ""
    # Alert names observed in this pattern. Not part of the rule — only
    # material for wording it in plain language.
    sample_names: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """Body for Keep's POST /rules/from-cel."""
        return {
            "ruleName": self.name,
            "celQuery": self.cel,
            "timeframeInSeconds": self.timeframe_seconds,
            "timeUnit": "seconds",
            "groupingCriteria": self.grouping_criteria,
            # Proposals arrive gated: the first incidents a generated rule
            # produces are candidates a human confirms, not facts.
            "requireApprove": True,
            "createOn": "any",
            "resolveOn": "never",
            "groupDescription": self.rationale,
        }


def _cel_literal(value: str) -> str | None:
    """Quote a value for CEL, or refuse it.

    Alert fields carry operator-supplied text. A service name with a quote
    in it would produce a malformed — or worse, a differently-meaning —
    expression, so unsafe values are dropped rather than escaped.
    """
    value = value.strip()
    if not value or not _CEL_SAFE.match(value):
        return None
    return f"'{value}'"


def _dominant(values: list[str], *, ratio: float = 0.8) -> str | None:
    """The value shared by most of a group, if one clearly dominates."""
    present = [v for v in values if v]
    if not present:
        return None
    value, count = Counter(present).most_common(1)[0]
    return value if count / len(present) >= ratio else None


def _field(alert: Any, *names: str) -> str:
    for name in names:
        value = alert.get(name) if isinstance(alert, dict) else getattr(alert, name, None)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value)
    return ""


def _signature(group: CorrelationGroup) -> tuple[str, str] | None:
    """What makes this group a group: (service, source).

    Only groups with a dominant service produce a rule. Correlating on
    wording alone would generate a rule that fires on vocabulary, which is
    exactly the kind of rule that merges unrelated outages.
    """
    service = _dominant([_field(a, "service", "services") for a in group.alerts])
    if not service:
        return None
    source = _dominant([_field(a, "source", "sources") for a in group.alerts]) or ""
    return service, source


def propose_rules(
    groups: list[CorrelationGroup],
    *,
    window_minutes: float,
    min_occurrences: int = MIN_OCCURRENCES,
) -> list[RuleProposal]:
    """Derive rule proposals from groupings observed over the history.

    Groups sharing a signature are evidence of the same recurring pattern;
    the proposal is emitted once, carrying how often it was seen.
    """
    by_signature: dict[tuple[str, str], list[CorrelationGroup]] = {}
    for group in groups:
        signature = _signature(group)
        if signature is not None:
            by_signature.setdefault(signature, []).append(group)

    proposals: list[RuleProposal] = []
    for (service, source), matched in sorted(by_signature.items()):
        if len(matched) < min_occurrences:
            continue

        service_literal = _cel_literal(service)
        if service_literal is None:
            continue

        conditions = [f"service == {service_literal}"]
        source_literal = _cel_literal(source) if source else None
        if source_literal:
            # `source` arrives as a list (["prometheus"]), so equality
            # against a string never matches and produces a rule that
            # silently never fires. `contains` is the list-safe form.
            conditions.append(f"source.contains({source_literal})")

        alerts_covered = sum(group.size for group in matched)
        seen_names: list[str] = []
        for group in matched:
            for alert in group.alerts:
                name = _field(alert, "name")
                if name and name not in seen_names:
                    seen_names.append(name)
        proposals.append(
            RuleProposal(
                name=f"{service} correlation",
                cel=" && ".join(conditions),
                # Grouping by service keeps one incident per service even
                # when the rule matches a wider set.
                grouping_criteria=["service"],
                timeframe_seconds=int(window_minutes * 60),
                occurrences=len(matched),
                alerts_covered=alerts_covered,
                rationale=(
                    f"Seen {len(matched)} "
                    f"{'time' if len(matched) == 1 else 'times'} covering "
                    f"{alerts_covered} "
                    f"{'alert' if alerts_covered == 1 else 'alerts'}. "
                    f"Alerts on {service}"
                    + (f" from {source}" if source else "")
                    + f" arriving within {int(window_minutes)} minutes were repeatedly "
                    "the same problem."
                ),
                sample_names=seen_names,
            )
        )

    # Most evidence first — that is the order an operator should review in.
    return sorted(proposals, key=lambda p: (-p.occurrences, -p.alerts_covered, p.name))
