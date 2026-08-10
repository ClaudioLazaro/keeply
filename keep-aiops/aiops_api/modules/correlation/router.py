"""The endpoint Keep calls to hand us a tenant, plus run/audit views.

`POST /remind_about_the_client` is Keep's external-AI contract: it fires
this with a 0.5s timeout and ignores the response, so the handler must
return immediately and do the work in the background.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from aiops_api.db import get_engine
from aiops_api.modules.correlation import service
from aiops_api.modules.correlation.models import RuleSuggestion

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
            if client.tenant_id != tenant_id:
                continue
            # Gate BEFORE reading anything. `fetch_config` calls Keep's
            # `/ai/stats`, and that endpoint calls `remind_about_the_client`
            # — which lands back here. Reading the config to decide whether
            # to run is therefore a recursive loop, and it ran at ~50
            # requests/second until it was caught.
            #
            # So the cheap floor is checked with no I/O at all. The full
            # window-based pacing still applies inside run_for_client's own
            # path, where the config has been read for other reasons
            # anyway.
            if not service.due_for_run(client, window_minutes=0.0):
                # Not an error and not worth a warning: the reminder is a
                # liveness signal from a UI poll, and re-deriving the same
                # groups from the same alerts is pure cost.
                logger.debug(
                    "correlation reminder inside the last run's window, skipping",
                    extra={"tenant_id": tenant_id},
                )
                return
            service.run_for_client(client)
            return
    except Exception:  # noqa: BLE001 — background task must never raise
        logger.exception("correlation run failed", extra={"tenant_id": tenant_id})


@router.get("/v1/correlation/suggestions")
def list_suggestions(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Rule proposals waiting on a decision.

    Each carries the evidence behind it — how often the pattern recurred
    and how many alerts it covered — because a rule an operator cannot
    justify is a rule they should not accept.
    """
    with Session(get_engine()) as session:
        query = select(RuleSuggestion).order_by(
            RuleSuggestion.occurrences.desc(), RuleSuggestion.created_at.desc()
        )
        if status:
            query = query.where(RuleSuggestion.status == status)
        suggestions = session.exec(query.limit(min(limit, 500))).all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "cel": s.cel,
                "grouping_criteria": s.grouping_criteria,
                "timeframe_seconds": s.timeframe_seconds,
                "occurrences": s.occurrences,
                "alerts_covered": s.alerts_covered,
                "rationale": s.rationale,
                "status": s.status,
                "created_rule_id": s.created_rule_id,
                "created_at": s.created_at.isoformat(),
            }
            for s in suggestions
        ]


@router.post("/v1/correlation/suggestions/{suggestion_id}:accept")
def accept(suggestion_id: str) -> dict[str, Any]:
    """Create the Keep rule this suggestion describes.

    From here on the rule lives in /rules like any other — Keep's engine
    executes it, and it is edited or deleted there.
    """
    try:
        return service.accept_suggestion(suggestion_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.CredentialRejected as exc:
        # 503, not 502: the request was fine and will succeed on retry.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface Keep's rejection verbatim
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


@router.post("/v1/correlation/suggestions/{suggestion_id}:dismiss")
def dismiss(suggestion_id: str) -> dict[str, Any]:
    try:
        return service.dismiss_suggestion(suggestion_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
