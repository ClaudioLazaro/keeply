"""What the model is actually given to reason with.

The LLM prompt used to carry only each evidence item's summary line, so a
model analysing an outage received "get_events: 13 events returned" while the
deterministic fallback — which dumps the whole payload — could see the BackOff
and the OOMKilled inside it. The more capable reasoner had strictly less to
work with, and the eval harness never caught it because it scores the
deterministic path.
"""

from types import SimpleNamespace

from aiops_api.modules.rca.draft import (
    DETAIL_MAX_CHARS,
    DETAIL_TOTAL_MAX_CHARS,
    evidence_detail,
)
from aiops_api.modules.rca.engine import _llm_user_prompt
from aiops_api.modules.rca.draft import build_citations


def _evidence(eid, tool, summary, result=None, error=None):
    payload = {"arguments": {}}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return SimpleNamespace(id=eid, tool=tool, summary=summary, payload=payload)


EVENTS = _evidence(
    "e1",
    "get_events",
    "get_events: 3 events returned",
    {
        "backend": "live",
        "cluster": "prod",
        "events": [
            {"type": "Warning", "reason": "OOMKilled", "message": "container exceeded memory"},
            {"type": "Warning", "reason": "BackOff", "message": "back-off restarting"},
        ],
    },
)


def test_the_finding_reaches_the_prompt_not_just_its_count():
    prompt = _llm_user_prompt({"id": "i"}, [EVENTS], [], build_citations([EVENTS], []))
    assert "OOMKilled" in prompt
    assert "back-off restarting" in prompt


def test_provenance_fields_are_not_repeated_into_the_detail():
    """backend/cluster are reported separately; here they would be noise."""
    detail = evidence_detail(EVENTS)
    assert "OOMKilled" in detail
    assert "backend" not in detail
    assert "cluster" not in detail


def test_a_long_list_is_truncated_visibly():
    """A silent cut would let the model think it saw the whole picture."""
    item = _evidence("e", "get_pods", "many", {"pods": [{"name": f"p-{i}"} for i in range(40)]})
    detail = evidence_detail(item)
    assert "more)" in detail
    assert len(detail) <= DETAIL_MAX_CHARS


def test_a_failed_call_says_why_it_failed():
    item = _evidence("e", "get_logs", "gap", error="ApiException: (400) container not specified")
    assert "container not specified" in evidence_detail(item)


def test_the_total_budget_is_respected_across_many_items():
    """Ten specialists must not crowd out the incident they explain."""
    many = [
        _evidence(f"e{i}", "get_pods", "s", {"pods": [{"name": "x" * 150} for _ in range(5)]})
        for i in range(60)
    ]
    prompt = _llm_user_prompt({"id": "i"}, many, [], build_citations(many, []))
    # Summaries are always present; only the detail is budgeted.
    assert len(prompt) < DETAIL_TOTAL_MAX_CHARS + 20_000


def test_the_head_of_the_evidence_keeps_its_detail_when_the_budget_runs_out():
    """Specialists run in priority order, so the tail is what should degrade."""
    many = [EVENTS] + [
        _evidence(f"e{i}", "get_pods", "s", {"pods": [{"name": "y" * 150} for _ in range(5)]})
        for i in range(60)
    ]
    prompt = _llm_user_prompt({"id": "i"}, many, [], build_citations(many, []))
    assert "OOMKilled" in prompt


def test_an_item_with_no_payload_is_skipped_quietly():
    assert evidence_detail(SimpleNamespace(id="e", tool="t", summary="s")) == ""
