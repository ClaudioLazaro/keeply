"""POST /v1/events/keep — signed CloudEvents webhook receiver.

Verifies `X-Keep-Signature: sha256=<hmac_hex>` over the raw body with
settings.webhook_secret (401 on mismatch), validates the envelope, dedupes on
the event id (persisted), and dispatches to the orchestrator. Always 202 once
the event is durably accepted (duplicates included).
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError
from sqlmodel import Session

from aiops_api.db import get_engine
from aiops_api.modules.event_bridge.models import ProcessedEvent
from aiops_api.modules.event_bridge.schemas import KeepEventEnvelope
from aiops_api.modules.orchestrator import service as orchestrator
from aiops_api.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["event-bridge"])

SIGNATURE_PREFIX = "sha256="


def _verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> None:
    if not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        raise HTTPException(status_code=401, detail="missing or malformed X-Keep-Signature")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len(SIGNATURE_PREFIX):]
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="signature mismatch")


@router.post("/keep", status_code=202)
async def receive_keep_event(request: Request, background_tasks: BackgroundTasks) -> dict:
    settings = get_settings()
    raw_body = await request.body()
    _verify_signature(raw_body, request.headers.get("x-keep-signature"), settings.webhook_secret)

    try:
        envelope = KeepEventEnvelope.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid event envelope: {exc.errors()}") from exc

    with Session(get_engine()) as session:
        if session.get(ProcessedEvent, envelope.id) is not None:
            logger.info("duplicate event, already accepted", extra={"event_id": envelope.id})
            return {"accepted": True, "event_id": envelope.id, "duplicate": True}
        session.add(
            ProcessedEvent(
                id=envelope.id,
                event_type=envelope.type.value,
                tenant_id=envelope.tenantid,
                subject=envelope.subject,
            )
        )
        session.commit()

    orchestrator.handle_event(envelope, background_tasks, settings)
    return {"accepted": True, "event_id": envelope.id}
