"""Configuration for the AIOps control plane (aiops-api)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings; prefix AIOPS_."""

    model_config = SettingsConfigDict(env_prefix="AIOPS_", env_file=".env", extra="ignore")

    service_name: str = "aiops-api"
    environment: str = "dev"

    # Keep upstream
    keep_api_url: str = "http://localhost:8080"
    keep_api_key: str = ""  # Keep API key with read:incident / write:incident scopes

    # Tenant auth (delegated to Keep GET /whoami). False = dev/test only:
    # requests run unauthenticated and read paths are not tenant-filtered.
    auth_enabled: bool = True
    auth_cache_ttl_seconds: int = 60

    # Event bridge
    webhook_secret: str = "dev-webhook-secret"  # HMAC key shared with Keep outbox dispatcher
    auto_investigate_severities: set[str] = {"critical", "high"}

    # MCP mesh
    mcp_gateway_url: str = "http://localhost:8090"

    # Context builder (M2)
    context_timeline_limit: int = 50

    # Cost budget per investigation (M3). Caps every MCP tool call, the
    # wall-clock gathering+hypothesizing phase, and the LLM token usage.
    # A breach moves the investigation to `failed` (coordinator raises
    # BudgetExceeded and the FSM catches it).
    budget_max_tool_calls: int = 50
    budget_max_wall_time_seconds: float = 120.0
    budget_max_llm_tokens: int = 200_000

    # LLM (LiteLLM, ADR-0007). Empty model = disabled: RCA generation uses
    # the deterministic rule-based fallback and everything works without a key.
    # These stay as bootstrap defaults; the persisted agent config
    # (modules/config) overrides them at runtime.
    llm_model: str = ""
    llm_api_key: str = ""

    # Persistence (M0: SQLite; M1: Postgres via same URL setting)
    database_url: str = "sqlite:///./aiops.db"
    # Dev/test convenience: create_all on startup. In prod (Postgres) set false
    # and manage schema via `alembic -c keep-aiops/alembic.ini upgrade head`.
    auto_create_tables: bool = True

    # Knowledge engine (ADR-0005): empty embedding model = keyword retrieval.
    llm_embedding_model: str = ""
    knowledge_seed_dir: str = "examples/runbooks"

    # Observability
    otel_exporter_otlp_endpoint: str = ""  # empty = no export, spans still created
    otel_console_export: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
