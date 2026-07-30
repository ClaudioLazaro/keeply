"""Human feedback API for investigations.

One feedback entry per investigation: POST upserts (a repeated POST replaces
rating/comment and refreshes updated_at). Tenant isolation mirrors the
orchestrator read API: cross-tenant access returns 404 — never 403 — so the
existence of another tenant's data is not leaked.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.metrics import investigation_feedback
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.feedback.models import InvestigationFeedback
from aiops_api.modules.orchestrator.router import _get_scoped

router = APIRouter(prefix="/v1/investigations", tags=["feedback"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackRequest(BaseModel):
    rating: Literal["useful", "not_useful"]
    comment: str | None = None


@router.post("/{investigation_id}/feedback", response_model=InvestigationFeedback)
def upsert_feedback(
    investigation_id: str,
    body: FeedbackRequest,
    context: TenantContext | None = Depends(get_tenant_context),
) -> InvestigationFeedback:
    with Session(get_engine()) as session:
        investigation = _get_scoped(session, investigation_id, context)
        existing = session.exec(
            select(InvestigationFeedback).where(InvestigationFeedback.investigation_id == investigation_id)
        ).first()
        if existing is None:
            feedback = InvestigationFeedback(
                investigation_id=investigation_id,
                tenant_id=investigation.tenant_id,
                rating=body.rating,
                comment=body.comment,
            )
            session.add(feedback)
            try:
                session.commit()
            except IntegrityError:
                # Concurrent first POST won the unique constraint on
                # investigation_id — fall back to updating that row.
                session.rollback()
                feedback = session.exec(
                    select(InvestigationFeedback).where(
                        InvestigationFeedback.investigation_id == investigation_id
                    )
                ).one()
                feedback.rating = body.rating
                feedback.comment = body.comment
                feedback.updated_at = _utcnow()
                session.add(feedback)
                session.commit()
        else:
            existing.rating = body.rating
            existing.comment = body.comment
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            feedback = existing
        session.refresh(feedback)
        investigation_feedback.labels(rating=feedback.rating).inc()
        return feedback


@router.get("/{investigation_id}/feedback", response_model=InvestigationFeedback)
def get_feedback(
    investigation_id: str,
    context: TenantContext | None = Depends(get_tenant_context),
) -> InvestigationFeedback:
    with Session(get_engine()) as session:
        _get_scoped(session, investigation_id, context)
        feedback = session.exec(
            select(InvestigationFeedback).where(InvestigationFeedback.investigation_id == investigation_id)
        ).first()
        if feedback is None:
            raise HTTPException(status_code=404, detail="feedback not found")
        return feedback
