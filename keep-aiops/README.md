# keep-aiops

AIOps control plane for the Keep AI-native product evolution (M0 architecture spike).

Architecture contract: `Keep Evolved = Keep Foundation + AI Control Plane (modular monolith) + MCP Tool Mesh`.
Docs: [`docs/aiops/`](../docs/aiops/overview.mdx) — start at [STATUS](../docs/aiops/STATUS.mdx).

## Layout

```text
aiops_api/            # modular monolith (FastAPI)
  main.py             # app entry: health + module routers
  settings.py         # pydantic-settings config
  telemetry.py        # OpenTelemetry bootstrap (investigation_id spans)
  modules/
    event_bridge/     # signed-webhook consumer of Keep domain events
    orchestrator/     # investigation FSM (queued -> gathering -> rca_ready)
    policy/           # suggest-only policy stub (fail-closed on mutate)
mcp_gateway/          # MCP tool gateway stub (separate process, security boundary)
packages/
  keep_client/        # httpx client for Keep REST API (read incident/alerts, write comment/enrich)
deploy/               # compose / kind manifests
tests/
```

## Run (dev)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
uvicorn aiops_api.main:app --port 8080 &
uvicorn mcp_gateway.main:app --port 8090 &
```

## Mode

M0 is **suggest-only**: no mutate tools, policy engine fails closed. See [ADR-0003](../docs/aiops/adr/0003-policy-gated-execution.mdx).
