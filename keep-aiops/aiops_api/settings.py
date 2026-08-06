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

    # MCP mesh.
    #
    # "legacy" keeps the hand-rolled HTTP gateway (mcp_gateway/). "mcp" routes
    # through ContextForge over the real protocol. Both are wired so the
    # cutover — and the rollback — is this one variable.
    mcp_transport: str = "legacy"
    mcp_gateway_url: str = "http://localhost:8090"  # legacy transport
    # ContextForge virtual-server endpoint, e.g.
    # http://mcp-gateway:4444/servers/<uuid>/mcp
    mcp_server_url: str = ""
    mcp_bearer_token: str = ""
    # Cluster the Kubernetes specialist inspects, as named in the MCP server's
    # registry. Configuration rather than inference: deriving it from the
    # incident's affected services is the next step, and until then a declared
    # target beats the previous behaviour of answering about whichever cluster
    # the gateway happened to run in.
    mcp_default_cluster: str = "in-cluster"
    # Tools we assert are read-only, as fnmatch patterns over the federated
    # name. This is an allowlist, not a hint we read off the tool: a federated
    # server is not necessarily one we control, and ContextForge drops MCP
    # annotations in transit anyway. Anything unmatched is treated as mutating
    # and denied by the suggest-only policy, so adding a server is a
    # deliberate act.
    mcp_trusted_read_only_tools: list[str] = ["keeply-k8s-*"]

    # Context builder (M2)
    context_timeline_limit: int = 50

    # Cost budget per investigation (M3). Caps every MCP tool call, the
    # wall-clock gathering+hypothesizing phase, and the LLM token usage.
    # A breach moves the investigation to `failed` (coordinator raises
    # BudgetExceeded and the FSM catches it).
    budget_max_tool_calls: int = 50
    budget_max_wall_time_seconds: float = 120.0
    budget_max_llm_tokens: int = 200_000

    # Ceiling on investigations running at once in this process. The budget
    # caps what ONE investigation costs; nothing capped how many start, so an
    # alert storm scheduled one background task per incident and each held a
    # DB connection for its whole run. Keep this below the connection pool
    # (pool_size + max_overflow) or the pool becomes the real limit again.
    max_concurrent_investigations: int = 8

    # An investigation still in gathering/hypothesizing after this long was
    # almost certainly orphaned by a restart — the in-process background task
    # died with the worker. Startup sweeps those to `failed` so they stop
    # reading as "still running" forever.
    orphan_investigation_timeout_seconds: float = 900.0

    # LLM (LiteLLM, ADR-0007). Empty model = disabled: RCA generation uses
    # the deterministic rule-based fallback and everything works without a key.
    # These stay as bootstrap defaults; the persisted agent config
    # (modules/config) overrides them at runtime.
    llm_model: str = ""
    llm_api_key: str = ""
    # Hard ceiling on one completion round-trip. Without it a hung provider
    # connection pins its worker thread and its DB connection forever: the
    # token budget cannot save us because it is charged AFTER the call
    # returns, so it is accounting, not a limiter. Sized under
    # budget_max_wall_time_seconds so the wall budget stays the outer bound.
    llm_timeout_seconds: float = 90.0

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
