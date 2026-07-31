# M0 Demo Runbook — Keep → event → read-only investigation → writeback

Reproduces the M0 architecture spike end to end:

```text
incident in Keep → outbox → signed webhook → aiops-api (FSM queued→gathering→rca_ready)
  → evidence via MCP Gateway (K8s read tools) → RCA draft comment + aiops.* enrichments in Keep
```

Prereqs: Docker + Docker Compose. No cloud account or LLM key needed (M0 is deterministic and suggest-only).

## 1. Build and start the stack

From the repo root:

```bash
# Keep backend must carry the outbox spike (not in published images). Either:
#   a) full source build (needs network access to the Alpine CDN):
docker build -f docker/Dockerfile.api -t keep-api:m0 .
#   b) or, on restricted networks, overlay local source on the published image (same version):
docker build -f docker/Dockerfile.api.local -t keep-api:m0 .

docker build -t keep-aiops:dev ./keep-aiops

docker compose \
  -f docker-compose.yml \
  -f keep-aiops/deploy/docker-compose.aiops.yml \
  up -d keep-backend keep-websocket-server mcp-gateway aiops-api
```

> `keep-websocket-server` (soketi) is required: Keep's comment endpoint pushes a UI event and returns 500 if Pusher is unreachable.
> If the host `./state` dir is root-owned from previous runs, the override's named `keep-state` volume avoids permission errors (first use may need `docker run --rm -u root -v keep_keep-state:/state --entrypoint /bin/bash keep-api:m0 -c "chown 1000:1000 /state"`).

The aiops override file adds three env vars to `keep-backend` (`KEEP_DOMAIN_EVENTS_ENABLED=true`,
`KEEP_DOMAIN_EVENTS_WEBHOOK_URL=http://aiops-api:8080/v1/events/keep`,
`KEEP_DOMAIN_EVENTS_WEBHOOK_SECRET=dev-webhook-secret`). Without the override file, vanilla Keep runs unchanged.

Health checks:

```bash
curl -s localhost:8080/healthcheck        # keep-backend (vanilla route)
curl -s localhost:8081/healthz            # aiops-api
curl -s localhost:8090/healthz            # mcp-gateway
curl -s localhost:8090/v1/mcp/tools       # tool catalog: get_pods/get_events/get_logs (read)
```

## 2. Create a qualifying incident

With `AUTH_TYPE=NO_AUTH` (default dev compose) every request still needs an API key header (any value works). Create a critical incident:

```bash
curl -s -X POST localhost:8080/incidents \
  -H 'Content-Type: application/json' \
  -H 'X-API-KEY: dev-key' \
  -d '{
    "incident_name": "Payment API elevated 5xx rate",
    "severity": "critical",
    "description": "M0 demo incident",
    "assignee": "sre-oncall@example.com"
  }'
```

Any severity in `AIOPS_AUTO_INVESTIGATE_SEVERITIES` (default `critical,high`) auto-starts an investigation.

Alternatively push an alert that correlates into an incident:

```bash
./scripts/simulate_alerts.sh   # or POST /alerts/event with a provider payload + a grouping rule
```

## 3. Observe the investigation

```bash
# aiops-api received the signed event and ran the FSM
curl -s localhost:8081/v1/investigations | jq

# evidence collected through the MCP gateway (stub K8s backend by default)
INVESTIGATION_ID=$(curl -s localhost:8081/v1/investigations | jq -r '.[0].id')
curl -s localhost:8081/v1/investigations/$INVESTIGATION_ID/evidence | jq
```

Expected: status `rca_ready`; ≥3 evidence items (`get_pods`, `get_events`, `get_logs`) including a
CrashLoopBackOff `payment-api` pod and OOMKilled events (stub backend).

Idempotency check: re-send the same `incident.created` — the same investigation is returned, no duplicate writeback.

## 4. Verify writeback in Keep

```bash
INCIDENT_ID=$(curl -s "localhost:8081/v1/investigations" | jq -r '.[0].incident_id')

# enrichments land on the incident DTO (top-level aiops.* keys)
curl -s "localhost:8080/incidents/$INCIDENT_ID" -H 'X-API-KEY: dev-key' | jq '."aiops.investigation_id", ."aiops.status"'

# the RCA draft is an audit-trail comment (incident comments reuse the alert audit store)
curl -s -X POST localhost:8080/alerts/audit \
  -H 'Content-Type: application/json' -H 'X-API-KEY: dev-key' \
  -d "[\"$INCIDENT_ID\"]" | jq '.[].description'
```

Expected: a comment containing the RCA draft (incident summary + evidence bullets +
"suggest-only — no actions taken") and enrichments `aiops.investigation_id` / `aiops.status=rca_ready`.
In keep-ui (`localhost:3000`) the same appears on the incident timeline.

## 5. Verify failure isolation (M0 exit criterion)

```bash
docker compose -f docker-compose.yml -f keep-aiops/deploy/docker-compose.aiops.yml stop aiops-api
# ingest still works:
curl -s -X POST localhost:8080/incidents -H 'Content-Type: application/json' \
  -H 'X-API-KEY: dev-key' \
  -d '{"incident_name": "ingest still fine", "severity": "warning"}'
# outbox rows accumulate (status=pending); restart aiops-api and they deliver:
docker compose -f docker-compose.yml -f keep-aiops/deploy/docker-compose.aiops.yml start aiops-api
```

## 6. Verify OTel `investigation_id`

`AIOPS_OTEL_CONSOLE_EXPORT=true` is set in the compose file:

```bash
docker compose -f docker-compose.yml -f keep-aiops/deploy/docker-compose.aiops.yml logs aiops-api | grep investigation_id
```

Every investigation span carries the `investigation_id` attribute. Point
`OTEL_EXPORTER_OTLP_ENDPOINT` at a collector for real traces.

## 7. Optional: UI with the investigation panel

The published keep-ui image does not include the `InvestigationPanel` or the
`/api/aiops/*` proxy. Build the UI from local source with the override:

```bash
docker compose \
  -f docker-compose.yml \
  -f keep-aiops/deploy/docker-compose.aiops.yml \
  -f keep-aiops/deploy/docker-compose.ui.yml \
  up -d --build keep-frontend
```

Open http://localhost:3000 (NO_AUTH auto-signs in) → incident detail →
**AI Investigation** panel (evidence, hypotheses, RCA draft, useful/not-useful
feedback). Notes:

- The override uses `dev:webpack` (turbopack fails on `next/font/google`'s
  virtual module) and mounts the host corporate CA (`proxy-root.crt`) so the
  font fetch survives TLS-intercepting proxies.
- First compile of `/incidents` needs >6 GB heap; on small hosts add swap or
  the container is OOM-killed (`docker inspect ... --format '{{.State.OOMKilled}}'`).

## 8. Optional: live K8s evidence with kind

```bash
kind create cluster --name keep-m0
docker compose -f docker-compose.yml -f keep-aiops/deploy/docker-compose.aiops.yml \
  run --rm -e MCP_K8S_MODE=live -v "$HOME/.kube:/root/.kube:ro" mcp-gateway
```

With `MCP_K8S_MODE=live` the gateway uses the in-cluster config, then `~/.kube/config`.
Without a cluster or the `kubernetes` extra, live mode fails closed with `503` + retry hint.

## Teardown

```bash
docker compose -f docker-compose.yml -f keep-aiops/deploy/docker-compose.aiops.yml down -v
kind delete cluster --name keep-m0 2>/dev/null || true
```
