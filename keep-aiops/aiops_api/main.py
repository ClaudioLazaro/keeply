"""aiops-api entry point — modular monolith for the AIOps control plane."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aiops_api.db import init_db
from aiops_api.metrics import setup_metrics
from aiops_api.settings import get_settings
from aiops_api.telemetry import init_telemetry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.auth_enabled:
        logger.warning(
            "AIOPS_AUTH_ENABLED=false: tenant auth DISABLED — dev/test only, "
            "read paths are not tenant-filtered"
        )
    init_db()
    from aiops_api.modules.knowledge.ingest import seed_global_runbooks
    from aiops_api.modules.policy.engine import seed_default_policies

    seed_default_policies()
    seed_global_runbooks()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="keep-aiops", version="0.1.0", lifespan=lifespan)
    init_telemetry(app)

    from aiops_api.modules.auth import TenantAuthMiddleware

    app.add_middleware(TenantAuthMiddleware)

    # Rejecting a pasted credential is not enough — the default 422 body
    # echoes the submitted value straight back to the caller.
    from fastapi.exceptions import RequestValidationError

    from aiops_api.modules.config.errors import validation_exception_handler

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    setup_metrics(app)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": settings.service_name}

    @app.get("/readyz")
    def readyz() -> dict:
        return {"status": "ready", "environment": settings.environment}

    # Module routers are registered below as modules land (M0 spike):
    from aiops_api.modules.config.router import router as config_router
    from aiops_api.modules.correlation.router import router as correlation_router
    from aiops_api.modules.event_bridge.router import router as event_bridge_router
    from aiops_api.modules.integrations.router import router as integrations_router
    from aiops_api.modules.feedback.router import router as feedback_router
    from aiops_api.modules.knowledge.router import router as knowledge_router
    from aiops_api.modules.orchestrator.router import router as orchestrator_router
    from aiops_api.modules.policy.router import router as policy_router
    from aiops_api.modules.stats.router import router as stats_router
    from aiops_api.modules.tools.router import router as tools_router

    app.include_router(config_router)
    app.include_router(correlation_router)
    app.include_router(event_bridge_router)
    app.include_router(integrations_router)
    app.include_router(feedback_router)
    app.include_router(knowledge_router)
    app.include_router(orchestrator_router)
    app.include_router(policy_router)
    app.include_router(stats_router)
    app.include_router(tools_router)

    return app


app = create_app()
