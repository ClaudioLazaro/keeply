"""Configuration for the MCP Gateway (separate process, security boundary)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings; prefix MCP_."""

    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    service_name: str = "mcp-gateway"
    environment: str = "dev"

    # Backends: 'stub' returns canned demo payloads; 'live' needs keep-aiops[live]
    k8s_mode: Literal["stub", "live"] = "stub"

    # Prometheus: 'stub' returns canned firing alerts / error-rate series for the
    # M0 payment-api incident; 'live' queries MCP_PROMETHEUS_URL via HTTP API.
    prometheus_mode: Literal["stub", "live"] = "stub"
    prometheus_url: str = ""

    # Audit: JSON-lines file (the mcp_gateway.audit logger always emits)
    audit_log_path: str | None = None

    # Observability (optional; empty endpoint = no export)
    otel_exporter_otlp_endpoint: str = ""
    otel_console_export: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
