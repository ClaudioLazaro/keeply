"""Keeply Alert Correlation.

Auto-merge is destructive: a wrong grouping buries a real incident inside
another one. These tests pin the boundaries that keep that from happening
silently — the window, the threshold, the size cap, and the audit trail.
"""

from datetime import datetime, timedelta, timezone

import pytest

from aiops_api.modules.correlation.grouping import group_alerts
from aiops_api.modules.correlation.similarity import similarity

BASE = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)


def alert(
    name: str,
    *,
    service: str | None = "payment-api",
    source: str = "prometheus",
    minute: int = 0,
    fingerprint: str | None = None,
    severity: str = "critical",
) -> dict:
    return {
        "name": name,
        "description": name,
        "service": service,
        "source": [source],
        "severity": severity,
        "fingerprint": fingerprint or f"fp-{name}",
        "lastReceived": (BASE + timedelta(minutes=minute)).isoformat(),
    }


def grouped(alerts, **overrides):
    params = {
        "window_minutes": 10,
        "similarity_threshold": 0.6,
        "max_group_size": 20,
    }
    params.update(overrides)
    return group_alerts(alerts, **params)


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #


def test_same_service_dominates_the_score():
    score = similarity(alert("5xx rate rising"), alert("latency degraded"))

    assert score.value >= 0.45
    assert any("same service" in reason for reason in score.reasons)


def test_different_services_do_not_correlate_on_wording_alone():
    """Two services with similar symptoms are two problems until something
    stronger than vocabulary links them."""
    score = similarity(
        alert("high latency detected", service="payment-api"),
        alert("high latency detected", service="billing-api", source="datadog"),
    )

    assert score.value < 0.6


def test_identical_fingerprint_is_recognised():
    score = similarity(
        alert("pod restart", fingerprint="fp-shared"),
        alert("pod restart again", fingerprint="fp-shared"),
    )

    assert any("identical fingerprint" in reason for reason in score.reasons)


def test_every_score_carries_its_reasons():
    """A grouping an operator cannot explain is one they cannot trust."""
    score = similarity(alert("5xx rate"), alert("5xx rate climbing"))

    assert score.reasons
    assert all(isinstance(reason, str) and reason for reason in score.reasons)


def test_alerts_without_shared_signals_score_near_zero():
    score = similarity(
        alert("disk pressure", service="storage", source="kubernetes"),
        alert("certificate expiring", service="ingress", source="cert-manager"),
    )

    assert score.value < 0.3


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def test_related_alerts_in_the_window_become_one_group():
    groups = grouped(
        [
            alert("5xx rate rising", minute=0),
            alert("latency degraded", minute=1),
            alert("error budget burn", minute=2),
        ]
    )

    assert len(groups) == 1
    assert groups[0].size == 3


def test_alerts_outside_the_window_stay_separate():
    groups = grouped(
        [alert("5xx rate rising", minute=0), alert("5xx rate rising again", minute=45)]
    )

    assert groups == []


def test_a_lone_alert_is_never_a_group():
    """Correlating one alert would just add noise to every incident."""
    assert grouped([alert("only one")]) == []


def test_unrelated_services_produce_no_group():
    groups = grouped(
        [
            alert("payment 5xx", service="payment-api", minute=0),
            alert("cert expiring", service="ingress", source="cert-manager", minute=1),
        ]
    )

    assert groups == []


def test_threshold_controls_how_eagerly_alerts_merge():
    alerts = [
        alert("payment 5xx", service="payment-api", minute=0),
        alert("payment db slow", service="payment-db", source="prometheus", minute=1),
    ]

    assert grouped(alerts, similarity_threshold=0.9) == []
    assert len(grouped(alerts, similarity_threshold=0.2)) == 1


def test_oversized_groups_are_dropped_not_truncated():
    """A 50-alert group means the thresholds are wrong; silently keeping
    the first N would hide that and merge arbitrary alerts."""
    alerts = [alert(f"symptom {i}", minute=i % 5) for i in range(30)]

    groups = grouped(alerts, max_group_size=10)

    assert all(group.size <= 10 for group in groups)


def test_window_slides_with_the_group_so_a_cascade_stays_together():
    """A steady drip of related alerts is one incident, not one per window."""
    alerts = [alert(f"cascade step {i}", minute=i * 8) for i in range(4)]

    groups = grouped(alerts, window_minutes=10)

    assert len(groups) == 1
    assert groups[0].size == 4


def test_group_confidence_is_the_strongest_link():
    groups = grouped(
        [
            alert("5xx rate", fingerprint="fp-same", minute=0),
            alert("5xx rate", fingerprint="fp-same", minute=1),
        ]
    )

    assert groups[0].confidence >= 0.8


def test_explanation_names_the_signals_that_caused_the_grouping():
    groups = grouped(
        [alert("5xx rate rising", minute=0), alert("5xx rate climbing", minute=1)]
    )

    explanation = groups[0].explain()
    assert "2 alerts correlated" in explanation
    assert "same service" in explanation


def test_grouping_is_deterministic_for_the_same_input():
    """The same alerts must always produce the same incidents — otherwise
    a rerun silently reshuffles what an operator already looked at."""
    alerts = [
        alert("b", minute=1),
        alert("a", minute=0),
        alert("c", minute=2),
    ]

    first = grouped(alerts)
    second = grouped(list(reversed(alerts)))

    assert [g.fingerprints() for g in first] == [g.fingerprints() for g in second]


def test_alerts_without_a_timestamp_are_ignored_not_guessed():
    """An alert with no arrival time cannot be windowed; correlating it
    would be a guess."""
    undated = alert("mystery")
    del undated["lastReceived"]

    groups = grouped([alert("5xx rate", minute=0), undated])

    assert groups == []


# --------------------------------------------------------------------------- #
# The Enabled toggle is the only thing that decides whether correlation runs
# --------------------------------------------------------------------------- #


def test_correlation_is_off_until_switched_on():
    """Analysis costs API calls against Keep, so it starts only when asked."""
    from aiops_api.modules.correlation.service import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["Enabled"] is False


def test_a_disabled_tenant_does_no_work(monkeypatch):
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    client = CorrelationClient(
        tenant_id="t1", back_api_url="http://keep.test", back_api_key="k"
    )
    monkeypatch.setattr(service, "fetch_settings", lambda _c: {**service.DEFAULT_SETTINGS, "Enabled": False})

    def _should_not_run(*args, **kwargs):
        raise AssertionError("alerts were fetched for a disabled tenant")

    monkeypatch.setattr(service, "fetch_recent_alerts", _should_not_run)

    assert service.run_for_client(client) == {"groups": 0, "proposed": 0}


def test_bool_settings_survive_the_round_trip_from_keep(monkeypatch):
    """Settings arrive as JSON; a bool must not be coerced into a float,
    which would make Enabled=False read as 0.0 and stay falsy by luck
    rather than by intent."""
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "algorithm_configs": [
                    {
                        "algorithm_id": service.ALGORITHM_ID,
                        "settings": [
                            {"name": "Enabled", "value": True},
                            {"name": "Similarity Threshold", "value": 0.75},
                        ],
                    }
                ]
            }

    class _Http:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(service, "_client_http", lambda _c: _Http())
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    settings = service.fetch_settings(client)

    assert settings["Enabled"] is True
    assert settings["Similarity Threshold"] == 0.75


# --------------------------------------------------------------------------- #
# Rule proposals — the algorithm's actual output
# --------------------------------------------------------------------------- #


def _proposals(groups, **kw):
    from aiops_api.modules.correlation.rules import propose_rules

    params = {"window_minutes": 10, "min_occurrences": 2}
    params.update(kw)
    return propose_rules(groups, **params)


def test_a_pattern_seen_once_is_not_a_rule():
    """One grouping is a coincidence. Proposing a rule from it would teach
    the engine to merge on a fluke."""
    groups = grouped([alert("5xx", minute=0), alert("latency", minute=1)])

    assert _proposals(groups) == []


def test_a_recurring_pattern_becomes_a_proposal():
    groups = grouped(
        [alert("5xx", minute=0), alert("latency", minute=1)]
    ) + grouped(
        [alert("5xx again", minute=0), alert("latency again", minute=1)]
    )

    proposals = _proposals(groups)

    assert len(proposals) == 1
    assert proposals[0].occurrences == 2
    assert "service == 'payment-api'" in proposals[0].cel


def test_proposal_groups_by_service_so_one_rule_is_not_one_incident():
    groups = grouped([alert("a", minute=0), alert("b", minute=1)]) * 2

    proposal = _proposals(groups)[0]

    assert proposal.grouping_criteria == ["service"]


def test_timeframe_comes_from_the_configured_window():
    groups = grouped([alert("a", minute=0), alert("b", minute=1)]) * 2

    proposal = _proposals(groups, window_minutes=25)[0]

    assert proposal.timeframe_seconds == 25 * 60


def test_proposal_carries_the_evidence_behind_it():
    """A rule an operator cannot justify is one they should not accept."""
    groups = grouped([alert("a", minute=0), alert("b", minute=1)]) * 3

    proposal = _proposals(groups)[0]

    assert proposal.occurrences == 3
    assert proposal.alerts_covered == 6
    assert "Seen 3 times" in proposal.rationale


def test_values_that_could_break_out_of_cel_are_refused():
    """Alert fields carry operator text; a quote in a service name must not
    produce a malformed — or differently-meaning — expression."""
    hostile = [
        alert("a", service="payment' || true || '", minute=0),
        alert("b", service="payment' || true || '", minute=1),
    ]
    groups = grouped(hostile) * 2

    assert _proposals(groups) == []


def test_a_group_without_a_dominant_service_produces_no_rule():
    """Correlating on wording alone would generate a rule that fires on
    vocabulary — exactly the rule that merges unrelated outages."""
    mixed = [
        alert("latency", service="a", minute=0),
        alert("latency", service="b", minute=1),
        alert("latency", service="c", minute=2),
    ]
    groups = grouped(mixed, similarity_threshold=0.1) * 2

    assert _proposals(groups) == []


def test_proposals_are_ordered_by_strength_of_evidence():
    strong = grouped([alert("a", minute=0), alert("b", minute=1)]) * 4
    weak = grouped(
        [
            alert("x", service="billing-api", minute=0),
            alert("y", service="billing-api", minute=1),
        ]
    ) * 2

    proposals = _proposals(strong + weak)

    assert [p.occurrences for p in proposals] == sorted(
        [p.occurrences for p in proposals], reverse=True
    )


def test_payload_gates_the_generated_rule_behind_approval():
    """A generated rule's first incidents are candidates a human confirms."""
    groups = grouped([alert("a", minute=0), alert("b", minute=1)]) * 2

    payload = _proposals(groups)[0].to_payload()

    assert payload["requireApprove"] is True
    assert payload["celQuery"].startswith("service == ")
