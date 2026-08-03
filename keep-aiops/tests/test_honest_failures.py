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
