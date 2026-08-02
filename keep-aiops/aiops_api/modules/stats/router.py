"""Console stats API: GET /v1/stats.

Aggregates that the overview page needs in one round trip. This is a
convenience read over the OLTP tables — Prometheus (`/metrics`) stays the
source of truth for time series; these are point-in-time counts.

Tenant isolation follows the orchestrator read API: when auth is enabled
every count is scoped to the request tenant.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.feedback.models import InvestigationFeedback
from aiops_api.modules.orchestrator.models import Evidence, Investigation
from aiops_api.settings import get_settings

router = APIRouter(prefix="/v1/stats", tags=["stats"])

RECENT_WINDOW_HOURS = 24

# Mirrors the FSM in orchestrator.models.Investigation. Listed explicitly so
# the UI always gets every bucket, including the ones currently at zero.
INVESTIGATION_STATUSES = [
    "queued",
    "gathering",
    "hypothesizing",
    "rca_ready",
    "failed",
    "cancelled",
]


class BudgetLimits(BaseModel):
    """The configured caps, so the console can show the posture without
    the operator having to read the ConfigMap."""

    max_tool_calls: int
    max_wall_time_seconds: float
    max_llm_tokens: int


class StatsResponse(BaseModel):
    investigations_total: int
    investigations_by_status: dict[str, int]
    investigations_last_24h: int
    evidence_total: int
    evidence_gaps: int
    feedback_useful: int
    feedback_not_useful: int
    budget: BudgetLimits
    mode: str
    llm_enabled: bool


@router.get("")
def get_stats(
    context: TenantContext | None = Depends(get_tenant_context),
) -> StatsResponse:
    settings = get_settings()
    tenant_id = context.tenant_id if context is not None else None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_WINDOW_HOURS)

    with Session(get_engine()) as session:

        def scoped(statement):
            return statement.where(Investigation.tenant_id == tenant_id) if tenant_id else statement

        by_status = {status: 0 for status in INVESTIGATION_STATUSES}
        rows = session.exec(
            scoped(select(Investigation.status, func.count()).group_by(Investigation.status))
        ).all()
        for status, count in rows:
            by_status[status] = count

        recent = session.exec(
            scoped(select(func.count()).select_from(Investigation).where(Investigation.created_at >= cutoff))
        ).one()

        # Evidence has no tenant column — join through the investigation so
        # the count respects tenant scope like everything else here.
        evidence_stmt = select(func.count()).select_from(Evidence)
        gap_stmt = select(func.count()).select_from(Evidence).where(Evidence.summary.contains("evidence gap"))
        if tenant_id:
            scope_ids = select(Investigation.id).where(Investigation.tenant_id == tenant_id)
            evidence_stmt = evidence_stmt.where(Evidence.investigation_id.in_(scope_ids))
            gap_stmt = gap_stmt.where(Evidence.investigation_id.in_(scope_ids))
        evidence_total = session.exec(evidence_stmt).one()
        evidence_gaps = session.exec(gap_stmt).one()

        feedback_stmt = select(InvestigationFeedback.rating, func.count()).group_by(
            InvestigationFeedback.rating
        )
        if tenant_id:
            feedback_stmt = feedback_stmt.where(InvestigationFeedback.tenant_id == tenant_id)
        feedback = dict(session.exec(feedback_stmt).all())

    return StatsResponse(
        investigations_total=sum(by_status.values()),
        investigations_by_status=by_status,
        investigations_last_24h=recent,
        evidence_total=evidence_total,
        evidence_gaps=evidence_gaps,
        feedback_useful=feedback.get("useful", 0),
        feedback_not_useful=feedback.get("not_useful", 0),
        budget=BudgetLimits(
            max_tool_calls=settings.budget_max_tool_calls,
            max_wall_time_seconds=settings.budget_max_wall_time_seconds,
            max_llm_tokens=settings.budget_max_llm_tokens,
        ),
        mode="suggest",  # M0-M3 are suggest-only; M4 introduces other modes
        llm_enabled=bool(settings.llm_model),
    )
