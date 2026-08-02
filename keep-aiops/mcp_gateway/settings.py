"""Configuration for the MCP Gateway (separate process, security boundary)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings; prefix MCP_."""

    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    service_name: str = "mcp-gateway"
    environment: str = "dev"

    # Control plane the gateway pulls integration config from. Empty
    # disables the pull entirely and the env settings below govern — the
    # pre-existing behaviour. See mcp_gateway/integrations.py.
    aiops_api_url: str = "http://aiops-api:8080"
    aiops_api_key: str = ""

    # Backends: 'stub' returns canned demo payloads; 'live' needs keep-aiops[live]
    k8s_mode: Literal["stub", "live"] = "stub"

    # Prometheus: 'stub' returns canned firing alerts / error-rate series for the
    # M0 payment-api incident; 'live' queries MCP_PROMETHEUS_URL via HTTP API.
    prometheus_mode: Literal["stub", "live"] = "stub"
    prometheus_url: str = ""

    # M3 read tool servers. All default to stub; live mode requires the
    # matching URL / credential env vars on the operator side.
    datadog_mode: Literal["stub", "live"] = "stub"
    datadog_url: str = ""
    datadog_api_key: str = ""
    datadog_app_key: str = ""

    eks_mode: Literal["stub", "live"] = "stub"
    rds_mode: Literal["stub", "live"] = "stub"

    argocd_mode: Literal["stub", "live"] = "stub"
    argocd_url: str = ""
    argocd_token: str = ""

    jira_mode: Literal["stub", "live"] = "stub"
    jira_url: str = ""
    jira_token: str = ""

    slack_mode: Literal["stub", "live"] = "stub"
    slack_url: str = ""
    slack_token: str = ""

    bitbucket_mode: Literal["stub", "live"] = "stub"
    bitbucket_url: str = ""
    bitbucket_user: str = ""
    bitbucket_token: str = ""

    backstage_mode: Literal["stub", "live"] = "stub"
    backstage_url: str = ""

    # Audit: JSON-lines file (the mcp_gateway.audit logger always emits)
    audit_log_path: str | None = None

    # Observability (optional; empty endpoint = no export)
    otel_exporter_otlp_endpoint: str = ""
    otel_console_export: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
