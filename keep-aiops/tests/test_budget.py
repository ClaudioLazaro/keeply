"""M3 per-investigation budget tracker: clamps and breaches."""

import pytest

from aiops_api.modules.specialists.base import Budget, BudgetExceeded
from aiops_api.modules.specialists.tracker import BudgetTracker


def _tracker(**overrides) -> BudgetTracker:
    kwargs = {"tool_calls": 2, "wall_time": 0.5, "llm_tokens": 10}
    kwargs.update(overrides)
    return BudgetTracker.start(Budget(**kwargs))


def test_tool_call_counter_increments_and_clamps():
    t = _tracker()
    t.record_tool_call()
    t.record_tool_call()
    assert t.tool_calls == 2
    with pytest.raises(BudgetExceeded) as exc:
        t.record_tool_call()
    assert exc.value.kind == "tool_calls"
    assert exc.value.used == 3
    assert exc.value.limit == 2


def test_llm_tokens_counter_clamps():
    t = _tracker()
    t.record_llm_tokens(5)
    t.record_llm_tokens(5)
    with pytest.raises(BudgetExceeded) as exc:
        t.record_llm_tokens(1)
    assert exc.value.kind == "llm_tokens"
    assert exc.value.limit == 10


def test_record_llm_tokens_ignores_zero_or_negative():
    t = _tracker()
    t.record_llm_tokens(0)
    t.record_llm_tokens(-5)
    assert t.llm_tokens == 0


def test_wall_time_breach_detected_on_check(monkeypatch):
    t = _tracker(wall_time=0.05)
    # Synthesize a 1s elapsed window.
    t.started_at -= 1.0
    with pytest.raises(BudgetExceeded) as exc:
        t.check()
    assert exc.value.kind == "wall_time"


def test_snapshot_exposes_all_dimensions():
    t = _tracker()
    t.record_tool_call(2)
    snap = t.snapshot()
    assert snap["tool_calls"] == 2
    assert "wall_time" in snap and snap["wall_time"] >= 0
    assert snap["llm_tokens"] == 0
