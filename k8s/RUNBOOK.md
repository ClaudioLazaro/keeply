# M3 K8s Deploy Runbook — keeply.clazar.net (K3D cluster)

End-to-end on a real K8s cluster, accessed through the existing
Cloudflare tunnel (`*.clazar.net` → Traefik). Keeps the M3 AC intact: the
MCP gateway reads the **live** K8s API for evidence and the AI plane runs
inside the cluster (coordinator + specialists + cost budget).

## 0. Prerequisites

- `k3d` v5.8+, `kubectl` 1.30+, `helm` 4+
- Docker daemon with the `keep-api:m0`, `keep-aiops:dev` and `keep-ui:keeply`
  images built:
  ```bash
  docker build -f docker/Dockerfile.api.local -t keep-api:m0 .      # M0 outbox spike
  docker build -t keep-aiops:dev ./keep-aiops                        # M3 specialists + budget
  docker build -f keep-ui/Dockerfile.keeply -t keep-ui:keeply ./keep-ui  # UI + investigation panel
  ```
  The `keep-aiops` image must install `[live]` so the gateway can use the
  in-cluster `kubernetes` client. The Dockerfile at `keep-aiops/Dockerfile`
  already does `pip install '.[live]'`.

## 1. Import the images into the cluster

```bash
k3d image import keep-api:m0     -c cuda-cluster
k3d image import keep-aiops:dev  -c cuda-cluster
k3d image import keep-ui:keeply  -c cuda-cluster
```

`k3d image import` only reaches the nodes that exist at import time. If a
pod later schedules onto another node you get `ErrImagePull` on a purely
local tag — re-run the import.

## 2. Apply the manifests

```bash
kubectl apply -f k8s/00-rbac-and-middleware.yaml   # SA + ClusterRole + Middleware
kubectl apply -f k8s/01-config.yaml                # ConfigMap + Secret + PVC
kubectl apply -f k8s/02-data-and-soketi.yaml       # postgres + soketi + keep-backend
kubectl apply -f k8s/03-aiops-and-mcp.yaml         # aiops-migrate + aiops-api + mcp-gateway
kubectl apply -f k8s/05-frontend.yaml              # keep-ui:keeply (investigation panel)
kubectl apply -f k8s/06-ingressroutes.yaml         # routing (see step 3)
kubectl apply -f k8s/demo-payments.yaml            # optional: a payment-api OOMKilled demo pod
```

## 3. Routing — the frontend owns the host

All routing lives in `k8s/06-ingressroutes.yaml`. The rule that matters:

> **The Next.js frontend owns the entire path space of `keeply.clazar.net`.**
> Every other service gets a non-colliding prefix.

```
/keepapi/*   -> keep-backend    (stripPrefix + X-API-KEY injected)
/aiopsapi/*  -> aiops-api       (stripPrefix)
/mcp/*       -> mcp-gateway     (stripPrefix)
/ws/*        -> keep-websocket  (stripPrefix; soketi mounts /app at root)
everything else -> keep-frontend   (catch-all, priority 1)
```

The backend prefix is `/aiopsapi`, **not** `/aiops` — `/aiops` is the
console UI section (`keep-ui/app/(keep)/aiops`). Same rule as `/keepapi`:
an API prefix must never shadow a UI route.

The browser never calls keep-backend directly. `API_URL` is server-side
only (`keep-ui/utils/apiUrl.ts` — literally `// server only!`); client
fetches go to the relative default `/backend/*`
(`keep-ui/shared/lib/getApiUrlFromConfig.ts`), and `keep-ui/middleware.ts`
rewrites those in-process to `API_URL`. Same for the AIOps panel:
`/api/aiops/*` is a Next route handler that proxies to `AIOPS_API_URL`.

**Never give keep-backend a rule like `PathPrefix('/incidents')`,
`PathPrefix('/alerts')` or `PathPrefix('/api')`.** Those paths are UI
routes. An earlier version of this runbook did exactly that and the
frontend broke in three ways at once: `/` redirects to `/incidents`,
which was served as raw API JSON; `/backend/*` had no route and 404'd,
killing every client-side fetch; and 10 of the 15 UI sections
(`/settings`, `/dashboard`, `/topology`, …) 404'd because they weren't in
the hand-maintained allowlist.

The `stripPrefix` middlewares are mandatory: Traefik's `PathType: Prefix`
**does not** strip, and the FastAPI apps mount at root, so they 404 on the
unexpected prefix.

## 4. DNS / Cloudflare tunnel

**Nothing to patch.** The `cloudflared-config` ConfigMap already has
`*.clazar.net → http://traefik.kube-system.svc.cluster.local:80`, and that
wildcard covers `keeply.clazar.net` (and any other subdomínio). Cloudflare's
DNS already points `keeply.clazar.net` to the same Cloudflare IPs as
`cafecafe.clazar.net` (104.21.92.157 / 172.67.195.106), so the tunnel
already accepts the request and forwards it to Traefik. Traefik then
matches the `Host` header to the keeply `IngressRoute` and routes the path.

The decision lives entirely in the cluster (Traefik `IngressRoute` +
middleware), not in the cloudflared config.

## 5. Run the migrations

The `aiops-migrate` Job is in the manifest but K8s can race it against the
api pod starting. The bullet-proof path:

```bash
kubectl -n keeply delete job aiops-migrate 2>/dev/null || true
kubectl -n keeply create -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata: { name: aiops-migrate, namespace: keeply }
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: keep-aiops:dev
          imagePullPolicy: Never
          command: ["alembic", "-c", "alembic.ini", "upgrade", "head"]
          envFrom:
            - configMapRef: { name: aiops-env }
            - secretRef: { name: keeply-secrets }
          env:
            - { name: AIOPS_DATABASE_URL,
                value: "postgresql+psycopg://aiops:aiops@postgres:5432/aiops" }
EOF
kubectl -n keeply wait --for=condition=complete --timeout=60s job/aiops-migrate
```

## 6. End-to-end smoke

```bash
# healthchecks
curl -sS https://keeply.clazar.net/keepapi/healthcheck
curl -sS https://keeply.clazar.net/aiopsapi/healthz
curl -sS https://keeply.clazar.net/mcp/healthz
curl -sS https://keeply.clazar.net/mcp/v1/mcp/tools | jq 'length'      # 19

# UI: every section must be 200 text/html (not application/json, not 404)
for p in / /incidents /alerts /settings /dashboard /topology /workflows \
         /providers /deduplication /rules /mapping /extraction \
         /maintenance /ai /notifications-hub \
         /aiops /aiops/investigations /aiops/tools /aiops/policies; do
  printf '%-20s %s\n' "$p" \
    "$(curl -sL -o /dev/null -w '%{http_code} %{content_type}' "https://keeply.clazar.net$p")"
done

# websocket (soketi) must answer 101 Switching Protocols.
# --http1.1 is required: you cannot upgrade over HTTP/2, and Cloudflare
# will hand you a bogus 500 if you try.
curl -si --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  'https://keeply.clazar.net/ws/app/keepappkey?protocol=7&client=js&version=8.0.0' | head -1
```

Create a critical incident and watch the investigation (live K8s evidence):

```bash
INC=$(curl -sS -X POST https://keeply.clazar.net/keepapi/incidents \
  -H 'Content-Type: application/json' -H 'X-API-KEY: dev-key' \
  -d '{"incident_name":"M3 K8s e2e","severity":"critical","assignee":"sre@example.com"}' \
  | jq -r .id)
curl -sS -H 'X-API-KEY: dev-key' \
  "https://keeply.clazar.net/aiopsapi/v1/investigations?incident_id=$INC" | jq
curl -sS -H 'X-API-KEY: dev-key' \
  "https://keeply.clazar.net/aiopsapi/v1/investigations/$(curl -sS -H 'X-API-KEY: dev-key' \
    \"https://keeply.clazar.net/aiopsapi/v1/investigations?incident_id=$INC\" | jq -r '.[0].id')/evidence" \
  | jq 'group_by(.tool) | map({tool: .[0].tool, backend: .[0].payload.result.backend, summary: .[0].summary})'
```

Expected: `rca_ready` within ~5s, evidence with `backend=live` for
`get_pods`/`get_events` (the others stay on stub because their backends
are not in this cluster).

## 7. Cost budget smoke (M3 AC)

```bash
kubectl -n keeply patch configmap aiops-env --type=merge \
  -p '{"data":{"AIOPS_BUDGET_MAX_TOOL_CALLS":"1","AIOPS_BUDGET_MAX_WALL_TIME_SECONDS":"10"}}'
kubectl -n keeply scale deploy/aiops-api --replicas=0
sleep 2
kubectl -n keeply scale deploy/aiops-api --replicas=1
# wait for ready
kubectl -n keeply wait --for=condition=ready --timeout=60s pod -l app=aiops-api

INC=$(curl -sS -X POST https://keeply.clazar.net/keepapi/incidents \
  -H 'Content-Type: application/json' -H 'X-API-KEY: dev-key' \
  -d '{"incident_name":"budget breach","severity":"critical","assignee":"sre@example.com"}' \
  | jq -r .id)
curl -sS -H 'X-API-KEY: dev-key' \
  "https://keeply.clazar.net/aiopsapi/v1/investigations?incident_id=$INC" \
  | jq '.[0] | {status, error}'
curl -sS https://keeply.clazar.net/aiopsapi/metrics | grep '^keep_aiops_investigation_cost_exceeded_total'
# restore
kubectl -n keeply patch configmap aiops-env --type=merge \
  -p '{"data":{"AIOPS_BUDGET_MAX_TOOL_CALLS":"50","AIOPS_BUDGET_MAX_WALL_TIME_SECONDS":"120"}}'
kubectl -n keeply scale deploy/aiops-api --replicas=0
sleep 2
kubectl -n keeply scale deploy/aiops-api --replicas=1
```

## 8. Notes & gotchas

- **cloudflared sends plain HTTP** to Traefik → use Traefik `entryPoints: [web]`
  (not `websecure`), or the route never matches.
- **Traefik does not strip `PathType: Prefix` prefixes**. Use
  `traefik.containo.us/v1alpha1` `IngressRoute` with `stripPrefix` middleware
  for `/mcp`, `/aiops`, `/keepapi` and `/ws`.
- **Never path-split the host between the UI and the API.** See §3. The
  frontend is the catch-all; everything else takes a dedicated prefix.
  There is no stock `Ingress` any more — `k8s/04-ingress.yaml` was deleted
  because its `/api` and `/healthcheck` rules collided with the UI.
- **Never patch `middleware.ts` to force `isAuthenticated = true` in
  NO_AUTH.** `/signin` is where the NoAuth provider mints the session and
  its accessToken; skipping it leaves the app sessionless, `/backend/*`
  401s, and `ApiClient` calls next-auth `signOut()`, whose client falls
  back to a hardcoded `http://localhost:3000/api/auth` — the user gets
  bounced to localhost. See the note in `keep-ui/build-patches.sh`.
- **Escape `&` in every `sed` replacement** used by the image build. An
  unescaped `&` means "the whole match", so a literal `&&` silently
  duplicates the matched text and corrupts the file.
- **`npm install` must pass `--include=dev`** in the image build:
  `NODE_ENV=production` makes npm omit devDependencies, and the build
  toolchain (tailwindcss, tailwind-variants, postcss, postcss-import)
  lives there.
- **`PUSHER_HOST` must be relative (`/ws`)**, not the in-cluster Service
  name. The value is shipped to the browser via the runtime config, and
  `keep-ui/utils/hooks/usePusher.ts:27` only derives `wsHost`/`wsPort` from
  `window.location` when the value starts with `/`. An absolute
  `keep-websocket` is not resolvable from a browser.
- **`backoffLimit: 0` on `aiops-migrate`** if the schema tables don't exist
  yet when the job first runs (otherwise the Job keeps retrying the
  `relation "policy" does not exist` error during the seed step). After
  the first successful run, set it back to 2 for prod.
- **`security-headers` middleware has `sslRedirect: false`** (the default for
  cafecafe is `true`, but cloudflared terminates SSL — the redirect loop
  would 404 the public endpoint).
- **MCP_K8S_MODE=live requires the `keep-mcp` ServiceAccount** with the
  `keep-mcp-reader` ClusterRole (read-only on pods/pods/log/events/namespaces).
  Without it, the gateway 503s with "forbidden".
- **DNS is not in your control here.** `*.clazar.net` is already in the
  tunnel; the keeply host is just a subdomínio. Don't touch
  `cloudflared-config` unless you're sure you need to.
