"""Investigation FSM service (M3 — coordinator + specialists).

Flow for an eligible `incident.created` (severity in
settings.auto_investigate_severities):

1. create Investigation idempotently (unique on tenant_id+incident_id)
2. background: queued -> gathering — the coordinator runs the default
   roster of specialists (kubernetes, prometheus, datadog, aws_eks, aws_rds,
   argocd, jira, slack, bitbucket, backstage), each invocation policy-checked
   (fail-closed) and persisted as Evidence; individual tool failures become
   evidence-gap notes and the run continues; the per-investigation budget
   caps tool calls, wall-time and LLM tokens
3. gathering -> hypothesizing — assemble the context pack, retrieve knowledge
   (best-effort), then generate hypotheses + an RCA draft with [E#]/[K#]
   citations (LiteLLM when AIOPS_LLM_MODEL is set, deterministic rules
   otherwise); the LLM call consumes the token budget
4. hypothesizing -> rca_ready — write back to Keep: incident comment (draft +
   references section) + aiops.* enrichment keys

Any unexpected error moves the investigation to `failed` with the error text.
`BudgetExceeded` is the special case where the cost cap was hit — the
tracker is reported in the error string so the operator can see what blew.
`incident.updated` is a no-op when an investigation exists (logged).
`incident.resolved` sets `incident_resolved=True` and keeps the status
(rca_ready) so the draft remains available — see models.Investigation.
"""

import logging
import time
from typing import Any

from sqlmodel import select

from aiops_api import metrics
from aiops_api.db import session_scope
from aiops_api.modules.config.models import GLOBAL_TENANT
from aiops_api.modules.event_bridge.schemas import EventType, KeepEventEnvelope
from aiops_api.modules.orchestrator.models import Evidence, Investigation, _utcnow
from aiops_api.modules.rca import Hypothesis, generate_rca
from aiops_api.modules.specialists.base import Budget, BudgetExceeded
from aiops_api.modules.specialists.coordinator import run_specialists
from aiops_api.modules.specialists.tracker import BudgetTracker
from aiops_api.settings import Settings, get_settings
from aiops_api.telemetry import investigation_span

logger = logging.getLogger(__name__)


def _budget_for(tenant_id: str, settings: Settings) -> Budget:
    """Budget from the persisted agent config, falling back to env."""
    from aiops_api.modules.config import get_effective_config

    config = get_effective_config(tenant_id)
    return Budget(
        tool_calls=config.budget_max_tool_calls,
        wall_time=config.budget_max_wall_time_seconds,
        llm_tokens=config.budget_max_llm_tokens,
    )


# --------------------------------------------------------------------------- #
# Event dispatch
# --------------------------------------------------------------------------- #


def handle_event(event: KeepEventEnvelope, background_tasks: Any, settings: Settings | None = None) -> Investigation | None:
    """Dispatch a validated Keep event into the orchestrator."""
    settings = settings or get_settings()

    if event.type == EventType.INCIDENT_CREATED:
        from aiops_api.modules.config import get_effective_config

        severities = get_effective_config(event.tenantid).auto_investigate_severities
        if event.data.severity.value not in severities:
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
    # Budget is resolved after the tenant is known — caps are per-tenant
    # configurable, so a global default would be the wrong ceiling.
    budget = _budget_for(GLOBAL_TENANT, settings)
    tracker: BudgetTracker | None = None
    try:
        with session_scope() as session:
            investigation = session.get(Investigation, investigation_id)
            if investigation is None:
                logger.error("investigation vanished before run", extra={"investigation_id": investigation_id})
                return
            mode = investigation.mode
            tenant_id, incident_id = investigation.tenant_id, investigation.incident_id
            _set_status(session, investigation, "gathering")
        budget = _budget_for(tenant_id, settings)

        with investigation_span(investigation_id, tenant_id=tenant_id, incident_id=incident_id):
            with session_scope() as session:
                investigation = session.get(Investigation, investigation_id)
                evidence, tracker = _gather_phase(investigation, settings, budget)
                for item in evidence:
                    session.add(item)
                _build_and_store_context_pack(investigation, tenant_id, incident_id, settings)
                _set_status(session, investigation, "hypothesizing")
                knowledge_results, knowledge_gap = _query_knowledge_safe(
                    investigation, evidence
                )
                if knowledge_gap is not None:
                    evidence.append(knowledge_gap)
                    session.add(knowledge_gap)
                rca_draft, hypotheses, citations = generate_rca(
                    investigation, evidence, investigation.context_pack, knowledge_results, settings=settings
                )
                # Charge the LLM (or fallback) cost to the budget so a runaway
                # token series fails the investigation on the next check.
                _charge_llm(tracker, rca_draft, citations)
                for hypothesis in hypotheses:
                    session.add(
                        Hypothesis(
                            investigation_id=investigation.id,
                            title=hypothesis["title"],
                            confidence=hypothesis["confidence"],
                            supporting_evidence=hypothesis["supporting_evidence"],
                            supporting_knowledge=hypothesis["supporting_knowledge"],
                            # Persist the corroboration verdict; without it
                            # the API returns a discounted confidence with
                            # no explanation of why it is low.
                            corroborated=bool(hypothesis.get("corroborated", True)),
                            caveat=hypothesis.get("caveat"),
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
    except BudgetExceeded as exc:
        # Cost cap hit — surface a clean "failed" status with the breach detail.
        metrics.investigations_failed.labels(mode=mode).inc()
        metrics.investigation_duration.labels(mode=mode).observe(time.monotonic() - start)
        metrics.investigation_cost_exceeded.labels(kind=exc.kind).inc()
        logger.warning(
            "investigation failed: budget exceeded",
            extra={"investigation_id": investigation_id, "kind": exc.kind, "limit": exc.limit},
        )
        _mark_failed(investigation_id, f"{type(exc).__name__}({exc.kind}): {exc}")
    except Exception as exc:  # noqa: BLE001 — FSM must capture, never raise from a background task
        metrics.investigations_failed.labels(mode=mode).inc()
        metrics.investigation_duration.labels(mode=mode).observe(time.monotonic() - start)
        logger.exception("investigation failed", extra={"investigation_id": investigation_id})
        _mark_failed(investigation_id, f"{type(exc).__name__}: {exc}")
    finally:
        if tracker is not None:
            snap = tracker.snapshot()
            metrics.investigation_cost.labels(kind="wall_time_seconds").inc(int(snap["wall_time"]))
        metrics.investigations_active.dec()


def _gather_phase(
    investigation: Investigation, settings: Settings, budget: Budget
) -> tuple[list[Evidence], BudgetTracker]:
    """Run the coordinator against the MCP gateway; persist nothing here.

    The orchestrator owns the session and the persistence step; the
    coordinator is a pure function over the gateway. The returned tracker
    is the same one the coordinator used, so the LLM phase can charge the
    same budget.
    """
    evidence, _results, tracker = run_specialists(
        investigation_id=investigation.id,
        tenant_id=investigation.tenant_id,
        gateway_url=settings.mcp_gateway_url,
        budget=budget,
    )
    return evidence, tracker


def _charge_llm(tracker: BudgetTracker | None, rca_draft: str, citations: dict | None) -> None:
    """Best-effort cost charge for the RCA writer call.

    The deterministic fallback is zero tokens. The LiteLLM path returns a
    ``usage`` object we can trust; the engine writes the count into
    ``rca_citations['_llm_total_tokens']`` so the tracker picks it up.
    """
    if tracker is None or not isinstance(citations, dict):
        return
    used = citations.get("_llm_total_tokens") or 0
    try:
        tracker.record_llm_tokens(int(used))
    except BudgetExceeded:
        # Let the outer FSM handler mark this failed.
        raise


def _mark_failed(investigation_id: str, error_text: str) -> None:
    try:
        with session_scope() as session:
            investigation = session.get(Investigation, investigation_id)
            if investigation is not None:
                investigation.error = error_text
                _set_status(session, investigation, "failed")
                session.add(investigation)
    except Exception:  # noqa: BLE001
        logger.exception("could not mark investigation failed", extra={"investigation_id": investigation_id})


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
# Knowledge retrieval (best-effort; never fails the investigation)
# --------------------------------------------------------------------------- #




def _query_knowledge_safe(
    investigation: Investigation, evidence: list[Evidence]
) -> tuple[list[dict], Evidence | None]:
    """Retrieve relevant knowledge docs in-process.

    Returns ``(results, gap)``. The knowledge module lands in a sibling
    slice — import defensively and tolerate retrieval failure so
    hypothesizing degrades to evidence-only. The gap is what makes that
    degradation visible instead of silent.
    """
    try:
        from aiops_api.modules.knowledge import query_knowledge
    except (ImportError, ModuleNotFoundError):
        # Not installed in this deployment: an absence by design, not a
        # failure, so nothing to report.
        return [], None
    query = " ".join(item.summary for item in evidence)[:500] or investigation.incident_id
    try:
        with investigation_span(investigation.id, name="knowledge.retrieve", query=query) as span:
            results = list(query_knowledge(investigation.tenant_id, query, k=5))
            span.set_attribute("results_count", len(results))
            return results, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("knowledge retrieval failed", extra={"investigation_id": investigation.id})
        # Reported as an evidence gap, not swallowed. Returning a bare []
        # made "no runbook matched this incident" and "the knowledge search
        # broke" produce identical drafts, so hypotheses silently lost
        # their grounding with nothing on the page to say so.
        return [], Evidence(
            investigation_id=investigation.id,
            tool="knowledge_search",
            summary=f"evidence gap — knowledge retrieval failed: {type(exc).__name__}: {exc}",
            backend="gap",
        )


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
