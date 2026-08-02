"""Tool catalog API: GET /v1/tools.

The MCP gateway is a separate process and a security boundary (ADR-0002);
the browser must never reach it directly. The console UI needs to *show*
the catalog, so aiops-api re-exposes it read-only here and the UI keeps a
single upstream (its own `/api/aiops/*` proxy).

Each entry is annotated with the policy decision the gateway would make
for it, so the operator sees the effective posture — not just what is
registered. The evaluation is a dry run: nothing is invoked.
"""

import logging

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from aiops_api.db import get_engine
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.policy import engine as policy_engine
from aiops_api.modules.policy.models import GLOBAL_TENANT
from aiops_api.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tools", tags=["tools"])

CATALOG_TIMEOUT_SECONDS = 5.0


class ToolCatalogEntry(BaseModel):
    name: str
    description: str
    execution_class: str
    input_schema: dict
    # Provenance of this tool's data: "live" talks to a real system,
    # "stub" returns a canned demo payload. Passed through from the
    # gateway — dropping it would leave the console unable to tell an
    # operator which evidence is real.
    mode: str = "unknown"
    # Effective policy outcome for this tool right now (allow | deny |
    # approval_required). `policy_id` is null when the fail-closed default
    # produced the decision.
    decision: str
    policy_id: str | None = None


class ToolCatalogResponse(BaseModel):
    """`gateway_available=False` means the catalog could not be fetched;
    `tools` is then empty and `error` carries the reason. The UI renders
    that as a degraded state rather than an empty catalog."""

    gateway_url: str
    gateway_available: bool
    tools: list[ToolCatalogEntry]
    error: str | None = None


@router.get("")
def list_tools(
    context: TenantContext | None = Depends(get_tenant_context),
) -> ToolCatalogResponse:
    settings = get_settings()
    gateway_url = settings.mcp_gateway_url.rstrip("/")
    tenant_id = context.tenant_id if context is not None else GLOBAL_TENANT

    try:
        with httpx.Client(timeout=CATALOG_TIMEOUT_SECONDS) as client:
            response = client.get(f"{gateway_url}/v1/mcp/tools")
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:  # noqa: BLE001 — a dead gateway is a UI state, not a 500
        logger.warning("tool catalog fetch failed", exc_info=True)
        return ToolCatalogResponse(
            gateway_url=gateway_url,
            gateway_available=False,
            tools=[],
            error=f"{type(exc).__name__}: {exc}",
        )

    entries: list[ToolCatalogEntry] = []
    with Session(get_engine()) as session:
        for tool in raw:
            outcome = policy_engine.evaluate(
                session,
                tenant_id=tenant_id,
                tool_name=tool.get("name", ""),
                execution_class=tool.get("execution_class", ""),
                environment=settings.environment,
            )
            entries.append(
                ToolCatalogEntry(
                    name=tool.get("name", ""),
                    description=tool.get("description", ""),
                    execution_class=tool.get("execution_class", ""),
                    input_schema=tool.get("input_schema") or {},
                    mode=tool.get("mode") or "unknown",
                    decision=outcome.decision,
                    policy_id=outcome.policy_id,
                )
            )

    return ToolCatalogResponse(
        gateway_url=gateway_url,
        gateway_available=True,
        tools=sorted(entries, key=lambda e: e.name),
    )
