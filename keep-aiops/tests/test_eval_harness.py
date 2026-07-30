"""Eval harness tests: end-to-end golden-set run + rubric discrimination.

The harness itself must run green against the landed M2 contracts (knowledge
keyword retrieval + deterministic RCA fallback); the rubric must also
discriminate — a citation-free draft is not useful, and imperative mutate
claims fail the suggest-only guard.
"""

import pytest

from eval import run_eval


@pytest.fixture(scope="module")
def contracts():
    return run_eval.load_contracts()


@pytest.fixture(scope="module")
def results(contracts):
    fixtures = run_eval.load_fixtures(run_eval.GOLDEN_SET_DIR)
    assert len(fixtures) == 6, "golden set must contain the 6 MVP scenarios"
    return [run_eval.run_fixture(fixture, contracts) for fixture in fixtures]


# --------------------------------------------------------------------------- #
# End-to-end (AC4)
# --------------------------------------------------------------------------- #


def test_golden_set_useful_ratio_meets_ac4(results):
    ratio = sum(r.rubric.useful for r in results) / len(results)
    assert ratio >= run_eval.USEFUL_RATIO_THRESHOLD, (
        f"AC4 violated: useful-ratio {ratio:.2f} < {run_eval.USEFUL_RATIO_THRESHOLD}; "
        f"per-fixture: {[(r.fixture['id'], r.rubric.total) for r in results]}"
    )


def test_every_fixture_produces_draft_with_citations(results):
    for result in results:
        assert result.draft.strip(), f"{result.fixture['id']}: empty draft"
        assert result.rubric.citation_count >= 1, f"{result.fixture['id']}: no citation markers"


def test_main_exit_code_zero(capsys):
    assert run_eval.main([]) == 0
    out = capsys.readouterr().out
    assert "useful-ratio:" in out
    assert "PASS" in out


# --------------------------------------------------------------------------- #
# Rubric discrimination
# --------------------------------------------------------------------------- #


BROKEN_FIXTURE = {
    "id": "broken-no-citations",
    "expected_keywords": ["OOMKilled"],
    "expected_min_citations": 2,
}


def test_broken_draft_without_citations_is_not_useful():
    draft = (
        "Something is wrong with the payment-api pod, it may be OOMKilled. "
        "A human should look at it.\n\n_Mode: suggest-only — no actions taken._"
    )
    score = run_eval.score_draft(BROKEN_FIXTURE, draft)
    assert not score.has_min_citations
    assert not score.has_evidence_ref
    assert not score.useful


def test_mutate_imperative_fails_suggest_only_guard():
    draft = (
        "The pod is OOMKilled [E1].\n\n"
        "Recommended: restart the pod and scale the deployment to 5 replicas now [K1].\n\n"
        "_Mode: suggest-only — no actions taken._"
    )
    score = run_eval.score_draft(BROKEN_FIXTURE, draft)
    assert score.disclaimer_found
    assert score.mutate_claims, "imperative restart/scale must be detected"
    assert not score.has_disclaimer_no_mutate


def test_negated_mutate_terms_pass_guard():
    draft = (
        "The pod is OOMKilled [E1] per the runbook [K1].\n\n"
        "The draft does not restart anything; no scale operation is suggested "
        "without human approval.\n\n"
        "_Mode: suggest-only — no actions taken._"
    )
    score = run_eval.score_draft(BROKEN_FIXTURE, draft)
    assert score.mutate_claims == []
    assert score.has_disclaimer_no_mutate


def test_verbatim_evidence_quotes_do_not_trip_guard():
    """Stub evidence legitimately contains 'restarting' — quoted sections are
    excluded from the mutate scan (they are observations, not claims)."""
    draft = (
        "## Summary\n\nThe pod payment-api is down [E1]; see runbook [K1].\n\n"
        "## Evidence\n\n"
        "- [E1] get_events: BackOff: Back-off restarting failed container payment-api\n\n"
        "## Knowledge references\n\n"
        "- [K1] Runbook: correlate restarts with deploys\n\n"
        "_Mode: suggest-only — no actions taken._"
    )
    score = run_eval.score_draft(BROKEN_FIXTURE, draft)
    assert score.mutate_claims == []
    assert score.has_disclaimer_no_mutate
    assert score.useful


def test_extract_generated_prose_strips_quoted_sections():
    draft = (
        "## Summary\n\nGenerated prose stays.\n\n"
        "## Evidence\n\n- [E1] Back-off restarting failed container\n\n"
        "```\nkubectl delete pod x\n```\n\n"
        "> quoted runbook line about restarts\n"
    )
    prose = run_eval.extract_generated_prose(draft)
    assert "Generated prose stays." in prose
    assert "restarting" not in prose
    assert "kubectl" not in prose
    assert "restarts" not in prose
