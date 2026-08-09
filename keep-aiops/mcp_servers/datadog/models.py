"""Output models for the Datadog MCP server.

Same contract as the Kubernetes server: ``backend`` and ``target`` have no
default, so MCP puts them in ``outputSchema.required`` and a result cannot omit
where it came from.

Datadog is shaped differently from Kubernetes, and that difference is the point
of building it second. There is no cluster and no namespace; scope is
``service + env + time window``. If the ``Scope`` abstraction survives both,
it generalises. If it does not, it was namespace with a generic name.
"""

from typing import Literal

from pydantic import BaseModel, Field

Provenance = Literal["live", "stub", "gap"]


class TargetScoped(BaseModel):
    """Fields every result carries so a reader can judge what it is worth."""

    backend: Provenance = Field(
        description="live (real Datadog account), stub (canned demo payload), gap (the call failed)."
    )
    target: str = Field(
        description="Which Datadog account answered, as named in the server's target registry."
    )
    error: str | None = Field(default=None, description="Why the call failed. Set only when backend is 'gap'.")


class MonitorState(BaseModel):
    id: int | None = None
    name: str
    status: str | None = Field(default=None, description="Alert / Warn / OK / No Data.")
    message: str | None = None
    tags: list[str] = Field(default_factory=list)
    last_triggered: str | None = None


class MonitorsResult(TargetScoped):
    monitors: list[MonitorState] = Field(default_factory=list)


class MetricPoint(BaseModel):
    timestamp: str
    value: float


class MetricSeries(BaseModel):
    metric: str
    scope: str | None = None
    points: list[MetricPoint] = Field(default_factory=list)


class MetricsResult(TargetScoped):
    query: str
    series: list[MetricSeries] = Field(default_factory=list)


class DatadogEvent(BaseModel):
    title: str
    text: str | None = None
    tags: list[str] = Field(default_factory=list)
    timestamp: str | None = None
    source: str | None = None


class EventsResult(TargetScoped):
    events: list[DatadogEvent] = Field(default_factory=list)


class SpanSummary(BaseModel):
    """One hop of a distributed trace.

    ``error`` and ``duration_ms`` are what make a trace useful for RCA: they
    say which hop failed and how long it took, which Kubernetes cannot answer
    at all — a pod can be Running while every call through it times out.
    """

    service: str
    operation: str | None = None
    duration_ms: float | None = None
    error: bool = False
    status_code: str | None = None


class TraceResult(TargetScoped):
    trace_id: str | None = None
    spans: list[SpanSummary] = Field(default_factory=list)
    failing_service: str | None = Field(
        default=None, description="First span with an error — where to look next."
    )


class LogLine(BaseModel):
    timestamp: str | None = None
    service: str | None = None
    message: str
    trace_id: str | None = None


class LogsResult(TargetScoped):
    query: str
    lines: list[LogLine] = Field(default_factory=list)


class TargetInfo(BaseModel):
    name: str
    mode: Literal["live", "stub"]
    site: str | None = None
    description: str | None = None


class TargetsResult(BaseModel):
    targets: list[TargetInfo] = Field(default_factory=list)
