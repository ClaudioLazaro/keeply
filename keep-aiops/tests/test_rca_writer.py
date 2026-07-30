"""RCA writer: deterministic fallback, LLM path, FSM hypothesizing phase, endpoint."""

import json
import sys
import types
from types import SimpleNamespace

import pytest
import respx
from fastapi.testclient import TestClient

from aiops_api.modules.rca.engine import generate_rca
from aiops_api.modules.rca.fallback import deterministic_rca
from aiops_api.settings import Settings
from tests.conftest import KEEP_API_URL, TENANT_ID, make_event, post_event

OOM_EVIDENCE = [
    SimpleNamespace(
        id="ev-1",
        tool="get_events",
        summary="get_events: 1 events returned",
        payload={"result": {"events": [{"reason": "OOMKilled", "message": "container oomkilled"}]}},
    ),
    SimpleNamespace(id="ev-2", tool="get_pods", summary="get_pods: 1 pods returned", payload={"pods": []}),
]

OOM_KNOWLEDGE = [
    {"id": "doc-1", "title": "OOMKilled runbook", "source": "runbooks", "chunk": "OOMKilled memory limit", "score": 0.9},
]

INCIDENT = {"id": "inc-1", "tenant_id": TENANT_ID, "name": "Payment API elevated 5xx rate", "investigation_id": "inv-1"}


# --------------------------------------------------------------------------- #
# Deterministic fallback (pure function)
# --------------------------------------------------------------------------- #


def test_fallback_produces_oom_hypothesis_with_valid_citations():
    result = deterministic_rca(INCIDENT, OOM_EVIDENCE, [])

    hypothesis = result["hypotheses"][0]
    assert hypothesis["title"] == "Container OOMKilled / memory limit"
    assert hypothesis["confidence"] == 0.7
    assert hypothesis["supporting_evidence"] == ["ev-1"]
    assert hypothesis["evidence_refs"] == ["E1"]
    assert result["citations"]["evidence"] == {"E1": "ev-1", "E2": "ev-2"}
    assert result["citations"]["knowledge"] == {}


def test_fallback_draft_contains_markers_and_suggest_only_disclaimer():
    result = deterministic_rca(INCIDENT, OOM_EVIDENCE, OOM_KNOWLEDGE)
    draft = result["draft"]

    assert "[E1]" in draft
    assert "[K1]" in draft
    assert "suggest-only" in draft
    assert "no actions taken" in draft
    assert result["hypotheses"][0]["supporting_knowledge"] == ["doc-1"]
    assert result["hypotheses"][0]["knowledge_refs"] == ["K1"]


def test_fallback_always_emits_at_least_one_hypothesis():
    evidence = [SimpleNamespace(id="ev-9", tool="get_logs", summary="get_logs: 2 log lines", payload={})]
    result = deterministic_rca(INCIDENT, evidence, [])

    assert len(result["hypotheses"]) >= 1
    assert result["hypotheses"][0]["supporting_evidence"] == ["ev-9"]
    assert result["hypotheses"][0]["evidence_refs"] == ["E1"]


def test_fallback_rules_error_rate_and_connection_pool():
    evidence = [
        SimpleNamespace(id="ev-1", tool="get_logs", summary="5xx error rate elevated", payload={}),
        SimpleNamespace(id="ev-2", tool="get_logs", summary="db connection pool at max", payload={}),
    ]
    result = deterministic_rca(INCIDENT, evidence, [])
    titles = [h["title"] for h in result["hypotheses"]]
    assert "Application error rate elevated" in titles
    assert "Connection pool exhaustion" in titles


# --------------------------------------------------------------------------- #
# LLM path (mocked litellm)
# --------------------------------------------------------------------------- #


def _fake_litellm(payload: dict | None = None, error: Exception | None = None):
    def completion(**kwargs):
        if error is not None:
            raise error
        content = json.dumps(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return types.SimpleNamespace(completion=completion)


def _llm_investigation():
    return SimpleNamespace(id="inv-1", tenant_id=TENANT_ID, incident_id="inc-1")


def test_llm_path_parses_structured_response_and_resolves_citations(monkeypatch):
    payload = {
        "summary": "Payment API pods are being OOMKilled under memory pressure.",
        "hypotheses": [
            {
                "title": "Container memory limit too low",
                "confidence": 0.82,
                "evidence_refs": ["E1"],
                "knowledge_refs": ["K1"],
            }
        ],
    }
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(payload=payload))
    settings = Settings(llm_model="test-model", llm_api_key="test-key")

    draft, hypotheses, citations = generate_rca(
        _llm_investigation(), OOM_EVIDENCE, None, OOM_KNOWLEDGE, settings=settings
    )

    assert hypotheses[0]["title"] == "Container memory limit too low"
    assert hypotheses[0]["confidence"] == 0.82
    assert hypotheses[0]["supporting_evidence"] == ["ev-1"]
    assert hypotheses[0]["supporting_knowledge"] == ["doc-1"]
    assert "Payment API pods are being OOMKilled" in draft
    assert "[E1]" in draft and "[K1]" in draft
    assert "suggest-only" in draft
    assert citations["evidence"]["E1"] == "ev-1"
    assert citations["knowledge"]["K1"] == "doc-1"


def test_llm_unknown_refs_are_dropped(monkeypatch):
    payload = {
        "summary": "s",
        "hypotheses": [{"title": "h", "confidence": 0.5, "evidence_refs": ["E99", "[e1]"], "knowledge_refs": []}],
    }
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(payload=payload))
    settings = Settings(llm_model="test-model")

    _, hypotheses, _ = generate_rca(_llm_investigation(), OOM_EVIDENCE, None, [], settings=settings)
    assert hypotheses[0]["evidence_refs"] == ["E1"]
    assert hypotheses[0]["supporting_evidence"] == ["ev-1"]


def test_llm_error_degrades_to_deterministic_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(error=RuntimeError("provider down")))
    settings = Settings(llm_model="test-model")

    draft, hypotheses, _ = generate_rca(_llm_investigation(), OOM_EVIDENCE, None, [], settings=settings)
    assert hypotheses[0]["title"] == "Container OOMKilled / memory limit"
    assert "suggest-only" in draft


def test_no_llm_model_uses_deterministic_fallback():
    draft, hypotheses, citations = generate_rca(
        _llm_investigation(), OOM_EVIDENCE, None, [], settings=Settings(llm_model="")
    )
    assert hypotheses[0]["title"] == "Container OOMKilled / memory limit"
    assert "[E1]" in draft
    assert citations["evidence"]["E1"] == "ev-1"


# --------------------------------------------------------------------------- #
# FSM integration: queued -> gathering -> hypothesizing -> rca_ready
# --------------------------------------------------------------------------- #


def test_fsm_passes_through_hypothesizing_to_rca_ready(client, mocked_backends, monkeypatch):
    from aiops_api.modules.orchestrator import service

    statuses: list[str] = []
    original = service._set_status

    def recording(session, investigation, status):
        statuses.append(status)
        return original(session, investigation, status)

    monkeypatch.setattr(service, "_set_status", recording)

    event = make_event("incident.created")
    assert post_event(client, event).status_code == 202

    assert "hypothesizing" in statuses
    assert statuses[-1] == "rca_ready"

    investigation = client.get("/v1/investigations", params={"incident_id": event["subject"]}).json()[0]
    assert investigation["status"] == "rca_ready"
    assert investigation["rca_citations"]["evidence"]["E1"]

    draft = investigation["rca_draft"]
    assert "[E1]" in draft
    assert "suggest-only" in draft

    hypotheses = client.get(f"/v1/investigations/{investigation['id']}/hypotheses").json()
    assert len(hypotheses) >= 1
    assert all(h["investigation_id"] == investigation["id"] for h in hypotheses)
    assert all(h["supporting_evidence"] for h in hypotheses)

    comment_body = json.loads(mocked_backends.comment_route.calls.last.request.content)["comment"]
    assert "References:" in comment_body
    assert "[E1]" in comment_body


# --------------------------------------------------------------------------- #
# Hypotheses endpoint tenant scoping
# --------------------------------------------------------------------------- #

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
KEY_A = "key-for-tenant-a"
KEY_B = "key-for-tenant-b"


@pytest.fixture()
def auth_client(settings_env, monkeypatch):
    monkeypatch.setenv("AIOPS_AUTH_ENABLED", "true")

    from aiops_api.main import create_app
    from aiops_api.modules.auth import clear_cache
    from aiops_api.settings import get_settings

    get_settings.cache_clear()
    clear_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    clear_cache()


def _seed_investigation_with_hypothesis(tenant_id: str) -> str:
    from aiops_api.db import get_engine
    from aiops_api.modules.orchestrator.models import Investigation
    from aiops_api.modules.rca.models import Hypothesis
    from sqlmodel import Session

    with Session(get_engine()) as session:
        investigation = Investigation(tenant_id=tenant_id, incident_id="inc-seed", status="rca_ready")
        session.add(investigation)
        session.flush()
        session.add(
            Hypothesis(
                investigation_id=investigation.id,
                title="Container OOMKilled / memory limit",
                confidence=0.7,
                supporting_evidence=["ev-1"],
                supporting_knowledge=[],
            )
        )
        session.commit()
        return investigation.id


@respx.mock
def test_hypotheses_endpoint_is_tenant_scoped(auth_client):
    respx.get(f"{KEEP_API_URL}/whoami", headers={"X-API-KEY": KEY_A}).respond(200, json={"tenant_id": TENANT_A})
    respx.get(f"{KEEP_API_URL}/whoami", headers={"X-API-KEY": KEY_B}).respond(200, json={"tenant_id": TENANT_B})
    investigation_id = _seed_investigation_with_hypothesis(TENANT_A)

    own = auth_client.get(f"/v1/investigations/{investigation_id}/hypotheses", headers={"X-API-KEY": KEY_A})
    assert own.status_code == 200
    assert [h["title"] for h in own.json()] == ["Container OOMKilled / memory limit"]
    assert own.json()[0]["confidence"] == 0.7
    assert own.json()[0]["supporting_evidence"] == ["ev-1"]

    cross = auth_client.get(f"/v1/investigations/{investigation_id}/hypotheses", headers={"X-API-KEY": KEY_B})
    assert cross.status_code == 404
