"""Alert similarity — the decision of whether two alerts are one problem.

Deliberately deterministic and explainable. A correlation that merges two
unrelated incidents hides a real outage inside another one, so every score
carries the reasons that produced it: an operator seeing a wrong grouping
can read exactly which signal caused it and tune that setting, rather than
being told a model decided.

Signals, in descending trustworthiness:

* same service          — the strongest signal Keep carries; two alerts on
                          one service during one window are usually one story
* shared source         — same monitoring system seeing the same thing
* fingerprint overlap   — Keep's own dedup identity, so an exact match means
                          the same rule fired twice
* name/description text — weakest; token overlap catches "high latency" vs
                          "latency degraded" but also produces false friends
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# Weights sum to 1.0 so the score reads as a probability-ish number in the
# UI. Service dominates on purpose — it is the only signal that reliably
# means "same blast radius".
WEIGHT_SERVICE = 0.45
WEIGHT_SOURCE = 0.15
WEIGHT_FINGERPRINT = 0.20
WEIGHT_TEXT = 0.20

# Evidence *against* two alerts being the same problem.
#
# Same service plus same source scores exactly the default threshold, which
# is deliberate calibration — and on a broad service bucket it is wrong.
# 955 alerts arrived under `service: elasticache`, which names the
# technology, not which cluster is unwell; every pair of them cleared the
# bar and would have been proposed as one incident.
#
# A bonus for a *shared* resource does not fix that: the pair already
# cleared the bar without it. What was missing is the negative case — two
# alerts that each name a resource, and name different ones. Subtracting
# rather than adding leaves every existing calibration intact and only
# removes groupings that the data itself contradicts.
WEIGHT_RESOURCE_CONFLICT = 0.25

# Values that mean "we do not know", not "these are the same thing".
#
# On a real Datadog account 413 of 3000 alerts carried `service: undefined`.
# Treated as a value, every one of them scored a full service match against
# every other — the strongest signal in the model, awarded for a shared
# absence of information. A placeholder is the opposite of an identity.
_PLACEHOLDERS = frozenset(
    {"undefined", "unknown", "none", "null", "n/a", "na", "-", "unspecified"}
)

# Where the specific unwell thing is named, in preference order. Read from
# `tags`, not `labels`: on a real account `labels` was empty on all 9936
# alerts while these tags were populated.
_RESOURCE_KEYS = ("host", "resource_name", "cacheclusterid", "pod_name", "instance")

# Words that appear in almost every alert and would inflate text overlap.
_STOPWORDS = frozenset(
    {
        "alert", "alerts", "the", "a", "an", "is", "are", "was", "were", "on",
        "in", "for", "of", "to", "and", "or", "with", "high", "low", "error",
        "errors", "warning", "critical", "failed", "failure", "detected",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class SimilarityScore:
    """A score plus why it is what it is."""

    value: float
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.value > 0


def _get(alert: Any, *names: str) -> Any:
    for name in names:
        if isinstance(alert, dict):
            value = alert.get(name)
        else:
            value = getattr(alert, name, None)
        if value:
            return value
    return None


def _tags(alert: Any) -> dict:
    """The alert's tag map, whichever shape the caller uses."""
    tags = _get(alert, "tags", "labels")
    return tags if isinstance(tags, dict) else {}


def _from_tags(alert: Any, keys: tuple[str, ...]) -> Any:
    """First present tag among `keys`, in preference order."""
    tags = _tags(alert)
    for key in keys:
        value = tags.get(key)
        if value:
            return value
    return None


def _as_set(value: Any) -> set[str]:
    """Keep sends services/sources as either a list or a bare string.

    Placeholder values are dropped here rather than at each call site, so a
    dimension added later cannot forget the rule.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        cleaned = value.strip().lower()
        return {cleaned} if cleaned and cleaned not in _PLACEHOLDERS else set()
    if isinstance(value, Iterable):
        return {
            cleaned
            for cleaned in (str(item).strip().lower() for item in value)
            if cleaned and cleaned not in _PLACEHOLDERS
        }
    return set()


def _tokens(alert: Any) -> set[str]:
    text = " ".join(
        str(_get(alert, field_name) or "")
        for field_name in ("name", "description", "message")
    ).lower()
    return {token for token in _TOKEN.findall(text) if token not in _STOPWORDS and len(token) > 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def alert_time(alert: Any) -> datetime | None:
    """Best-effort arrival time, always timezone-aware.

    Naive values are read as UTC, which is what Keep stores. Returning them
    as-is meant one provider emitting a timestamp without an offset made
    every comparison against an aware one raise, and since grouping sorts
    and windows across the whole batch, a single such alert took down the
    entire tenant's correlation run rather than just being skipped.
    """
    raw = _get(alert, "lastReceived", "last_received", "timestamp", "created_at")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def similarity(left: Any, right: Any) -> SimilarityScore:
    """Score two alerts in [0, 1] with the reasons behind the number."""
    score = 0.0
    reasons: list[str] = []

    left_services = _as_set(_get(left, "service", "services"))
    right_services = _as_set(_get(right, "service", "services"))
    shared_services = left_services & right_services
    if shared_services:
        score += WEIGHT_SERVICE
        reasons.append(f"same service ({', '.join(sorted(shared_services))})")

    left_sources = _as_set(_get(left, "source", "sources"))
    right_sources = _as_set(_get(right, "source", "sources"))
    if left_sources & right_sources:
        score += WEIGHT_SOURCE
        reasons.append(f"same source ({', '.join(sorted(left_sources & right_sources))})")

    left_fp = str(_get(left, "fingerprint") or "")
    right_fp = str(_get(right, "fingerprint") or "")
    if left_fp and left_fp == right_fp:
        score += WEIGHT_FINGERPRINT
        reasons.append("identical fingerprint")

    text_overlap = _jaccard(_tokens(left), _tokens(right))
    if text_overlap > 0:
        score += WEIGHT_TEXT * text_overlap
        if text_overlap >= 0.3:
            shared = sorted(_tokens(left) & _tokens(right))[:4]
            reasons.append(f"wording overlap ({', '.join(shared)})")

    # Applied last, and only when both sides actually name a resource.
    # Silence is not disagreement: an alert with no host tag says nothing
    # about whether it is the same box, and penalising it would punish
    # sparse metadata instead of contradictory metadata.
    left_resource = _as_set(_from_tags(left, _RESOURCE_KEYS))
    right_resource = _as_set(_from_tags(right, _RESOURCE_KEYS))
    if left_resource and right_resource and not (left_resource & right_resource):
        score -= WEIGHT_RESOURCE_CONFLICT
        reasons.append(
            f"different resource ({', '.join(sorted(left_resource))} vs "
            f"{', '.join(sorted(right_resource))})"
        )

    return SimilarityScore(value=round(max(min(score, 1.0), 0.0), 3), reasons=reasons)
