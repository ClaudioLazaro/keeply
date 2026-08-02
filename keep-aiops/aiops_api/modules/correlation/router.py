"""The endpoint Keep calls to hand us a tenant, plus run/audit views.

`POST /remind_about_the_client` is Keep's external-AI contract: it fires
this with a 0.5s timeout and ignores the response, so the handler must
return immediately and do the work in the background.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.modules.correlation import service
from aiops_api.modules.correlation.models import CorrelationDecision

logger = logging.getLogger(__name__)

router = APIRouter(tags=["correlation"])


class RemindRequest(BaseModel):
    """Exactly the body Keep sends (keep/api/models/ai_external.py)."""

    tenant_id: str
    back_api_key: str
    back_api_url: str
    # Keep also sends the algorithm's own api_key; we do not need it —
    # the endpoint is already behind the control plane's auth.
    api_key: str | None = None


@router.post("/remind_about_the_client")
def remind_about_the_client(
    body: RemindRequest, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Register the tenant and kick off a correlation pass.

    Keep gives this 0.5 seconds before giving up, so the response is
    immediate and the run happens in the background.
    """
    service.register_client(
        tenant_id=body.tenant_id,
        back_api_url=body.back_api_url,
        back_api_key=body.back_api_key,
    )
    background_tasks.add_task(_run_safely, body.tenant_id)
    return {"status": "accepted", "tenant_id": body.tenant_id}


def _run_safely(tenant_id: str) -> None:
    try:
        for client in service.active_clients():
            if client.tenant_id == tenant_id:
                service.run_for_client(client)
                return
    except Exception:  # noqa: BLE001 — background task must never raise
        logger.exception("correlation run failed", extra={"tenant_id": tenant_id})


@router.get("/v1/correlation/decisions")
def list_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """Audit trail — what was correlated, how sure, and on what evidence.

    Auto-merge is destructive; this is how a wrong grouping gets traced
    back to the signals and settings that produced it.
    """
    with Session(get_engine()) as session:
        decisions = session.exec(
            select(CorrelationDecision)
            .order_by(CorrelationDecision.created_at.desc())
            .limit(min(limit, 500))
        ).all()
        return [
            {
                "id": decision.id,
                "outcome": decision.outcome,
                "confidence": decision.confidence,
                "explanation": decision.explanation,
                "alert_fingerprints": decision.alert_fingerprints,
                "incident_id": decision.incident_id,
                "settings_snapshot": decision.settings_snapshot,
                "created_at": decision.created_at.isoformat(),
            }
            for decision in decisions
        ]
