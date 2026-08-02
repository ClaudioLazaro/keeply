"""Evidence provenance guard: stub data must never pass as live.

The failure this protects against: an RCA built entirely on canned demo
payloads renders identically to one built on production telemetry, and an
operator acts on it during an incident.
"""

from types import SimpleNamespace

from aiops_api.modules.rca.fallback import deterministic_rca
from aiops_api.modules.rca.provenance import (
    UNCORROBORATED_CONFIDENCE_FACTOR,
    annotate_hypotheses,
    describe,
    evidence_backend,
    tally,
)

INCIDENT = {"id": "inc-1", "tenant_id": "t1", "investigation_id": "inv-1"}


def ev(id_: str, backend: str, summary: str = "OOMKilled container", **kw):
    return SimpleNamespace(id=id_, tool="get_events", summary=summary, backend=backend, **kw)


# --------------------------------------------------------------------------- #
# Provenance resolution
# --------------------------------------------------------------------------- #


def test_backend_column_is_authoritative():
    assert evidence_backend(ev("e1", "live")) == "live"
    assert evidence_backend(ev("e1", "stub")) == "stub"


def test_backend_falls_back_to_payload_for_legacy_rows():
    """Rows written before the column existed still classify correctly."""
    legacy = SimpleNamespace(
        id="e1", tool="get_pods", summary="s", payload={"result": {"backend": "stub"}}
    )
    assert evidence_backend(legacy) == "stub"


def test_missing_provenance_is_unknown_not_live():
    """The safe default: never assume unlabelled evidence is real."""
    unlabelled = SimpleNamespace(id="e1", tool="t", summary="s", payload={})
    assert evidence_backend(unlabelled) == "unknown"


def test_tally_counts_each_bucket():
    counts = tally([ev("e1", "live"), ev("e2", "stub"), ev("e3", "stub"), ev("e4", "gap")])
    assert counts == {"live": 1, "stub": 2, "gap": 1}


# --------------------------------------------------------------------------- #
# Summary sentence
# --------------------------------------------------------------------------- #


def test_describe_states_the_split_explicitly():
    text = describe([ev("e1", "live"), ev("e2", "stub")])
    assert "1 live" in text
    assert "1 stub (demo data)" in text


def test_describe_shouts_when_nothing_is_live():
    """All-stub is the dangerous case and must be impossible to miss."""
    text = describe([ev("e1", "stub"), ev("e2", "stub")])
    assert "No live evidence was collected" in text
    assert "must not be used to make incident decisions" in text


def test_describe_does_not_shout_when_some_evidence_is_live():
    text = describe([ev("e1", "live"), ev("e2", "stub")])
    assert "No live evidence was collected" not in text
    assert "marked unverified" in text


# --------------------------------------------------------------------------- #
# Hypothesis corroboration
# --------------------------------------------------------------------------- #


def test_hypothesis_backed_by_live_evidence_keeps_its_confidence():
    evidence = [ev("e1", "live")]
    hypotheses = [{"title": "OOM", "confidence": 0.7, "supporting_evidence": ["e1"]}]

    annotate_hypotheses(hypotheses, evidence)

    assert hypotheses[0]["confidence"] == 0.7
    assert hypotheses[0]["corroborated"] is True
    assert "caveat" not in hypotheses[0]


def test_hypothesis_backed_only_by_stub_is_discounted_and_labelled():
    evidence = [ev("e1", "stub")]
    hypotheses = [{"title": "OOM", "confidence": 0.7, "supporting_evidence": ["e1"]}]

    annotate_hypotheses(hypotheses, evidence)

    assert hypotheses[0]["confidence"] == round(0.7 * UNCORROBORATED_CONFIDENCE_FACTOR, 2)
    assert hypotheses[0]["corroborated"] is False
    assert hypotheses[0]["caveat"] == "unverified — stub data only"


def test_one_live_reference_is_enough_to_corroborate():
    evidence = [ev("e1", "stub"), ev("e2", "live")]
    hypotheses = [{"title": "OOM", "confidence": 0.7, "supporting_evidence": ["e1", "e2"]}]

    annotate_hypotheses(hypotheses, evidence)

    assert hypotheses[0]["corroborated"] is True
    assert hypotheses[0]["confidence"] == 0.7


def test_confidence_is_never_inflated():
    evidence = [ev("e1", "live")]
    hypotheses = [{"title": "OOM", "confidence": 0.2, "supporting_evidence": ["e1"]}]

    annotate_hypotheses(hypotheses, evidence)

    assert hypotheses[0]["confidence"] == 0.2


# --------------------------------------------------------------------------- #
# End-to-end through the deterministic RCA
# --------------------------------------------------------------------------- #


def test_all_stub_rca_marks_every_hypothesis_unverified():
    evidence = [ev("e1", "stub"), ev("e2", "stub", summary="5xx error rate rising")]

    result = deterministic_rca(INCIDENT, evidence, [])

    assert all(h["corroborated"] is False for h in result["hypotheses"])
    assert all(h["confidence"] <= 0.3 for h in result["hypotheses"])
    assert "No live evidence was collected" in result["draft"]
    assert "unverified — stub data only" in result["draft"]


def test_draft_tags_stub_evidence_bullets_and_leaves_live_clean():
    evidence = [ev("e1", "live", summary="OOMKilled in payment-api"), ev("e2", "stub")]

    draft = deterministic_rca(INCIDENT, evidence, [])["draft"]

    live_line = next(line for line in draft.splitlines() if "OOMKilled in payment-api" in line)
    stub_line = next(
        line for line in draft.splitlines() if line.startswith("- [E2]")
    )
    assert "(stub" not in live_line
    assert "_(stub — demo data)_" in stub_line
