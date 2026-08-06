"""Coordinator: runs specialists under a per-investigation budget.

The coordinator is the only caller of MCP tools during the gathering phase
of an investigation. It owns the HTTP client, the live catalog, and the
budget tracker. Specialists are stateless — they only know their tool names
and how to chain calls. That makes them individually testable and lets
the coordinator centralize the cost / audit / failure policy.

Flow for a single investigation run:

1. fetch the live catalog from the gateway (one HTTP call, cached for the run)
2. instantiate a ``BudgetTracker`` (raises ``BudgetExceeded`` on breach)
3. pick the subset of specialists whose tools appear in the live catalog
4. for each applicable specialist, call ``gather(invoke, budget, used)`` and
   persist the resulting ``ToolCall``s as Evidence rows
5. return the aggregated list of Evidence (caller persists them)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

import httpx

from aiops_api import metrics
from aiops_api.modules.orchestrator.models import Evidence
from aiops_api.modules.policy import assert_tool_allowed
from aiops_api.modules.specialists.base import (
    Budget,
    BudgetExceeded,
    SpecialistResult,
    ToolCall,
)
from aiops_api.modules.specialists.registry import default_specialists
from aiops_api.modules.specialists.tracker import BudgetTracker
from aiops_api.telemetry import investigation_span

logger = logging.getLogger(__name__)

INVESTIGATION_TIMEOUT = 15.0


def _fetch_tool_catalog(client: Any, gateway_url: str) -> dict[str, dict[str, Any]]:
    response = client.get(f"{gateway_url}/v1/mcp/tools")
    response.raise_for_status()
    return {tool["name"]: tool for tool in response.json()}


def _summarize(tool: str, result: Any) -> str:
    """Mirror the helper in ``service.py`` so the evidence section is stable."""
    if isinstance(result, dict):
        for key in ("pods", "items", "events", "alerts", "applications", "repositories"):
            value = result.get(key)
            if isinstance(value, list):
                return f"{tool}: {len(value)} {key} returned"
        if isinstance(result.get("logs"), str):
            lines = result["logs"].count("\n") + 1
            return f"{tool}: {lines} log lines returned"
        if isinstance(result.get("lines"), list):
            return f"{tool}: {len(result['lines'])} log lines returned"
    if isinstance(result, list):
        return f"{tool}: {len(result)} items returned"
    return f"{tool}: result received ({type(result).__name__})"


def _make_invoke(
    client: Any,
    gateway_url: str,
    catalog: dict[str, dict[str, Any]],
    tenant_id: str,
    investigation_id: str,
) -> Callable[[str, dict[str, Any]], tuple[Any, str | None]]:
    """Build the closure specialists call to invoke a tool.

    Each call: fail-closed policy check (mirroring the gateway's gate),
    HTTP POST to the gateway. The closure is intentionally small so it is
    easy to fake in tests.
    """

    def _invoke(tool: str, arguments: dict[str, Any]) -> tuple[Any, str | None]:
        descriptor = catalog.get(tool)
        assert_tool_allowed(tool, descriptor.get("execution_class") if descriptor else None)
        response = client.post(
            f"{gateway_url}/v1/mcp/tools/{tool}:invoke",
            json={"tenant_id": tenant_id, "investigation_id": investigation_id, "arguments": arguments},
        )
        response.raise_for_status()
        body = response.json()
        return body.get("result"), body.get("audit_id")

    return _invoke


def _resolve_backend(call: ToolCall, catalog_entry: dict[str, Any] | None) -> str:
    """Provenance of one tool call: live | stub | gap | unknown.

    The tool's own result is authoritative when it reports a ``backend``
    (every built-in tool does). The catalog's declared ``mode`` is the
    fallback, so a tool that forgets to report still gets classified
    rather than silently passing as live.
    """
    if call.is_gap:
        return "gap"
    if isinstance(call.result, dict):
        reported = call.result.get("backend")
        if isinstance(reported, str) and reported:
            return reported
    if catalog_entry:
        mode = catalog_entry.get("mode")
        if isinstance(mode, str) and mode:
            return mode
    return "unknown"


def _call_to_evidence(
    investigation_id: str, call: ToolCall, catalog_entry: dict[str, Any] | None = None
) -> Evidence:
    """Translate one specialist tool call into an Evidence row.

    The summary and payload are produced identically to the pre-M3
    orchestrator so existing tests and the writeback keep working.
    """
    backend = _resolve_backend(call, catalog_entry)
    if call.is_gap:
        return Evidence(
            investigation_id=investigation_id,
            tool=call.tool,
            summary=f"{call.tool}: evidence gap — {call.error}",
            payload={"arguments": call.arguments, "error": call.error},
            backend=backend,
        )
    return Evidence(
        investigation_id=investigation_id,
        tool=call.tool,
        summary=call.summary or _summarize(call.tool, call.result),
        payload={"arguments": call.arguments, "result": call.result, "audit_id": call.audit_id},
        backend=backend,
    )


class _NullCtx:
    """Context-manager shim for clients (e.g. test doubles) that don't
    support ``with`` themselves but expose ``.get`` / ``.post`` directly."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def __enter__(self) -> Any:
        return self.client

    def __exit__(self, exc_type, exc, tb) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


@contextmanager
def _mesh(
    *,
    gateway_url: str,
    tenant_id: str,
    investigation_id: str,
    client_factory: Callable[[], Any] | None,
) -> Iterator[tuple[dict[str, dict[str, Any]], Callable[..., Any]]]:
    """Yield ``(catalog, invoke)`` from whichever tool mesh is configured.

    Two transports live side by side on purpose. ``AIOPS_MCP_TRANSPORT=mcp``
    routes through ContextForge over real MCP; the default keeps the legacy
    HTTP gateway, so the cutover is one environment variable and so is the
    rollback. An injected ``client_factory`` always means the legacy path —
    that is how tests supply a fake gateway, and a test double should not be
    silently bypassed by an environment setting.
    """
    from aiops_api.settings import get_settings

    settings = get_settings()
    if client_factory is None and settings.mcp_transport == "mcp":
        from aiops_api.modules.specialists.mcp_client import mcp_mesh

        with mcp_mesh(
            url=settings.mcp_server_url,
            token=settings.mcp_bearer_token,
            tenant_id=tenant_id,
            investigation_id=investigation_id,
            timeout=INVESTIGATION_TIMEOUT,
            trusted_read_only=settings.mcp_trusted_read_only_tools,
        ) as pair:
            yield pair
        return

    factory = client_factory or (lambda: httpx.Client(timeout=INVESTIGATION_TIMEOUT))
    client = factory()
    ctx = client if hasattr(client, "__enter__") else _NullCtx(client)
    with ctx as active:
        catalog = _fetch_tool_catalog(active, gateway_url)
        yield catalog, _make_invoke(active, gateway_url, catalog, tenant_id, investigation_id)


def run_specialists(
    *,
    investigation_id: str,
    tenant_id: str,
    gateway_url: str,
    budget: Budget,
    specialists: tuple[Any, ...] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> tuple[list[Evidence], list[SpecialistResult], BudgetTracker]:
    """Run every applicable specialist under a single budget.

    Returns the evidence rows to persist, the per-specialist results (for
    inspection), and the tracker (for cost metrics). Raises
    :class:`BudgetExceeded` on breach — the caller (orchestrator service)
    turns that into an investigation ``failed`` status.

    A specialist is only run if at least one of its declared tools appears
    in the live MCP catalog. This keeps the M0/M2 demo path (3 k8s tools)
    identical to the pre-M3 orchestrator: when only K8s is registered,
    only the K8s specialist runs. Production deployments that register
    more tools get more evidence without any orchestrator change.
    """
    specialists = specialists or default_specialists()

    # Operator-disabled specialists never run, regardless of catalog. This
    # is the lever for cutting integrations that are still on stub and only
    # add noise to the evidence.
    from aiops_api.modules.config import get_effective_config

    disabled = set(get_effective_config(tenant_id).disabled_specialists)
    if disabled:
        specialists = tuple(spec for spec in specialists if spec.name not in disabled)
        logger.info(
            "specialists disabled by config",
            extra={"investigation_id": investigation_id, "disabled": sorted(disabled)},
        )

    tracker = BudgetTracker.start(budget)
    results: list[SpecialistResult] = []
    evidence: list[Evidence] = []

    with _mesh(
        gateway_url=gateway_url,
        tenant_id=tenant_id,
        investigation_id=investigation_id,
        client_factory=client_factory,
    ) as (catalog, invoke):
        applicable = [spec for spec in specialists if any(t in catalog for t in spec.tools)]
        if not applicable:
            logger.warning(
                "no specialists match the live MCP catalog",
                extra={"investigation_id": investigation_id, "specialists": [s.name for s in specialists]},
            )

        for specialist in applicable:
            with investigation_span(
                investigation_id,
                name=f"specialist.{specialist.name}",
                specialist=specialist.name,
                tools=",".join(specialist.tools),
            ) as span:
                try:
                    result = specialist.gather(
                        catalog=catalog,
                        invoke=invoke,
                        budget=budget,
                        used=tracker,
                    )
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 — specialist-level fault, not an investigation fault
                    logger.exception(
                        "specialist crashed",
                        extra={"investigation_id": investigation_id, "specialist": specialist.name},
                    )
                    span.set_attribute("outcome", "specialist_error")
                    result = SpecialistResult(
                        specialist=specialist.name,
                        calls=[ToolCall(tool="*", arguments={}, error=f"{type(exc).__name__}: {exc}")],
                    )
                else:
                    span.set_attribute(
                        "outcome",
                        "ok" if not any(call.is_gap for call in result.calls) else "partial",
                    )
                    span.set_attribute("calls", len(result.calls))
                    span.set_attribute("gaps", sum(1 for call in result.calls if call.is_gap))

                for call in result.calls:
                    evidence.append(
                        _call_to_evidence(investigation_id, call, catalog.get(call.tool))
                    )
                    metrics.mcp_tool_calls.labels(
                        tool=call.tool,
                        outcome="error" if call.is_gap else "success",
                    ).inc()
                    if call.is_gap:
                        metrics.evidence_gaps.labels(tool=call.tool).inc()
                results.append(result)
    return evidence, results, tracker
