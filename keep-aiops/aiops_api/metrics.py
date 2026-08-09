"""Prometheus metrics for the AI plane.

All series carry the ``keep_aiops_`` namespace. Cardinality is deliberately
low: NO tenant / investigation / incident labels — only bounded enums
(``mode``, ``tool``, ``outcome``).

``setup_metrics(app)`` instruments HTTP via prometheus-fastapi-instrumentator
and exposes ``GET /metrics``. The endpoint is registered directly on the app
(outside the event-bridge router), so it is exempt from the webhook HMAC
dependency — Prometheus scrapes it without credentials. Network-level access
control is the deployment's job (see deploy/observability/).
"""

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

METRIC_NAMESPACE = "keep_aiops"

# Investigation lifecycle (label: mode — currently always "suggest")
investigations_started = Counter(
    "investigations_started_total",
    "Investigations created (auto-investigate eligible incidents).",
    labelnames=("mode",),
    namespace=METRIC_NAMESPACE,
)
investigations_completed = Counter(
    "investigations_completed_total",
    "Investigations that reached rca_ready.",
    labelnames=("mode",),
    namespace=METRIC_NAMESPACE,
)
investigations_failed = Counter(
    "investigations_failed_total",
    "Investigations that ended in failed status.",
    labelnames=("mode",),
    namespace=METRIC_NAMESPACE,
)
investigation_duration = Histogram(
    "investigation_duration_seconds",
    "Wall-clock duration of an investigation run (start to terminal status).",
    labelnames=("mode",),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 900),
    namespace=METRIC_NAMESPACE,
)
investigations_active = Gauge(
    "investigations_active",
    "Investigations currently running (gathering/writeback in flight).",
    namespace=METRIC_NAMESPACE,
)

# MCP gateway tool calls from the orchestrator (labels: tool, outcome)
mcp_tool_calls = Counter(
    "mcp_tool_calls_total",
    "MCP gateway tool invocations from the orchestrator.",
    labelnames=("tool", "outcome"),
    namespace=METRIC_NAMESPACE,
)

# Human feedback on investigations (label: rating — bounded enum useful|not_useful)
investigation_feedback = Counter(
    "investigation_feedback_total",
    "Human feedback submitted on investigations (useful / not_useful).",
    labelnames=("rating",),
    namespace=METRIC_NAMESPACE,
)

# Evidence gaps (tool call denied by policy or failed) — drives the evidence-gap panel
evidence_gaps = Counter(
    "evidence_gaps_total",
    "Evidence items that could not be gathered (policy deny or tool error).",
    labelnames=("tool",),
    namespace=METRIC_NAMESPACE,
)

# Per-investigation cost (M3). Tracks tool calls, LLM tokens, and wall-clock
# seconds consumed by an investigation. Cardinality stays low: only the
# bounded `kind` label (tool_calls | llm_tokens | wall_time_seconds).
investigation_cost = Counter(
    "investigation_cost_total",
    "Per-investigation resource consumption. Labels: kind (tool_calls, llm_tokens, wall_time_seconds).",
    labelnames=("kind",),
    namespace=METRIC_NAMESPACE,
)
# Money, separate from the resource counter above because it answers a
# different question: the counter says how much was consumed, this says what it
# was worth. `priced` carries whether the model was in the price table — an
# unpriced model contributes 0.0 and must never be read as free.
investigation_cost_usd = Counter(
    "investigation_cost_usd_total",
    "Estimated LLM spend, in USD. Labels: priced (yes|no).",
    labelnames=("priced",),
    namespace=METRIC_NAMESPACE,
)
investigation_cost_exceeded = Counter(
    "investigation_cost_exceeded_total",
    "Investigations that failed because a cost budget was exceeded.",
    labelnames=("kind",),
    namespace=METRIC_NAMESPACE,
)

def setup_metrics(app) -> None:
    """Instrument HTTP and expose GET /metrics (unauthenticated scrape endpoint)."""
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=("/metrics",),
    ).instrument(app, metric_namespace=METRIC_NAMESPACE).expose(app, endpoint="/metrics", include_in_schema=False)
