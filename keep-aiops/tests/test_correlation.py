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


def test_source_uses_contains_because_alerts_carry_a_list():
    """`source` is a list on the alert, so `source == 'x'` never matches and
    would produce a rule that silently never fires."""
    groups = grouped(
        [alert("5xx", minute=0), alert("latency", minute=1)]
    ) + grouped(
        [alert("5xx b", minute=0), alert("latency b", minute=1)]
    )

    cel = _proposals(groups)[0].cel

    assert "source.contains('prometheus')" in cel
    assert "source == " not in cel


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


# --------------------------------------------------------------------------- #
# Wording — the LLM writes, it does not decide
# --------------------------------------------------------------------------- #


def _two_recurring_groups():
    return grouped([alert("5xx", minute=0), alert("latency", minute=1)]) + grouped(
        [alert("5xx b", minute=0), alert("latency b", minute=1)]
    )


def _configured(monkeypatch, reply: str, captured: dict | None = None):
    """A configured model that answers with `reply`.

    `apply_wording` imports both litellm and the config lookup inside the
    function, so both are patched where they are looked up rather than
    where they are defined.
    """
    import sys
    import types

    class _Response:
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]

    def _completion(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return _Response()

    module = types.ModuleType("litellm")
    module.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", module)
    # A real EffectiveConfig, not a namespace: the wording path now resolves
    # the model through per-function routing, and a stub that answers only
    # the attributes yesterday's code touched will quietly stop exercising
    # the path the moment resolution grows a step.
    from aiops_api.modules.config.service import EffectiveConfig

    config = EffectiveConfig(
        llm_provider="deepseek",
        llm_model="deepseek/deepseek-v4-pro",
        llm_api_key_env=None,
        budget_max_tool_calls=50,
        budget_max_wall_time_seconds=120.0,
        budget_max_llm_tokens=200_000,
        context_timeline_limit=50,
        llm_embedding_model=None,
    )
    monkeypatch.setattr(
        "aiops_api.modules.config.service.EffectiveConfig.llm_api_key",
        property(lambda self: "k"),
    )
    monkeypatch.setattr(
        "aiops_api.modules.config.get_effective_config", lambda _t: config
    )


def test_wording_never_changes_what_the_rule_matches(monkeypatch):
    """The model names the pattern; the CEL, grouping and evidence counts
    are decided by scoring and must survive untouched."""
    from aiops_api.modules.correlation.wording import apply_wording

    _configured(monkeypatch, '{"name": "Payments error spike", "summary": "Groups payment failures."}')
    proposal = _proposals(_two_recurring_groups())[0]
    before = (proposal.cel, list(proposal.grouping_criteria), proposal.occurrences, proposal.alerts_covered)

    apply_wording([proposal], "t1")

    assert (proposal.cel, list(proposal.grouping_criteria), proposal.occurrences, proposal.alerts_covered) == before
    assert proposal.name == "Payments error spike"


def test_computed_evidence_still_leads_the_rationale(monkeypatch):
    """A hallucinated count is indistinguishable from a real one, so the
    numbers stay computed and come first."""
    from aiops_api.modules.correlation.wording import apply_wording

    _configured(monkeypatch, '{"name": "Payments", "summary": "Useful prose."}')
    proposal = _proposals(_two_recurring_groups())[0]

    apply_wording([proposal], "t1")

    assert proposal.rationale.startswith("Seen 2 times covering 4 alerts.")
    assert "Useful prose." in proposal.rationale
    assert proposal.rationale.endswith("Useful prose.")


def test_a_model_name_that_could_break_keep_is_refused(monkeypatch):
    """The name is handed to Keep as ruleName; model output is untrusted."""
    from aiops_api.modules.correlation.wording import apply_wording

    _configured(monkeypatch, '{"name": "bad\\" || true || \\"", "summary": "ok"}')
    proposal = _proposals(_two_recurring_groups())[0]

    apply_wording([proposal], "t1")

    assert proposal.name == "payment-api correlation"


def test_no_model_configured_keeps_deterministic_wording(monkeypatch):
    """Deterministic wording is the product, not a degraded mode."""
    import types

    from aiops_api.modules.correlation import wording

    monkeypatch.setattr(
        "aiops_api.modules.config.get_effective_config",
        lambda _t: types.SimpleNamespace(llm_model=None, llm_api_key=None),
    )
    monkeypatch.setattr(
        "aiops_api.settings.get_settings",
        lambda: types.SimpleNamespace(llm_model=None),
    )
    proposal = _proposals(_two_recurring_groups())[0]

    wording.apply_wording([proposal], "t1")

    assert proposal.name == "payment-api correlation"


def test_an_llm_failure_does_not_lose_the_proposal(monkeypatch):
    """Wording is a nicety; losing it must not lose the rule."""
    import sys
    import types

    from aiops_api.modules.correlation.wording import apply_wording

    module = types.ModuleType("litellm")

    def _boom(**kwargs):
        raise RuntimeError("provider down")

    module.completion = _boom
    monkeypatch.setitem(sys.modules, "litellm", module)
    monkeypatch.setattr(
        "aiops_api.modules.config.get_effective_config",
        lambda _t: types.SimpleNamespace(llm_model="m", llm_api_key="k"),
    )
    proposal = _proposals(_two_recurring_groups())[0]

    apply_wording([proposal], "t1")  # must not raise

    assert proposal.name == "payment-api correlation"
    assert "Seen 2 times" in proposal.rationale


def test_the_prompt_forbids_inventing_counts():
    """The one thing the model must never do, stated where it will read it."""
    from aiops_api.modules.correlation.wording import SYSTEM_PROMPT

    assert "Never state counts" in SYSTEM_PROMPT
    assert "Never claim a root cause" in SYSTEM_PROMPT


def test_a_single_occurrence_reads_as_english():
    """Minimum Occurrences goes down to 1, so "Seen 1 times" reaches users."""
    groups = grouped([alert("a", minute=0), alert("b", minute=1)])

    rationale = _proposals(groups, min_occurrences=1)[0].rationale

    assert rationale.startswith("Seen 1 time covering 2 alerts.")


def test_proposals_carry_alert_names_for_wording():
    proposal = _proposals(_two_recurring_groups())[0]

    assert "5xx" in proposal.sample_names
    assert "latency" in proposal.sample_names


# --------------------------------------------------------------------------- #
# Execution logs — what the AI page shows about the last run
# --------------------------------------------------------------------------- #


def test_a_run_that_found_nothing_still_says_it_ran():
    """"Algorithm not executed yet" and "ran, found nothing" look identical
    to an operator, and only one of them means something is broken."""
    from aiops_api.modules.correlation.service import _summarise

    summary = _summarise(BASE, [], [], stored=0, pending=0)

    assert "analysed 0 alerts" in summary
    assert "2026-08-02 10:00 UTC" in summary


def test_groups_found_but_nothing_qualified_blames_the_threshold():
    from aiops_api.modules.correlation.service import _summarise

    groups = grouped([alert("a", minute=0), alert("b", minute=1)])
    summary = _summarise(BASE, [{}, {}], groups, stored=0, pending=0, qualified=0)

    assert "found 1 correlated group" in summary
    assert "Minimum Occurrences" in summary


def test_a_steady_state_does_not_read_as_a_failure():
    """Patterns that still hold, already proposed, are the normal case on
    every run after the first — blaming the threshold would be wrong."""
    from aiops_api.modules.correlation.service import _summarise

    groups = grouped([alert("a", minute=0), alert("b", minute=1)])
    summary = _summarise(BASE, [{}, {}], groups, stored=0, pending=3, qualified=3)

    assert "already proposed on an earlier run" in summary
    assert "Minimum Occurrences" not in summary


def test_summary_points_at_the_decision_waiting_in_rules():
    from aiops_api.modules.correlation.service import _summarise

    summary = _summarise(BASE, [{}], [], stored=1, pending=3)

    assert "Proposed 1 new correlation rule" in summary
    assert "3 proposal(s) awaiting your decision" in summary


def test_summary_says_correlation_does_not_create_incidents():
    """The page must not read as "this is merging my alerts" — it proposes."""
    from aiops_api.modules.correlation.service import _summarise

    assert "rules engine" in _summarise(BASE, [], [], stored=0, pending=0)


def test_reporting_logs_preserves_every_other_field(monkeypatch):
    """Keep replaces the config row wholesale, so writing a log while
    echoing anything stale would silently revert the operator's settings."""
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    stored_config = {
        "id": "cfg-1",
        "algorithm_id": service.ALGORITHM_ID,
        "tenant_id": "t1",
        "settings": [{"name": "Enabled", "value": True}],
        "settings_proposed_by_algorithm": None,
        "feedback_logs": None,
        "algorithm": {"name": "Keeply Alert Correlation"},
    }
    sent: dict = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    class _Http:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def put(self, url, json):
            sent["url"] = url
            sent["body"] = json
            return _Response()

    monkeypatch.setattr(service, "fetch_config", lambda _c: dict(stored_config))
    monkeypatch.setattr(service, "_client_http", lambda _c: _Http())
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    service.report_execution(client, None, "ran fine")

    assert sent["body"]["feedback_logs"] == "ran fine"
    # Everything else must survive untouched.
    assert sent["body"]["settings"] == stored_config["settings"]
    assert sent["body"]["id"] == "cfg-1"


def test_reporting_repairs_double_encoded_settings_instead_of_deepening_them(monkeypatch):
    """Rows written by the old json.dumps-into-a-JSON-column bug come back
    as text. Echoing that text verbatim nests it one level deeper on every
    run, until the AI page cannot render its own controls."""
    import json as _json

    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    real_settings = [{"name": "Enabled", "value": True}]
    corrupted = {"id": "cfg-1", "settings": _json.dumps(_json.dumps(real_settings))}
    sent: dict = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    class _Http:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def put(self, url, json):
            sent["body"] = json
            return _Response()

    monkeypatch.setattr(service, "fetch_config", lambda _c: dict(corrupted))
    monkeypatch.setattr(service, "_client_http", lambda _c: _Http())
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    service.report_execution(client, None, "ran fine")

    assert sent["body"]["settings"] == real_settings


def test_decoding_leaves_ordinary_strings_alone():
    """feedback_logs is genuine prose, not JSON — it must survive intact."""
    from aiops_api.modules.correlation.service import _decoded

    assert _decoded("analysed 3 alerts") == "analysed 3 alerts"
    assert _decoded(None) is None
    assert _decoded([{"a": 1}]) == [{"a": 1}]


def test_reporting_rereads_config_so_a_concurrent_edit_is_not_reverted(monkeypatch):
    """Settings can change while the analysis is in flight. Echoing the
    copy read at the start would roll that edit back."""
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    stale = {"id": "cfg-1", "settings": [{"name": "Enabled", "value": True}]}
    current = {"id": "cfg-1", "settings": [{"name": "Enabled", "value": False}]}
    sent: dict = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    class _Http:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def put(self, url, json):
            sent["body"] = json
            return _Response()

    monkeypatch.setattr(service, "fetch_config", lambda _c: dict(current))
    monkeypatch.setattr(service, "_client_http", lambda _c: _Http())
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    service.report_execution(client, stale, "ran fine")

    assert sent["body"]["settings"] == current["settings"]


def test_a_failed_report_does_not_fail_the_analysis(monkeypatch):
    """The log is a nicety; losing it must not lose the run."""
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    def _boom(_c):
        raise RuntimeError("keep is down")

    monkeypatch.setattr(service, "fetch_config", _boom)
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    service.report_execution(client, None, "ran fine")  # must not raise


def test_a_disabled_tenant_reports_that_it_is_off_not_silence(monkeypatch):
    from aiops_api.modules.correlation import service
    from aiops_api.modules.correlation.models import CorrelationClient

    reported: list[str] = []
    monkeypatch.setattr(service, "fetch_config", lambda _c: {"id": "cfg-1"})
    monkeypatch.setattr(
        service,
        "report_execution",
        lambda _c, _cfg, message: reported.append(message),
    )
    monkeypatch.setattr(
        service, "settings_from_config", lambda _cfg: {**service.DEFAULT_SETTINGS, "Enabled": False}
    )
    client = CorrelationClient(tenant_id="t1", back_api_url="http://keep.test", back_api_key="k")

    service.run_for_client(client)

    assert reported and "switched off" in reported[0]


def test_payload_gates_the_generated_rule_behind_approval():
    """A generated rule's first incidents are candidates a human confirms."""
    groups = grouped([alert("a", minute=0), alert("b", minute=1)]) * 2

    payload = _proposals(groups)[0].to_payload()

    assert payload["requireApprove"] is True
    assert payload["celQuery"].startswith("service == ")


# --------------------------------------------------------------------------- #
# What real provider data broke
# --------------------------------------------------------------------------- #


def _alert(**kw):
    base = {
        "name": "High latency",
        "service": "checkout-api",
        "source": ["datadog"],
        "lastReceived": "2026-08-10T12:00:00Z",
    }
    base.update(kw)
    return base


def test_a_placeholder_service_is_not_an_identity():
    # 413 of 3000 alerts on a real account carried `service: undefined`.
    # Scored as a value, every pair of them earned the strongest signal in
    # the model for sharing an absence of information.
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(service="undefined", name="Disk pressure"),
        _alert(service="undefined", name="Certificate expiring"),
    )

    assert "same service" not in " ".join(score.reasons)


def test_a_real_shared_service_still_counts():
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(_alert(), _alert(name="Latency degraded"))

    assert "same service (checkout-api)" in score.reasons


def test_placeholders_are_ignored_inside_a_list_too():
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(service=["undefined"], name="A"),
        _alert(service=["undefined"], name="B"),
    )

    assert "same service" not in " ".join(score.reasons)


def test_two_clusters_in_one_broad_service_are_not_one_problem():
    # `service: elasticache` names the technology, not which cluster is
    # unwell. 955 alerts arrived under it; every pair cleared the default
    # threshold on service plus source alone.
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(service="elasticache", tags={"cacheclusterid": "prd-sae1-redis"}),
        _alert(service="elasticache", tags={"cacheclusterid": "prd-sae1-sessions"}),
    )

    assert score.value < 0.6  # the default Similarity Threshold
    assert any("different resource" in reason for reason in score.reasons)


def test_the_same_cluster_still_groups():
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(service="elasticache", tags={"cacheclusterid": "prd-sae1-redis"}),
        _alert(service="elasticache", tags={"cacheclusterid": "prd-sae1-redis"}),
    )

    assert score.value >= 0.6


def test_a_missing_resource_tag_is_not_treated_as_disagreement():
    # Silence is not conflict. Penalising an alert that simply carries no
    # host tag would punish sparse metadata rather than contradictory
    # metadata — and most alerts have sparse metadata.
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(service="elasticache", tags={"cacheclusterid": "prd-sae1-redis"}),
        _alert(service="elasticache", tags={}),
    )

    assert score.value >= 0.6
    assert not any("different resource" in reason for reason in score.reasons)


def test_the_conflict_says_which_resources_disagreed():
    # A grouping an operator cannot explain is a grouping they cannot tune.
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        _alert(tags={"host": "prd-esc-log-01"}),
        _alert(tags={"host": "prd-esc-log-02"}),
    )

    reason = next(r for r in score.reasons if "different resource" in r)
    assert "prd-esc-log-01" in reason and "prd-esc-log-02" in reason


def test_a_score_never_goes_negative():
    from aiops_api.modules.correlation.similarity import similarity

    score = similarity(
        {"name": "A", "tags": {"host": "h1"}},
        {"name": "B", "tags": {"host": "h2"}},
    )

    assert score.value >= 0.0


def test_history_lookback_is_independent_of_the_grouping_window():
    # Tight grouping and a long memory are compatible wishes; the old
    # multiplier made them mutually exclusive.
    from aiops_api.modules.correlation.service import history_lookback_minutes

    tight = {"History Lookback (hours)": 48.0}
    assert history_lookback_minutes(tight, window_minutes=5.0) == 48 * 60
    assert history_lookback_minutes(tight, window_minutes=60.0) == 48 * 60


def test_an_unset_lookback_keeps_the_previous_behaviour():
    # A deployment that never opens the settings page must not silently
    # change what it mines.
    from aiops_api.modules.correlation.service import (
        HISTORY_WINDOWS,
        history_lookback_minutes,
    )

    assert history_lookback_minutes({}, window_minutes=10.0) == 10.0 * HISTORY_WINDOWS


def test_a_nonsense_lookback_falls_back_instead_of_crashing():
    from aiops_api.modules.correlation.service import (
        HISTORY_WINDOWS,
        history_lookback_minutes,
    )

    for bad in ("", None, "soon", -5):
        assert history_lookback_minutes(
            {"History Lookback (hours)": bad}, window_minutes=10.0
        ) == 10.0 * HISTORY_WINDOWS


def test_a_rejected_credential_says_which_system_refused(monkeypatch):
    # The raw transport error named neither system, so an operator with
    # nothing to fix saw "Failed" and had no way to know that waiting was
    # the correct action.
    import httpx

    from aiops_api.modules.correlation import service as cs

    class _Resp:
        status_code = 401

    class _Http:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return _Resp()

    suggestion = cs.RuleSuggestion(
        tenant_id="keep", name="n", cel="x", timeframe_seconds=60,
        grouping_criteria=[], rationale="r", status="pending",
        occurrences=2, alerts_covered=4,
    )
    from aiops_api.db import session_scope
    with session_scope() as s:
        s.add(suggestion); s.flush(); sid = suggestion.id

    monkeypatch.setattr(cs, "_client_http", lambda c: _Http())
    monkeypatch.setattr(
        cs, "active_clients",
        lambda: [cs.CorrelationClient(tenant_id="keep", back_api_url="u", back_api_key="k")],
    )

    with pytest.raises(cs.CredentialRejected) as excinfo:
        cs.accept_suggestion(sid)

    message = str(excinfo.value)
    assert "Keep rejected" in message
    assert "re-registers" in message  # tells them waiting is the fix
    assert "401" in message


def test_the_suggestion_stays_pending_when_the_credential_is_rejected(monkeypatch):
    # Marking it accepted would lose the proposal to a transient auth
    # problem, and the mining that produced it is not free.
    from aiops_api.modules.correlation import service as cs

    class _Resp:
        status_code = 403

    class _Http:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return _Resp()

    suggestion = cs.RuleSuggestion(
        tenant_id="keep", name="n2", cel="x", timeframe_seconds=60,
        grouping_criteria=[], rationale="r", status="pending",
        occurrences=2, alerts_covered=4,
    )
    from aiops_api.db import session_scope
    from sqlmodel import Session
    from aiops_api.db import get_engine
    with session_scope() as s:
        s.add(suggestion); s.flush(); sid = suggestion.id

    monkeypatch.setattr(cs, "_client_http", lambda c: _Http())
    monkeypatch.setattr(
        cs, "active_clients",
        lambda: [cs.CorrelationClient(tenant_id="keep", back_api_url="u", back_api_key="k")],
    )

    with pytest.raises(cs.CredentialRejected):
        cs.accept_suggestion(sid)

    with Session(get_engine()) as s:
        assert s.get(cs.RuleSuggestion, sid).status == "pending"


# --------------------------------------------------------------------------- #
# Pacing: the reminder is a liveness signal, not a request to recompute
# --------------------------------------------------------------------------- #


def _client(last_run=None):
    from aiops_api.modules.correlation.service import CorrelationClient

    return CorrelationClient(
        tenant_id="keep", back_api_url="u", back_api_key="k", last_run_at=last_run
    )


def test_the_first_reminder_always_runs():
    from aiops_api.modules.correlation.service import due_for_run

    assert due_for_run(_client(last_run=None), window_minutes=10.0)


def test_a_reminder_inside_the_window_does_not_recompute():
    # Keep pings us from GET /ai/stats and the UI polls it every few
    # seconds. Measured: 392 reminders in 25 minutes, 909s of Keep's CPU
    # spent answering the resulting /alerts reads, on a 1-core limit.
    from datetime import timedelta

    from aiops_api.modules.correlation.service import _utcnow, due_for_run

    recent = _utcnow() - timedelta(seconds=30)
    assert not due_for_run(_client(last_run=recent), window_minutes=10.0)


def test_a_reminder_after_the_window_runs_again():
    from datetime import timedelta

    from aiops_api.modules.correlation.service import _utcnow, due_for_run

    old = _utcnow() - timedelta(minutes=11)
    assert due_for_run(_client(last_run=old), window_minutes=10.0)


def test_a_tiny_window_cannot_turn_the_reminder_into_a_hot_loop():
    # Grouping over 30 seconds is a legitimate setting; recomputing every
    # 30 seconds because a browser tab is open is not.
    from datetime import timedelta

    from aiops_api.modules.correlation.service import _utcnow, due_for_run

    recent = _utcnow() - timedelta(seconds=35)
    assert not due_for_run(_client(last_run=recent), window_minutes=0.5)


def test_a_naive_timestamp_does_not_crash_the_comparison():
    # SQLite hands back naive datetimes; an aware/naive comparison raises,
    # and this runs inside a background task where it would be swallowed.
    from datetime import datetime, timedelta

    from aiops_api.modules.correlation.service import due_for_run

    naive = datetime.utcnow() - timedelta(minutes=30)
    assert due_for_run(_client(last_run=naive), window_minutes=10.0) is True
