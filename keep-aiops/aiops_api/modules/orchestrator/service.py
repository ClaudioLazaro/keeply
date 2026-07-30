"""Investigation FSM service.

Flow for an eligible `incident.created` (severity in
settings.auto_investigate_severities):

1. create Investigation idempotently (unique on tenant_id+incident_id)
2. background: queued -> gathering — call read-only MCP gateway tools
   (get_pods / get_events / get_logs), each invocation policy-checked
   (fail-closed) and persisted as Evidence; individual tool failures become
   evidence-gap notes and the run continues
3. gathering -> hypothesizing — assemble the context pack, retrieve knowledge
   (best-effort), then generate hypotheses + an RCA draft with [E#]/[K#]
   citations (LiteLLM when AIOPS_LLM_MODEL is set, deterministic rules
   otherwise); hypotheses and the citations map are persisted
4. hypothesizing -> rca_ready — write back to Keep: incident comment (draft +
   references section) + aiops.* enrichment keys

Any unexpected error moves the investigation to `failed` with the error text.
`incident.updated` is a no-op when an investigation exists (logged).
`incident.resolved` sets `incident_resolved=True` and keeps the status
(rca_ready) so the draft remains available — see models.Investigation.
"""

import logging
import time
from typing import Any

import httpx
from sqlmodel import select

from aiops_api import metrics
from aiops_api.db import session_scope
from aiops_api.modules.event_bridge.schemas import EventType, KeepEventEnvelope
from aiops_api.modules.orchestrator.models import Evidence, Investigation, _utcnow
from aiops_api.modules.policy import PolicyDenied, assert_tool_allowed
from aiops_api.modules.rca import Hypothesis, generate_rca
from aiops_api.settings import Settings, get_settings
from aiops_api.telemetry import investigation_span

logger = logging.getLogger(__name__)

GATHER_TOOLS = ("get_pods", "get_events", "get_logs")
INVESTIGATION_TIMEOUT = 15.0


# --------------------------------------------------------------------------- #
# Event dispatch
# --------------------------------------------------------------------------- #


def handle_event(event: KeepEventEnvelope, background_tasks: Any, settings: Settings | None = None) -> Investigation | None:
    """Dispatch a validated Keep event into the orchestrator."""
    settings = settings or get_settings()

    if event.type == EventType.INCIDENT_CREATED:
        if event.data.severity.value not in settings.auto_investigate_severities:
            logger.info(
                "incident.created below auto-investigate severities, skipping",
                extra={"severity": event.data.severity.value, "incident_id": event.data.incident_id},
            )
            return None
        investigation, created = get_or_create_investigation(
            tenant_id=event.tenantid,
            incident_id=event.data.incident_id,
        )
        if created:
            metrics.investigations_started.labels(mode=investigation.mode).inc()
            background_tasks.add_task(run_investigation, investigation.id)
        else:
            logger.info(
                "investigation already exists for incident, not re-queuing",
                extra={"investigation_id": investigation.id, "incident_id": event.data.incident_id},
            )
        return investigation

    if event.type == EventType.INCIDENT_UPDATED:
        # M0: no replanning — log only when an investigation exists.
        existing = find_investigation(event.tenantid, event.data.incident_id)
        if existing is not None:
            logger.info(
                "incident.updated for existing investigation (no-op in M0)",
                extra={"investigation_id": existing.id, "incident_id": event.data.incident_id},
            )
        return existing

    if event.type == EventType.INCIDENT_RESOLVED:
        existing = find_investigation(event.tenantid, event.data.incident_id)
        if existing is not None:
            with session_scope() as session:
                investigation = session.get(Investigation, existing.id)
                investigation.incident_resolved = True
                investigation.updated_at = _utcnow()
                session.add(investigation)
            logger.info(
                "incident.resolved: marked investigation incident_resolved (status kept)",
                extra={"investigation_id": existing.id},
            )
        return existing

    logger.warning("unhandled event type", extra={"type": event.type})
    return None


def get_or_create_investigation(tenant_id: str, incident_id: str) -> tuple[Investigation, bool]:
    """Idempotent on (tenant_id, incident_id); IntegrityError -> return existing."""
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        existing = session.exec(
            select(Investigation).where(
                Investigation.tenant_id == tenant_id,
                Investigation.incident_id == incident_id,
            )
        ).first()
        if existing is not None:
            session.expunge(existing)
            return existing, False
        investigation = Investigation(tenant_id=tenant_id, incident_id=incident_id)
        session.add(investigation)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(Investigation).where(
                    Investigation.tenant_id == tenant_id,
                    Investigation.incident_id == incident_id,
                )
            ).one()
            session.expunge(existing)
            return existing, False
        session.expunge(investigation)
        return investigation, True


def find_investigation(tenant_id: str, incident_id: str) -> Investigation | None:
    with session_scope() as session:
        investigation = session.exec(
            select(Investigation).where(
                Investigation.tenant_id == tenant_id,
                Investigation.incident_id == incident_id,
            )
        ).first()
        if investigation is not None:
            session.expunge(investigation)
        return investigation


# --------------------------------------------------------------------------- #
# FSM run (background task)
# --------------------------------------------------------------------------- #


def run_investigation(investigation_id: str) -> None:
    """queued -> gathering -> hypothesizing -> rca_ready, with writeback to Keep."""
    settings = get_settings()
    start = time.monotonic()
    metrics.investigations_active.inc()
    mode = "suggest"  # M0 is suggest-only; relabel if modes are introduced
    try:
        with session_scope() as session:
            investigation = session.get(Investigation, investigation_id)
            if investigation is None:
                logger.error("investigation vanished before run", extra={"investigation_id": investigation_id})
                return
            mode = investigation.mode
            tenant_id, incident_id = investigation.tenant_id, investigation.incident_id
            _set_status(session, investigation, "gathering")

        with investigation_span(investigation_id, tenant_id=tenant_id, incident_id=incident_id):
            with session_scope() as session:
                investigation = session.get(Investigation, investigation_id)
                evidence = _gather_evidence(session, investigation, settings)
                _build_and_store_context_pack(investigation, tenant_id, incident_id, settings)
                _set_status(session, investigation, "hypothesizing")
                knowledge_results = _query_knowledge_safe(investigation, evidence)
                rca_draft, hypotheses, citations = generate_rca(
                    investigation, evidence, investigation.context_pack, knowledge_results, settings=settings
                )
                for hypothesis in hypotheses:
                    session.add(
                        Hypothesis(
                            investigation_id=investigation.id,
                            title=hypothesis["title"],
                            confidence=hypothesis["confidence"],
                            supporting_evidence=hypothesis["supporting_evidence"],
                            supporting_knowledge=hypothesis["supporting_knowledge"],
                        )
                    )
                investigation.rca_draft = rca_draft
                investigation.rca_citations = citations
                _set_status(session, investigation, "rca_ready")
                session.add(investigation)

            _writeback(
                investigation_id,
                tenant_id,
                incident_id,
                rca_draft,
                evidence,
                citations=citations,
                knowledge=knowledge_results,
            )
        metrics.investigations_completed.labels(mode=mode).inc()
        metrics.investigation_duration.labels(mode=mode).observe(time.monotonic() - start)
    except Exception as exc:  # noqa: BLE001 — FSM must capture, never raise from a background task
        metrics.investigations_failed.labels(mode=mode).inc()
        metrics.investigation_duration.labels(mode=mode).observe(time.monotonic() - start)
        logger.exception("investigation failed", extra={"investigation_id": investigation_id})
        try:
            with session_scope() as session:
                investigation = session.get(Investigation, investigation_id)
                if investigation is not None:
                    investigation.error = f"{type(exc).__name__}: {exc}"
                    _set_status(session, investigation, "failed")
                    session.add(investigation)
        except Exception:  # noqa: BLE001
            logger.exception("could not mark investigation failed", extra={"investigation_id": investigation_id})
    finally:
        metrics.investigations_active.dec()


def _set_status(session, investigation: Investigation, status: str) -> None:
    investigation.status = status
    investigation.updated_at = _utcnow()
    session.add(investigation)


def _build_and_store_context_pack(
    investigation: Investigation, tenant_id: str, incident_id: str, settings: Settings
) -> None:
    """Assemble and persist the M2 context pack during the gathering phase.

    Lazy import avoids a module cycle; a context-pack failure must never fail
    the investigation, so exceptions are swallowed (the builder itself is
    already partial-failure tolerant — this guards construction-level bugs).
    """
    try:
        from aiops_api.modules.context_builder import build_context_pack

        investigation.context_pack = build_context_pack(tenant_id, incident_id, settings=settings)
    except Exception:  # noqa: BLE001
        logger.exception("context pack build failed", extra={"investigation_id": investigation.id})


# --------------------------------------------------------------------------- #
# Evidence gathering via the MCP gateway (all calls policy-gated, fail-closed)
# --------------------------------------------------------------------------- #


def _fetch_tool_catalog(client: httpx.Client, gateway_url: str) -> dict[str, dict]:
    response = client.get(f"{gateway_url}/v1/mcp/tools")
    response.raise_for_status()
    return {tool["name"]: tool for tool in response.json()}


def _invoke_tool(
    client: httpx.Client,
    gateway_url: str,
    catalog: dict[str, dict],
    tool: str,
    tenant_id: str,
    investigation_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one gateway tool after a fail-closed policy check."""
    descriptor = catalog.get(tool)
    # Fail-closed: unknown tools are denied exactly like mutate-class tools.
    assert_tool_allowed(tool, descriptor.get("execution_class") if descriptor else None)
    response = client.post(
        f"{gateway_url}/v1/mcp/tools/{tool}:invoke",
        json={"tenant_id": tenant_id, "investigation_id": investigation_id, "arguments": arguments},
    )
    response.raise_for_status()
    return response.json()


def _default_arguments(tool: str, pods_result: Any) -> dict[str, Any]:
    if tool == "get_logs":
        pod = _first_pod_name(pods_result)
        return {"pod": pod, "tail_lines": 100} if pod else {"pod": ""}
    return {}


def _first_pod_name(pods_result: Any) -> str | None:
    """Best-effort extraction of a pod name from a get_pods result."""
    items = None
    if isinstance(pods_result, dict):
        items = pods_result.get("pods") or pods_result.get("items")
    elif isinstance(pods_result, list):
        items = pods_result
    if not items:
        return None
    first = items[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        if isinstance(first.get("name"), str):
            return first["name"]
        metadata = first.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
            return metadata["name"]
    return None


def _summarize(tool: str, result: Any) -> str:
    if isinstance(result, dict):
        for key in ("pods", "items", "events"):
            value = result.get(key)
            if isinstance(value, list):
                return f"{tool}: {len(value)} {key} returned"
        if isinstance(result.get("logs"), str):
            lines = result["logs"].count("\n") + 1
            return f"{tool}: {lines} log lines returned"
    if isinstance(result, list):
        return f"{tool}: {len(result)} items returned"
    return f"{tool}: result received ({type(result).__name__})"


def _gather_evidence(session, investigation: Investigation, settings: Settings) -> list[Evidence]:
    evidence: list[Evidence] = []
    pods_result: Any = None
    with httpx.Client(timeout=INVESTIGATION_TIMEOUT) as client:
        catalog = _fetch_tool_catalog(client, settings.mcp_gateway_url)
        for tool in GATHER_TOOLS:
            arguments = _default_arguments(tool, pods_result)
            try:
                outcome = _invoke_tool(
                    client,
                    settings.mcp_gateway_url,
                    catalog,
                    tool,
                    investigation.tenant_id,
                    investigation.id,
                    arguments,
                )
                result, audit_id = outcome.get("result"), outcome.get("audit_id")
                metrics.mcp_tool_calls.labels(tool=tool, outcome="success").inc()
                if tool == "get_pods":
                    pods_result = result
                record = Evidence(
                    investigation_id=investigation.id,
                    tool=tool,
                    summary=_summarize(tool, result),
                    payload={"arguments": arguments, "result": result, "audit_id": audit_id},
                )
            except PolicyDenied as exc:
                # Policy denial on a read tool is a misconfiguration: record the
                # gap and keep gathering (fail-closed already prevented the call).
                metrics.mcp_tool_calls.labels(tool=tool, outcome="error").inc()
                metrics.evidence_gaps.labels(tool=tool).inc()
                record = Evidence(
                    investigation_id=investigation.id,
                    tool=tool,
                    summary=f"{tool}: evidence gap — policy denied ({exc.reason})",
                    payload={"arguments": arguments, "error": str(exc)},
                )
            except Exception as exc:  # noqa: BLE001 — tolerate individual tool failure
                metrics.mcp_tool_calls.labels(tool=tool, outcome="error").inc()
                metrics.evidence_gaps.labels(tool=tool).inc()
                logger.warning(
                    "tool invocation failed, recording evidence gap",
                    extra={"tool": tool, "investigation_id": investigation.id, "error": str(exc)},
                )
                record = Evidence(
                    investigation_id=investigation.id,
                    tool=tool,
                    summary=f"{tool}: evidence gap — {type(exc).__name__}: {exc}",
                    payload={"arguments": arguments, "error": f"{type(exc).__name__}: {exc}"},
                )
            session.add(record)
            evidence.append(record)
    return evidence


# --------------------------------------------------------------------------- #
# Knowledge retrieval (best-effort; never fails the investigation)
# --------------------------------------------------------------------------- #


def _query_knowledge_safe(investigation: Investigation, evidence: list[Evidence]) -> list[dict]:
    """Retrieve relevant knowledge docs in-process; [] when unavailable.

    The knowledge module lands in a sibling slice — import defensively and
    tolerate retrieval failure so hypothesizing degrades to evidence-only.
    """
    try:
        from aiops_api.modules.knowledge import query_knowledge
    except (ImportError, ModuleNotFoundError):
        return []
    query = " ".join(item.summary for item in evidence)[:500] or investigation.incident_id
    try:
        return list(query_knowledge(investigation.tenant_id, query, k=5))
    except Exception:  # noqa: BLE001
        logger.exception("knowledge retrieval failed", extra={"investigation_id": investigation.id})
        return []


# --------------------------------------------------------------------------- #
# RCA draft + writeback (suggest-only)
# --------------------------------------------------------------------------- #


def _citations_section(
    citations: dict[str, dict[str, str]] | None, evidence: list[Evidence], knowledge: list[dict]
) -> str:
    """Render the [E#]/[K#] reference list for the writeback comment."""
    if not citations:
        return ""
    lines = ["", "References:"]
    evidence_labels = {v: k for k, v in citations.get("evidence", {}).items()}
    for item in evidence:
        marker = evidence_labels.get(item.id)
        if marker:
            lines.append(f"- [{marker}] {item.tool}: {item.summary}")
    knowledge_labels = {v: k for k, v in citations.get("knowledge", {}).items()}
    for item in knowledge:
        marker = knowledge_labels.get(str(item.get("id", "")))
        if marker:
            source = item.get("source") or ""
            source_part = f" ({source})" if source else ""
            lines.append(f"- [{marker}] {item.get('title', '')}{source_part}")
    return "\n".join(lines) if len(lines) > 2 else ""


def _writeback(
    investigation_id: str,
    tenant_id: str,
    incident_id: str,
    rca_draft: str,
    evidence: list[Evidence],
    citations: dict[str, dict[str, str]] | None = None,
    knowledge: list[dict] | None = None,
) -> None:
    """Comment the RCA draft onto the Keep incident and set aiops.* enrichments."""
    from keep_client import KeepClient

    bullets = "\n".join(f"- {item.summary}" for item in evidence)
    references = _citations_section(citations, evidence, knowledge or [])
    with KeepClient.from_settings() as keep:
        incident = keep.get_incident(incident_id)
        name = incident.user_generated_name or incident_id
        header = f"Incident: {name} (severity: {incident.severity or 'unknown'}, alerts: {incident.alerts_count})"
        comment = f"{header}\n\n{rca_draft}\n\nEvidence summary:\n{bullets}{references}"
        keep.add_comment(incident_id, comment, status=incident.status)
        keep.enrich_incident(
            incident_id,
            {"aiops.investigation_id": investigation_id, "aiops.status": "rca_ready"},
        )
    logger.info("writeback complete", extra={"investigation_id": investigation_id, "incident_id": incident_id})
