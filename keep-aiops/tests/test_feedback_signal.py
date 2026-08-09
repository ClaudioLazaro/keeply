"""The operator's verdict finally reaches retrieval."""

import pytest

from aiops_api.modules.knowledge.feedback_signal import (
    MAX_ADJUSTMENT,
    MIN_RATINGS,
    adjustment_for,
    apply,
)


def test_a_thin_sample_has_no_opinion():
    """One rating is noise; acting on it adds jitter to a deterministic rank."""
    assert adjustment_for(1, 0) == 1.0
    assert adjustment_for(0, 0) == 1.0


def test_useful_ratings_lift_and_unhelpful_ones_lower():
    assert adjustment_for(4, 0) == pytest.approx(1 + MAX_ADJUSTMENT)
    assert adjustment_for(0, 4) == pytest.approx(1 - MAX_ADJUSTMENT)
    assert adjustment_for(2, 2) == pytest.approx(1.0)


def test_the_adjustment_is_bounded_on_both_sides():
    """Unbounded, one popular document would dominate every retrieval and the
    corpus would collapse to whatever was rated first."""
    for useful, not_useful in [(1000, 0), (0, 1000), (999, 1)]:
        factor = adjustment_for(useful, not_useful)
        assert 1 - MAX_ADJUSTMENT <= factor <= 1 + MAX_ADJUSTMENT


def test_it_nudges_ranking_rather_than_filtering(monkeypatch):
    """A thumbs-down means the analysis was unhelpful — not that this runbook
    was wrong. Excluding on that evidence would be overreach."""
    monkeypatch.setattr(
        "aiops_api.modules.knowledge.feedback_signal._document_ratings",
        lambda tenant: {"d-bad": (0, 5)},
    )
    results = [
        {"id": "d-bad", "title": "b", "score": 0.9},
        {"id": "d-ok", "title": "a", "score": 0.85},
    ]
    out = apply("t", results)
    ids = [r["id"] for r in out]
    assert "d-bad" in ids, "a badly rated document must still be retrievable"
    assert out[0]["id"] == "d-ok", "but it should rank below a comparable one"


def test_the_reason_is_recorded_so_a_surprising_rank_can_be_explained(monkeypatch):
    monkeypatch.setattr(
        "aiops_api.modules.knowledge.feedback_signal._document_ratings",
        lambda tenant: {"d1": (5, 0)},
    )
    out = apply("t", [{"id": "d1", "title": "x", "score": 0.5}])
    assert out[0]["feedback_factor"] > 1
    assert out[0]["feedback_ratings"] == {"useful": 5, "not_useful": 0}


def test_relevance_still_dominates(monkeypatch):
    """Feedback breaks ties; it must not overturn a real match."""
    monkeypatch.setattr(
        "aiops_api.modules.knowledge.feedback_signal._document_ratings",
        lambda tenant: {"weak": (50, 0)},
    )
    out = apply("t", [{"id": "strong", "title": "a", "score": 0.90},
                      {"id": "weak", "title": "b", "score": 0.70}])
    assert out[0]["id"] == "strong"


def test_an_unavailable_feedback_store_leaves_ranking_untouched(monkeypatch):
    def boom(_tenant):
        raise RuntimeError("table missing")

    monkeypatch.setattr("aiops_api.modules.knowledge.feedback_signal._document_ratings", boom)
    results = [{"id": "d1", "title": "x", "score": 0.5}]
    assert apply("t", results) == results
