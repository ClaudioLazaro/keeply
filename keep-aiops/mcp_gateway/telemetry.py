"""Minimal OpenTelemetry bootstrap for the MCP gateway.

Mirrors aiops_api.telemetry but degrades gracefully: if opentelemetry is not
installed the gateway still runs, just without tracing. Every tool invocation
span carries tenant_id and investigation_id for end-to-end correlation.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    trace = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

_initialized = False


def init_telemetry(app=None) -> None:
    """Initialize tracer provider and (optionally) FastAPI instrumentation."""
    global _initialized
    if _initialized or not _OTEL_AVAILABLE:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    from mcp_gateway.settings import get_settings

    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))

    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"))
        )
    if settings.otel_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)

    _initialized = True
    logger.info("telemetry initialized", extra={"service": settings.service_name})


@contextmanager
def tool_span(*, tool: str, tenant_id: str, investigation_id: str):
    """Span per tool invocation with tenant_id / investigation_id attributes."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer = trace.get_tracer("mcp_gateway")
    with tracer.start_as_current_span("mcp.tool.invoke") as span:
        span.set_attribute("tool", tool)
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("investigation_id", investigation_id)
        yield span
