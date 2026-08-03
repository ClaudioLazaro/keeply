"""A failure must never be reportable as a result.

Every case here is one where the platform used to answer confidently after
something had gone wrong. That is worse than an error: an operator can act
on an error, but a confident wrong answer gets believed.
"""

import pytest

from aiops_api.modules.correlation import service
from aiops_api.modules.correlation.models import CorrelationClient


def _client():
    return CorrelationClient(
        tenant_id="t1", back_api_url="http://keep.test", back_api_key="k"
    )


class _DeadHttp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def get(self, *args, **kwargs):
        raise ConnectionError("name or service not known")


# --------------------------------------------------------------------------- #
# Correlation: an unreachable Keep is not a configuration answer
# --------------------------------------------------------------------------- #


def test_unreachable_keep_raises_rather_than_reading_as_no_config(monkeypatch):
    monkeypatch.setattr(service, "_client_http", lambda _c: _DeadHttp())

    with pytest.raises(service.KeepUnreachable):
        service.fetch_config(_client())


def test_unreachable_keep_is_not_reported_as_correlation_being_off(monkeypatch):
    """The defaults have Enabled=False, so a failed read used to tell the
    operator to switch on something that was already on."""
    monkeypatch.setattr(service, "_client_http", lambda _c: _DeadHttp())
    reported: list[str] = []
    monkeypatch.setattr(
        service, "report_execution", lambda _c, _cfg, message: reported.append(message)
    )

    result = service.run_for_client(_client())

    assert result == {"groups": 0, "proposed": 0}
    assert reported, "an unreadable Keep must still be reported"
    assert "switched off" not in reported[0]
    assert "connectivity problem" in reported[0]


def test_unreadable_alert_history_is_not_reported_as_a_quiet_system(monkeypatch):
    """"analysed 0 alerts, nothing repeated" for a failed read tells the
    operator all is well at the moment the analysis has gone blind."""
    monkeypatch.setattr(service, "fetch_config", lambda _c: {"id": "cfg-1"})
    monkeypatch.setattr(
        service,
        "settings_from_config",
        lambda _cfg: {**service.DEFAULT_SETTINGS, "Enabled": True},
    )

    def _dead(*args, **kwargs):
        raise service.KeepUnreachable("boom")

    monkeypatch.setattr(service, "fetch_recent_alerts", _dead)
    reported: list[str] = []
    monkeypatch.setattr(
        service, "report_execution", lambda _c, _cfg, message: reported.append(message)
    )

    service.run_for_client(_client())

    assert "does not mean your alerts are quiet" in reported[0]
    assert "analysed 0 alerts" not in reported[0]


def test_alert_fetch_failure_is_distinguishable_from_an_empty_history(monkeypatch):
    monkeypatch.setattr(service, "_client_http", lambda _c: _DeadHttp())

    with pytest.raises(service.KeepUnreachable):
        service.fetch_recent_alerts(_client(), 10)


def test_fetch_settings_still_tolerates_an_unreachable_keep(monkeypatch):
    """Callers that only want values keep the conservative defaults."""
    monkeypatch.setattr(service, "_client_http", lambda _c: _DeadHttp())

    assert service.fetch_settings(_client()) == service.DEFAULT_SETTINGS


# --------------------------------------------------------------------------- #
# RCA: a template must not sign its work as a model
# --------------------------------------------------------------------------- #


def _draft(**overrides):
    from aiops_api.modules.rca.draft import render_draft

    kwargs = {
        "incident": {"name": "x", "severity": "critical"},
        "summary": "something happened",
        "hypotheses": [],
        "evidence": [],
        "knowledge": [],
        "citations": {},
        "investigation_id": "inv-1",
    }
    kwargs.update(overrides)
    return render_draft(**kwargs)


def test_the_deterministic_fallback_does_not_claim_to_be_ai_assisted():
    from aiops_api.modules.rca.fallback import deterministic_rca

    result = deterministic_rca({"name": "x", "investigation_id": "inv-1"}, [], [])

    assert "AI-assisted" not in result["draft"]
    assert "deterministic" in result["draft"]


def test_the_llm_path_still_says_ai_assisted():
    assert "AI-assisted" in _draft(ai_assisted=True)


# --------------------------------------------------------------------------- #
# Knowledge: "nothing matched" and "the search broke" are different answers
# --------------------------------------------------------------------------- #


def test_a_knowledge_failure_surfaces_as_an_evidence_gap(monkeypatch):
    from aiops_api.modules.orchestrator import service as orch
    from aiops_api.modules.orchestrator.models import Investigation

    def _boom(*args, **kwargs):
        raise RuntimeError("index unavailable")

    import aiops_api.modules.knowledge as knowledge_module

    monkeypatch.setattr(knowledge_module, "query_knowledge", _boom)
    investigation = Investigation(id="inv-1", tenant_id="t1", incident_id="inc-1")

    results, gap = orch._query_knowledge_safe(investigation, [])

    assert results == []
    assert gap is not None
    assert gap.backend == "gap"
    assert "knowledge retrieval failed" in gap.summary


def test_a_successful_empty_search_reports_no_gap(monkeypatch):
    from aiops_api.modules.orchestrator import service as orch
    from aiops_api.modules.orchestrator.models import Investigation

    import aiops_api.modules.knowledge as knowledge_module

    monkeypatch.setattr(knowledge_module, "query_knowledge", lambda *a, **k: [])
    investigation = Investigation(id="inv-1", tenant_id="t1", incident_id="inc-1")

    results, gap = orch._query_knowledge_safe(investigation, [])

    assert results == []
    assert gap is None


# --------------------------------------------------------------------------- #
# Provenance: the weakest analyses must not be the quietest ones
# --------------------------------------------------------------------------- #


def test_collecting_nothing_at_all_is_warned_about():
    """Zero evidence is weaker than demo data, yet it was the only case
    that produced no warning."""
    from aiops_api.modules.rca.provenance import describe

    sentence = describe([])

    assert "No evidence was collected at all" in sentence
    assert "must not be used to make incident decisions" in sentence


def test_an_all_gap_investigation_is_warned_about():
    from aiops_api.modules.rca.provenance import describe

    sentence = describe([{"backend": "gap", "id": "e1"}, {"backend": "gap", "id": "e2"}])

    assert "Every evidence-gathering call failed" in sentence


def test_stub_only_keeps_its_existing_warning():
    from aiops_api.modules.rca.provenance import describe

    assert "rests entirely on" in describe([{"backend": "stub", "id": "e1"}])


def test_live_evidence_produces_no_alarm():
    from aiops_api.modules.rca.provenance import describe

    sentence = describe([{"backend": "live", "id": "e1"}])

    assert "must not be used" not in sentence


def test_a_caveat_names_the_provenance_it_actually_has():
    """"stub data only" was printed even when every supporting call had
    failed and no stub existed."""
    from aiops_api.modules.rca.provenance import annotate_hypotheses

    evidence = [{"backend": "gap", "id": "e1"}]
    hypotheses = annotate_hypotheses(
        [{"title": "x", "confidence": 0.9, "supporting_evidence": ["e1"]}], evidence
    )

    assert hypotheses[0]["caveat"] == "unverified — every supporting call failed"


def test_a_hypothesis_citing_nothing_says_so():
    from aiops_api.modules.rca.provenance import annotate_hypotheses

    hypotheses = annotate_hypotheses(
        [{"title": "x", "confidence": 0.9, "supporting_evidence": []}], []
    )

    assert hypotheses[0]["caveat"] == "unverified — no evidence cited"


def test_stub_backed_hypotheses_keep_the_original_label():
    from aiops_api.modules.rca.provenance import annotate_hypotheses

    hypotheses = annotate_hypotheses(
        [{"title": "x", "confidence": 0.9, "supporting_evidence": ["e1"]}],
        [{"backend": "stub", "id": "e1"}],
    )

    assert hypotheses[0]["caveat"] == "unverified — stub data only"
    assert hypotheses[0]["confidence"] == 0.36


def test_mixed_stub_and_gap_support_claims_neither():
    from aiops_api.modules.rca.provenance import annotate_hypotheses

    hypotheses = annotate_hypotheses(
        [{"title": "x", "confidence": 0.5, "supporting_evidence": ["e1", "e2"]}],
        [{"backend": "stub", "id": "e1"}, {"backend": "gap", "id": "e2"}],
    )

    assert hypotheses[0]["caveat"] == "unverified — no live evidence"


# --------------------------------------------------------------------------- #
# Grouping: a runaway group is a finding, not a non-event
# --------------------------------------------------------------------------- #


def test_oversized_groups_are_reported_not_just_dropped():
    """A group that blew past the cap is the clearest sign the thresholds
    are wrong; dropping it silently reported the run as quiet."""
    from datetime import datetime, timedelta, timezone

    from aiops_api.modules.correlation.grouping import group_alerts

    base = datetime(2026, 8, 2, 10, tzinfo=timezone.utc)
    alerts = [
        {
            "name": f"symptom {i}",
            "service": "payment-api",
            "source": ["prometheus"],
            "fingerprint": f"fp-{i}",
            "lastReceived": (base + timedelta(seconds=i * 10)).isoformat(),
        }
        for i in range(12)
    ]

    result = group_alerts(
        alerts, window_minutes=10, similarity_threshold=0.4, max_group_size=5
    )

    assert result == []  # nothing usable survived
    assert result.oversized, "the runaway group must still be visible"
    assert result.oversized[0].size == 12


def test_the_summary_names_a_runaway_group():
    from datetime import datetime, timezone

    from aiops_api.modules.correlation.grouping import GroupingResult, CorrelationGroup
    from aiops_api.modules.correlation.service import _summarise

    groups = GroupingResult()
    groups.oversized = [CorrelationGroup(alerts=[{}] * 40)]

    summary = _summarise(
        datetime(2026, 8, 2, 10, tzinfo=timezone.utc), [{}], groups, 0, 0, qualified=0
    )

    assert "exceeded Max Alerts Per Incident" in summary
    assert "largest: 40 alerts" in summary


def test_a_normal_run_says_nothing_about_oversized_groups():
    from datetime import datetime, timezone

    from aiops_api.modules.correlation.grouping import GroupingResult
    from aiops_api.modules.correlation.service import _summarise

    summary = _summarise(
        datetime(2026, 8, 2, 10, tzinfo=timezone.utc), [{}], GroupingResult(), 1, 1, qualified=1
    )

    assert "exceeded" not in summary


# --------------------------------------------------------------------------- #
# Timestamps: one bad alert must not take down the whole run
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_does_not_break_grouping():
    """Mixing naive and aware datetimes raises on subtraction, and grouping
    sorts across the whole batch — so one such alert failed every alert."""
    from datetime import datetime, timedelta, timezone

    from aiops_api.modules.correlation.grouping import group_alerts

    base = datetime(2026, 8, 2, 10, tzinfo=timezone.utc)
    aware = {
        "name": "5xx rate",
        "service": "payment-api",
        "source": ["prometheus"],
        "fingerprint": "fp-1",
        "lastReceived": base.isoformat(),
    }
    naive = {
        "name": "latency",
        "service": "payment-api",
        "source": ["prometheus"],
        "fingerprint": "fp-2",
        # no offset — what a provider emitting local time looks like
        "lastReceived": (base + timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
    }

    groups = group_alerts(
        [aware, naive], window_minutes=10, similarity_threshold=0.4, max_group_size=20
    )

    assert len(groups) == 1
    assert groups[0].size == 2


def test_a_naive_timestamp_is_read_as_utc():
    from datetime import datetime, timezone

    from aiops_api.modules.correlation.similarity import alert_time

    parsed = alert_time({"lastReceived": "2026-08-02T10:00:00"})

    assert parsed == datetime(2026, 8, 2, 10, tzinfo=timezone.utc)


def test_unknown_provenance_is_not_called_demo_data():
    """A tool that answered but did not say whether its data was real is
    not the same as a tool that returned a canned payload."""
    from aiops_api.modules.rca.provenance import describe

    sentence = describe([{"backend": "unknown", "id": "e1"}])

    assert "demo data" not in sentence
    assert "did not say whether their data was real" in sentence
    assert "must not be used to make incident decisions" in sentence


def test_every_zero_live_mix_still_warns():
    """Whatever the combination, an analysis with no live evidence says so."""
    from itertools import combinations

    from aiops_api.modules.rca.provenance import describe

    kinds = ["stub", "gap", "unknown"]
    for size in (1, 2, 3):
        for combo in combinations(kinds, size):
            evidence = [{"backend": k, "id": f"e-{k}"} for k in combo]
            assert "must not be used to make incident decisions" in describe(evidence), combo


# --------------------------------------------------------------------------- #
# Context pack: an RCA about an incident it knows nothing about
# --------------------------------------------------------------------------- #


def test_a_failed_context_pack_surfaces_as_an_evidence_gap(monkeypatch):
    """Without the pack the incident view is bare ids, so the model reasons
    about an incident with no name, severity, service or alerts."""
    from aiops_api.modules.orchestrator import service as orch
    from aiops_api.modules.orchestrator.models import Investigation

    import aiops_api.modules.context_builder as ctx

    def _boom(*args, **kwargs):
        raise RuntimeError("keep unreachable")

    monkeypatch.setattr(ctx, "build_context_pack", _boom)
    investigation = Investigation(id="inv-1", tenant_id="t1", incident_id="inc-1")

    gap = orch._build_and_store_context_pack(investigation, "t1", "inc-1", None)

    assert gap is not None
    assert gap.backend == "gap"
    assert "incident context could not be assembled" in gap.summary


def test_a_successful_context_pack_reports_no_gap(monkeypatch):
    from aiops_api.modules.orchestrator import service as orch
    from aiops_api.modules.orchestrator.models import Investigation

    import aiops_api.modules.context_builder as ctx

    monkeypatch.setattr(ctx, "build_context_pack", lambda *a, **k: {"incident": {"name": "x"}})
    investigation = Investigation(id="inv-1", tenant_id="t1", incident_id="inc-1")

    gap = orch._build_and_store_context_pack(investigation, "t1", "inc-1", None)

    assert gap is None
    assert investigation.context_pack == {"incident": {"name": "x"}}
