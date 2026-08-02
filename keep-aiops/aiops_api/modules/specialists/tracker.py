"""Per-investigation budget tracker.

The coordinator instantiates one ``BudgetTracker`` per investigation. Each
MCP tool call, the RCA LLM call, and the wall-clock tick consult
``tracker.check`` before doing work; a breach raises
:class:`BudgetExceeded` and the investigation moves to ``failed``.

The tracker also publishes ``keep_aiops_investigation_cost_total{kind=...}``
counters so the operator can see per-tenant / per-mode cost drift
(low cardinality: ``kind`` is one of ``tool_calls`` | ``llm_tokens``;
tenant / investigation ids are deliberately NOT labels).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from aiops_api import metrics
from aiops_api.modules.specialists.base import Budget, BudgetExceeded


@dataclass
class BudgetTracker:
    """Mutable per-run budget state. Thread-unsafe by design (one task)."""

    budget: Budget
    started_at: float
    tool_calls: int = 0
    llm_tokens: int = 0

    @classmethod
    def start(cls, budget: Budget) -> "BudgetTracker":
        return cls(budget=budget, started_at=time.monotonic())

    # --- mutators --------------------------------------------------------

    def record_tool_call(self, n: int = 1) -> None:
        self.tool_calls += n
        metrics.investigation_cost.labels(kind="tool_calls").inc(n)
        self._check()

    def record_llm_tokens(self, n: int) -> None:
        if n <= 0:
            return
        self.llm_tokens += n
        metrics.investigation_cost.labels(kind="llm_tokens").inc(n)
        self._check()

    # --- queries ---------------------------------------------------------

    @property
    def wall_time(self) -> float:
        return time.monotonic() - self.started_at

    def snapshot(self) -> dict[str, float | int]:
        return {
            "tool_calls": self.tool_calls,
            "wall_time": round(self.wall_time, 3),
            "llm_tokens": self.llm_tokens,
        }

    # --- enforcement -----------------------------------------------------

    def check(self) -> None:
        """Raise :class:`BudgetExceeded` if any limit is breached."""
        reason = self.budget.clamp(self.tool_calls, self.wall_time, self.llm_tokens)
        if reason is not None:
            kind = reason.split("=", 1)[0]
            raise BudgetExceeded(kind=kind, used=self._value_for(kind), limit=self._limit_for(kind))

    # --- internals -------------------------------------------------------

    def _check(self) -> None:
        """Same as :meth:`check` but a private alias for internal callers."""
        self.check()

    def _value_for(self, kind: str) -> int | float:
        return {"tool_calls": self.tool_calls, "wall_time": self.wall_time, "llm_tokens": self.llm_tokens}[kind]

    def _limit_for(self, kind: str) -> int | float:
        return {
            "tool_calls": self.budget.tool_calls,
            "wall_time": self.budget.wall_time,
            "llm_tokens": self.budget.llm_tokens,
        }[kind]
