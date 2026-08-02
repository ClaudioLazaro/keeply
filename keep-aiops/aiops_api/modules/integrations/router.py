"""Integration API: GET /v1/integrations, plus the gateway pull endpoint.

Read-only on purpose. Credentials are installed and rotated in Keep's
provider UI (`/providers`); duplicating that here would mean two secret
stores, two rotation paths and an operator configuring Datadog twice.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends

from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.integrations import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])


@router.get("")
def list_integrations(
    context: TenantContext | None = Depends(get_tenant_context),
) -> list[dict[str, Any]]:
    """Which Keep provider backs each MCP tool group, and its live/stub mode."""
    del context
    return service.describe_all()


@router.get("/resolved")
def resolved(
    context: TenantContext | None = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Credentials for the MCP gateway to pull, sourced from Keep providers.

    Behind the same auth as the rest of the control plane. Never call this
    from the browser — it is the one endpoint that returns credentials.
    """
    del context
    return service.resolved_for_gateway()
