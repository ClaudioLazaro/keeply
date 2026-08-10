"""What a model turned out to accept, learned from its own refusals.

Three separate 400s were needed to get the workflow builder talking to a
DeepSeek reasoning model — the `developer` role, a compelled `tool_choice`,
and a replayed tool call missing `reasoning_content`. Each was found in
production, by a person using the product, after the previous one was fixed.

Hardcoding the resulting workarounds would have solved exactly one model.
Vendors add reasoning modes to existing model names, so the next 400 is a
matter of time, and the operator has no way to know which knob to turn.

So the shims are recorded rather than assumed. The client tries the strong
form, and when the provider refuses with a signature we recognise, it
downgrades, retries, and reports what it learned here. The next request for
that model starts from the known-good shape.

Two properties this must keep:

**Evidence, always.** A stored downgrade carries the provider's verbatim
error. A downgrade with no cause on record is indistinguishable from a bug,
and this codebase has already paid for treating an unexplained state as fine.

**Never authoritative over the operator.** An explicit `on`/`off` wins. What
is learned here only fills in `auto`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import select

from aiops_api.db import session_scope
from aiops_api.modules.config.models import GLOBAL_TENANT, LlmCapability, _utcnow

logger = logging.getLogger(__name__)

# The downgrades a client may report. Closed on purpose: an unknown name
# would be stored, shown in the UI as an applied workaround, and mean
# nothing to anyone reading it.
#
# **The implementation is the source of truth, and it is not here.** These
# shims live in `keep-ui/shared/lib/server/openAiCompatFetch.ts`
# (`ALL_DOWNGRADES`); this list only decides what may be persisted. The
# duplication is deliberate — a validator that trusts its input is not a
# validator — but the drift it invites is not: adding a shim on the client
# without adding its name here silently drops the record.
#
# So drift is reported rather than merely logged. `record()` returns the
# names it refused, and the API hands them back to the caller, which is the
# one party in a position to notice.
KNOWN_DOWNGRADES: tuple[str, ...] = (
    # `developer` role rejected — send `system`.
    "developer_role",
    # `required`/forced function rejected — send `auto`.
    "tool_choice",
    # replayed tool call needs the field present — send an empty string.
    "reasoning_content",
)


def _key(provider: str | None, model: str | None) -> tuple[str, str]:
    return ((provider or "").strip().lower(), (model or "").strip())


def get(tenant_id: str, provider: str | None, model: str | None) -> dict[str, Any] | None:
    """What is known about this model, or None if it has never been tried."""
    provider_key, model_key = _key(provider, model)
    if not model_key:
        return None
    try:
        with session_scope() as session:
            row = session.exec(
                select(LlmCapability)
                .where(LlmCapability.tenant_id == tenant_id)
                .where(LlmCapability.provider == provider_key)
                .where(LlmCapability.model == model_key)
            ).first()
            if row is None:
                return None
            return {
                "provider": row.provider,
                "model": row.model,
                "downgrades": list(row.downgrades or []),
                "evidence": row.evidence,
                "observed_at": row.observed_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
    except Exception:  # noqa: BLE001 — an unavailable table must not block a chat
        logger.info("capability lookup failed; assuming nothing is known", exc_info=True)
        return None


def record(
    tenant_id: str,
    provider: str | None,
    model: str | None,
    downgrades: list[str],
    evidence: str | None = None,
) -> dict[str, Any] | None:
    """Store what a model needed. Unknown downgrade names are dropped.

    Accumulative: a model may reveal its constraints one refusal at a time,
    exactly as this one did, so a later report adds to the set rather than
    replacing it. The empty list is still meaningful — it records that the
    strong form worked, which is why "no downgrades" and "never tried" are
    kept distinguishable.
    """
    provider_key, model_key = _key(provider, model)
    if not model_key:
        return None

    accepted = [name for name in downgrades if name in KNOWN_DOWNGRADES]
    rejected = sorted(set(downgrades) - set(KNOWN_DOWNGRADES))
    if rejected:
        logger.warning(
            "refused unrecognised downgrade names; this list is behind the client",
            extra={"unknown": rejected},
        )

    try:
        with session_scope() as session:
            row = session.exec(
                select(LlmCapability)
                .where(LlmCapability.tenant_id == tenant_id)
                .where(LlmCapability.provider == provider_key)
                .where(LlmCapability.model == model_key)
            ).first()
            if row is None:
                row = LlmCapability(
                    tenant_id=tenant_id,
                    provider=provider_key,
                    model=model_key,
                    downgrades=sorted(accepted),
                    evidence=(evidence or None),
                )
            else:
                merged = sorted(set(row.downgrades or []) | set(accepted))
                row.downgrades = merged
                # Keep the newest cause; the older one is already reflected
                # in the accumulated set.
                if evidence:
                    row.evidence = evidence
                row.updated_at = _utcnow()
            session.add(row)
            session.flush()
            result = {
                "provider": row.provider,
                "model": row.model,
                "downgrades": list(row.downgrades or []),
                "evidence": row.evidence,
                # Handed back so the caller — which implements these — can
                # see that this service does not know about one of them.
                "rejected": rejected,
            }
    except Exception:  # noqa: BLE001 — learning is an optimisation, not a dependency
        logger.warning("could not record model capability", exc_info=True)
        return None

    logger.info(
        "recorded model capability",
        extra={"provider": provider_key, "model": model_key, "downgrades": result["downgrades"]},
    )
    return result


def list_all(tenant_id: str = GLOBAL_TENANT) -> list[dict[str, Any]]:
    """Everything learned, for the settings page."""
    try:
        with session_scope() as session:
            rows = session.exec(
                select(LlmCapability).where(LlmCapability.tenant_id == tenant_id)
            ).all()
            return [
                {
                    "provider": row.provider,
                    "model": row.model,
                    "downgrades": list(row.downgrades or []),
                    "evidence": row.evidence,
                    "observed_at": row.observed_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in sorted(rows, key=lambda r: (r.provider, r.model))
            ]
    except Exception:  # noqa: BLE001
        logger.info("capability listing unavailable", exc_info=True)
        return []
