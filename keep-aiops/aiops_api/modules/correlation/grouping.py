"""Group alerts into correlation candidates.

Single-link clustering over the similarity graph, bounded by an arrival
window: an alert joins a group when it is similar enough to *any* member,
which is what lets a cascade (api → db → cache) land as one incident even
though the first and last alert barely resemble each other.

Single-link is the right shape here and also the risky one — it chains, so
one over-similar pair can pull two unrelated groups together. Three things
bound that: the time window, the similarity threshold, and a hard cap on
group size. A group that hits the cap is dropped rather than truncated,
because a 50-alert group almost always means the thresholds are wrong, not
that one incident has 50 symptoms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from aiops_api.modules.correlation.similarity import alert_time, similarity


@dataclass
class CorrelationGroup:
    """A set of alerts proposed as one incident, and why."""

    alerts: list[Any] = field(default_factory=list)
    # Highest pairwise score in the group — how sure we are overall.
    confidence: float = 0.0
    # Human-readable reasons, deduped, in the order they were found.
    reasons: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.alerts)

    def fingerprints(self) -> list[str]:
        out: list[str] = []
        for alert in self.alerts:
            value = (
                alert.get("fingerprint")
                if isinstance(alert, dict)
                else getattr(alert, "fingerprint", None)
            )
            if value:
                out.append(str(value))
        return out

    def explain(self) -> str:
        """The audit line recorded on the incident."""
        head = f"{self.size} alerts correlated at {self.confidence:.0%} confidence"
        if not self.reasons:
            return head
        return f"{head} — {'; '.join(self.reasons)}"


def group_alerts(
    alerts: list[Any],
    *,
    window_minutes: float,
    similarity_threshold: float,
    max_group_size: int,
) -> list[CorrelationGroup]:
    """Cluster alerts into correlation candidates.

    Only groups of 2+ are returned: a lone alert needs no correlating, and
    proposing one would just be noise on every incident.
    """
    window = timedelta(minutes=window_minutes)

    # Chronological order makes the window check a simple forward scan and
    # keeps grouping deterministic for the same input.
    ordered = sorted(
        (alert for alert in alerts if alert_time(alert) is not None),
        key=lambda alert: alert_time(alert),  # type: ignore[arg-type]
    )

    groups: list[CorrelationGroup] = []
    for alert in ordered:
        when = alert_time(alert)
        placed = False

        for group in groups:
            # Window is measured against the group's most recent member, so
            # a steady drip of related alerts keeps one incident open
            # instead of starting a new one every window.
            latest = max(alert_time(member) for member in group.alerts)  # type: ignore[type-var]
            if when - latest > window:  # type: ignore[operator]
                continue

            best = None
            for member in group.alerts:
                score = similarity(alert, member)
                if best is None or score.value > best.value:
                    best = score
            if best is None or best.value < similarity_threshold:
                continue

            group.alerts.append(alert)
            group.confidence = max(group.confidence, best.value)
            for reason in best.reasons:
                if reason not in group.reasons:
                    group.reasons.append(reason)
            placed = True
            break

        if not placed:
            groups.append(CorrelationGroup(alerts=[alert]))

    # Oversized groups are dropped, not trimmed: picking which alerts to
    # keep would be arbitrary, and the operator needs to see that the
    # thresholds let something run away.
    return [
        group
        for group in groups
        if 2 <= group.size <= max_group_size
    ]
