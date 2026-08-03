"""MCP Gateway — the only path from agent runtimes to tools (ADR-0002).

Separate FastAPI process exposing:
  GET  /healthz
  GET  /v1/mcp/tools                      -> catalog of registered tools
  POST /v1/mcp/tools/{tool}:invoke        -> policy-gated, audited invocation

Policy gate (M0, fail-closed): only execution_class == "read" tools are
invocable; unknown or mutate-class tools get 403.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from mcp_gateway.audit import build_audit_entry, record_audit
from mcp_gateway.settings import get_settings
from mcp_gateway.telemetry import init_telemetry, tool_span
from mcp_gateway.tools import catalog, get_tool, validate_arguments
from mcp_gateway.tools.backend import BackendUnavailable, ResourceNotFound

class InvokeRequest(BaseModel):
    tenant_id: str
    investigation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    result: Any
    audit_id: str


def create_app() -> FastAPI:
    settings = get_settings()
    init_telemetry()
    app = FastAPI(title="Keep AIOps MCP Gateway", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name, "k8s_mode": settings.k8s_mode}

    @app.get("/v1/mcp/tools")
    def list_tools() -> list[dict[str, Any]]:
        return catalog()

    @app.post("/v1/mcp/tools/{tool}:invoke", response_model=InvokeResponse)
    async def invoke_tool(tool: str, body: InvokeRequest) -> InvokeResponse:
        audit_id = str(uuid.uuid4())
        outcome = "success"
        started = time.perf_counter()
        try:
            spec = get_tool(tool)
            # Policy gate — fail closed: M0 is suggest-only, only read-class tools run.
            if spec is None or spec.execution_class != "read":
                outcome = "policy_denied"
                raise HTTPException(
                    status_code=403,
                    detail=f"tool '{tool}' is unknown or not read-class; invocation denied (policy fail-closed)",
                )

            errors = validate_arguments(spec.input_schema, body.arguments)
            if errors:
                outcome = "validation_error"
                raise HTTPException(status_code=422, detail=errors)

            with tool_span(tool=tool, tenant_id=body.tenant_id, investigation_id=body.investigation_id):
                try:
                    result = await run_in_threadpool(spec.handler, **body.arguments)
                except BackendUnavailable as exc:
                    # Live backend (k8s, prometheus, datadog, eks, rds,
                    # argocd, jira, slack, bitbucket, backstage) is
                    # unreachable or unconfigured. 503 + retry hint so the
                    # orchestrator can mark an evidence gap and keep going.
                    outcome = "backend_unavailable"
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": str(exc),
                            "hint": "tool backend temporarily unavailable; retry with backoff",
                            "retry_after_seconds": exc.retry_after_seconds,
                        },
                    ) from exc
                except ResourceNotFound as exc:
                    # The backend answered; the resource simply is not there.
                    # Reporting that as 503 made an evidence gap read as a
                    # broken gateway.
                    outcome = "not_found"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": str(exc),
                            "hint": "the backend is healthy; this resource does not exist",
                        },
                    ) from exc
                except ValueError as exc:
                    outcome = "validation_error"
                    raise HTTPException(status_code=422, detail=[str(exc)]) from exc
            return InvokeResponse(result=result, audit_id=audit_id)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            entry = build_audit_entry(
                audit_id=audit_id,
                tenant_id=body.tenant_id,
                investigation_id=body.investigation_id,
                tool=tool,
                arguments=body.arguments,
                outcome=outcome,
                duration_ms=duration_ms,
            )
            record_audit(entry, settings.audit_log_path)

    return app


app = create_app()
