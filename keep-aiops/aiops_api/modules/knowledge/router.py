"""Knowledge API: POST /v1/knowledge/query + POST /v1/knowledge/sources:seed.

Tenant isolation mirrors the orchestrator read API: when auth is enabled the
request tenant is forced from TenantContext (the body's tenant_id can never
widen scope); when auth is disabled (dev/test only) the body's tenant_id is
required so the mandatory tenant filter always has a value.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from aiops_api.db import get_engine, session_scope
from aiops_api.modules.auth import TenantContext, get_tenant_context
from aiops_api.modules.knowledge import ingest, retriever

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeQueryRequest(BaseModel):
    tenant_id: str | None = None  # required only when auth is disabled (dev/test)
    query: str
    k: int = Field(default=5, ge=1, le=50)


class KnowledgeQueryResult(BaseModel):
    id: str
    title: str
    source: str
    chunk: str
    score: float


class KnowledgeQueryResponse(BaseModel):
    results: list[KnowledgeQueryResult]


class SeedSourcesRequest(BaseModel):
    tenant_id: str | None = None  # dev/test only; auth forces the request tenant


class SeedSourcesResponse(BaseModel):
    tenant_id: str
    seeded: int


def _resolve_tenant(body_tenant: str | None, context: TenantContext | None) -> str:
    if context is not None:
        return context.tenant_id
    if body_tenant:
        return body_tenant
    raise HTTPException(status_code=422, detail="tenant_id is required when auth is disabled")


@router.post("/query")
def query_knowledge_endpoint(
    body: KnowledgeQueryRequest,
    context: TenantContext | None = Depends(get_tenant_context),
) -> KnowledgeQueryResponse:
    tenant_id = _resolve_tenant(body.tenant_id, context)
    with Session(get_engine()) as session:
        results = retriever.query(session, tenant_id, body.query, body.k)
    return KnowledgeQueryResponse(results=[KnowledgeQueryResult(**item) for item in results])


@router.post("/sources:seed")
def seed_sources_endpoint(
    body: SeedSourcesRequest | None = None,
    context: TenantContext | None = Depends(get_tenant_context),
) -> SeedSourcesResponse:
    """Dev convenience: (re)seed runbooks for the request tenant. Idempotent."""
    tenant_id = _resolve_tenant(body.tenant_id if body else None, context)
    with session_scope() as session:
        seeded = ingest.seed_runbooks(session, tenant_id)
    return SeedSourcesResponse(tenant_id=tenant_id, seeded=seeded)
