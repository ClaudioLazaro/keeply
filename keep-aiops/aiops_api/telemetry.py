"""OpenTelemetry bootstrap for aiops-api.

Every investigation run MUST carry `investigation_id` as a span attribute so
traces can be correlated end-to-end (M0 exit criterion).
"""

import logging
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from aiops_api.settings import get_settings

logger = logging.getLogger(__name__)

_initialized = False


def init_telemetry(app=None) -> None:
    """Initialize tracer provider and (optionally) FastAPI instrumentation."""
    global _initialized
    if _initialized:
        return
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


def get_tracer(name: str = "aiops") -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def investigation_span(investigation_id: str, name: str = "investigation", **attributes):
    """Span tagged with investigation_id; mandatory for all investigation work."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        span.set_attribute("investigation_id", investigation_id)
        for key, value in attributes.items():
            span.set_attribute(key, value)
        yield span
