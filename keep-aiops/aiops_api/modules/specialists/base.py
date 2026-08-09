"""Specialist protocol + value types.

A specialist is a read-only agent that contributes evidence to an
investigation by invoking a fixed set of MCP tools through the gateway. The
interface is deliberately small: ``gather`` is the only entry point and the
return type is a pure data class so the coordinator can persist it without
hitting the registry again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """A single tool invocation (success or evidence gap) made by a specialist.

    ``result`` carries the gateway payload on success. ``error`` carries a
    short reason on policy denial or transport failure (the gap section of
    the context pack reads it). Exactly one of the two is populated.
    """

    tool: str
    arguments: dict[str, Any]
    result: Any = None
    audit_id: str | None = None
    error: str | None = None
    summary: str | None = None

    @property
    def is_gap(self) -> bool:
        return self.error is not None


@dataclass
class SpecialistResult:
    """Aggregated output of one specialist run.

    ``calls`` lists every attempted tool invocation in the order the
    specialist chose to make them. ``extra_evidence`` is a free-form dict
    of derived facts (e.g. discovered pod names) that the coordinator can
    pass to subsequent specialists or attach to the investigation row.
    """

    specialist: str
    calls: list[ToolCall] = field(default_factory=list)
    extra_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def successful_calls(self) -> list[ToolCall]:
        return [call for call in self.calls if not call.is_gap]


@dataclass(frozen=True)
class Scope:
    """What the investigation is about, so gathering can be aimed at it.

    Without this a specialist queries everything the credential can see. On a
    shared cluster that meant 55 pods across five namespaces belonging to
    unrelated projects, one of which was then picked by a "most troubled pod"
    heuristic and filed as evidence for whatever incident was running.

    ``derived`` is false when the incident carried nothing to aim at. The
    difference matters: a query that could not be scoped is weaker evidence
    than one that was, and the specialist says so in the summary rather than
    letting an unscoped sweep read like a targeted one.
    """

    cluster: str = ""
    services: tuple[str, ...] = ()
    namespaces: tuple[str, ...] = ()

    @property
    def derived(self) -> bool:
        return bool(self.namespaces or self.services)


class BudgetExceeded(RuntimeError):
    """Raised when a specialist's tool call would breach the per-investigation budget.

    The coordinator catches this, marks the investigation ``failed`` with
    ``BudgetExceeded`` in the error, and stops the run.
    """

    def __init__(self, kind: str, used: int | float, limit: int | float) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"budget exceeded: {kind}={used} > limit={limit}")


@dataclass
class Budget:
    """Per-investigation resource caps. All fields are hard ceilings.

    ``tool_calls`` counts every MCP invocation (success or gap). ``wall_time``
    is the wall-clock seconds between the start of the investigation's
    gathering phase and now. ``llm_tokens`` is the LiteLLM-reported prompt +
    completion token count; the RCA writer increments it after each call.
    A breach raises :class:`BudgetExceeded` from the next-check call.
    """

    tool_calls: int = 50
    wall_time: float = 120.0
    llm_tokens: int = 200_000

    def clamp(self, used_tool_calls: int, used_wall: float, used_tokens: int) -> str | None:
        if used_tool_calls > self.tool_calls:
            return f"tool_calls={used_tool_calls} > limit={self.tool_calls}"
        if used_wall > self.wall_time:
            return f"wall_time={used_wall:.1f}s > limit={self.wall_time:.1f}s"
        if used_tokens > self.llm_tokens:
            return f"llm_tokens={used_tokens} > limit={self.llm_tokens}"
        return None


class Specialist(Protocol):
    """Protocol every specialist implements. The registry keys by ``name``."""

    name: str
    tools: tuple[str, ...]

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: "Callable[[str, dict[str, Any]], tuple[Any, str | None]]",
        budget: Budget,
        used: "BudgetTracker",
        scope: Scope,
    ) -> SpecialistResult: ...


# Forward reference types imported at use site to avoid a circular import
# at module-load time (specialists import BudgetTracker; tracker imports
# BudgetExceeded).
from typing import Callable  # noqa: E402
from aiops_api.modules.specialists.tracker import BudgetTracker  # noqa: E402,F401
