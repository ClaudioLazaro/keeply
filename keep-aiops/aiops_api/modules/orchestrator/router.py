"""Read API for investigations and their evidence.

Tenant isolation: when auth is enabled (TenantContext present on the request)
every query is scoped to the request tenant and cross-tenant access returns
404 — never 403 — so existence of another tenant's data is not leaked. When
auth is disabled (dev/test only) the legacy unfiltered behavior applies.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.orchestrator.models import Evidence, Investigation
from aiops_api.modules.rca.models import Hypothesis

router = APIRouter(prefix="/v1/investigations", tags=["orchestrator"])


def _get_scoped(session: Session, investigation_id: str, context: TenantContext | None) -> Investigation:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None or (context is not None and investigation.tenant_id != context.tenant_id):
        raise HTTPException(status_code=404, detail="investigation not found")
    return investigation


@router.get("")
def list_investigations(
    incident_id: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    context: TenantContext | None = Depends(get_tenant_context),
) -> list[Investigation]:
    with Session(get_engine()) as session:
        statement = select(Investigation)
        if incident_id is not None:
            statement = statement.where(Investigation.incident_id == incident_id)
        if context is not None:
            # Authenticated: scope is forced to the request tenant; the
            # tenant_id param can only narrow (never widen) that scope.
            statement = statement.where(Investigation.tenant_id == context.tenant_id)
        if tenant_id is not None:
            statement = statement.where(Investigation.tenant_id == tenant_id)
        return list(session.exec(statement.order_by(Investigation.created_at)).all())


@router.get("/{investigation_id}")
def get_investigation(
    investigation_id: str,
    context: TenantContext | None = Depends(get_tenant_context),
) -> Investigation:
    with Session(get_engine()) as session:
        return _get_scoped(session, investigation_id, context)


@router.get("/{investigation_id}/evidence")
def get_investigation_evidence(
    investigation_id: str,
    context: TenantContext | None = Depends(get_tenant_context),
) -> list[Evidence]:
    with Session(get_engine()) as session:
        _get_scoped(session, investigation_id, context)
        statement = (
            select(Evidence)
            .where(Evidence.investigation_id == investigation_id)
            .order_by(Evidence.created_at)
        )
        return list(session.exec(statement).all())


@router.get("/{investigation_id}/hypotheses")
def get_investigation_hypotheses(
    investigation_id: str,
    context: TenantContext | None = Depends(get_tenant_context),
) -> list[Hypothesis]:
    with Session(get_engine()) as session:
        _get_scoped(session, investigation_id, context)
        statement = (
            select(Hypothesis)
            .where(Hypothesis.investigation_id == investigation_id)
            .order_by(Hypothesis.created_at)
        )
        return list(session.exec(statement).all())
