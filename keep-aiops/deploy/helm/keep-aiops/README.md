# keep-aiops Helm chart

Deploys the Keep **AIOps control plane** — `aiops-api` (orchestrator / webhook
entry, port 8080) and `mcp-gateway` (agent tool boundary, port 8090) — into a
dedicated namespace (convention: `keep-aiops`), **beside Keep**. Keep itself
(keep-api, keep-ui, soketi, …) deploys separately, typically via its own chart
into the `keep` namespace; this chart only consumes its URL and credentials.

Topology and isolation rules follow
`docs/aiops/architecture/deployment.mdx`: orchestrator → keep-api is the only
north-south call, agents reach tools only through mcp-gateway, and capability
modes roll out progressively (`suggest` → `assisted` → `auto`). The M1 posture
is suggest-only: the gateway policy gate is fail-closed and only
`execution_class=read` tools are invocable.

## Prerequisites

- Kubernetes 1.27+ (PDB `policy/v1`, HPA `autoscaling/v2`)
- Helm 3.14+
- Container image `keep-aiops` (both processes share one image; the command
  selects `aiops_api.main:app` vs `mcp_gateway.main:app`)

The bitnami/postgresql subchart (16.7.27) is **vendored** in `charts/`, so
`helm lint` / `helm template` / installs work without fetching anything. To
refresh it: `helm pull oci://registry-1.docker.io/bitnamicharts/postgresql
--version <v> -d charts/` and bump the version in `Chart.yaml`.

## Install (dev defaults: bundled PostgreSQL, stub K8s backend)

```sh
kubectl create namespace keep-aiops
helm install keep-aiops ./keep-aiops/deploy/helm/keep-aiops -n keep-aiops
```

## Install (prod shape: external Postgres, secrets, NetworkPolicies, HPA)

```sh
helm upgrade --install keep-aiops ./keep-aiops/deploy/helm/keep-aiops \
  -n keep-aiops \
  --set environment=prod \
  --set image.tag=0.1.0 \
  --set postgresql.enabled=false \
  --set database.existingSecret=aiops-database \
  --set aiopsApi.keepApiUrl=https://keep-api.keep.svc.cluster.local:8080 \
  --set aiopsApi.keepApiKey.existingSecret=keep-aiops-secrets \
  --set aiopsApi.webhookSecret.existingSecret=keep-aiops-secrets \
  --set aiopsApi.otel.exporterOtlpEndpoint=http://otel-collector.keep-aiops:4317 \
  --set aiopsApi.autoscaling.enabled=true \
  --set networkPolicy.enabled=true
```

Expected secrets:

| Secret | Key | Consumed as |
| --- | --- | --- |
| `aiops-database` | `database-url` | `AIOPS_DATABASE_URL` (full SQLAlchemy URL) |
| `keep-aiops-secrets` | `keep-api-key` | `AIOPS_KEEP_API_KEY` |
| `keep-aiops-secrets` | `webhook-secret` | `AIOPS_WEBHOOK_SECRET` (must match Keep's `KEEP_DOMAIN_EVENTS_WEBHOOK_SECRET`) |

## Upgrade

```sh
helm upgrade keep-aiops ./keep-aiops/deploy/helm/keep-aiops -n keep-aiops
```

Upgrades re-run the migration hook (see below) before rolling pods.

## Schema migrations (aiops-migrate Job)

The chart runs `alembic -c /app/alembic.ini upgrade head` as a **Helm
pre-install/pre-upgrade hook Job** — the boring, fail-closed option: a failed
migration aborts the release before any new pod rolls, and
`hook-delete-policy: before-hook-creation,hook-succeeded` reaps old hook Jobs
so repeated upgrades stay clean. `AIOPS_AUTO_CREATE_TABLES=false` everywhere;
alembic owns the schema. The image ships the alembic scaffold at
`/app/alembic.ini` + `/app/alembic/` (see `keep-aiops/Dockerfile`). Disable
with `--set migrate.enabled=false` only for sqlite-style local tinkering.

## Values

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` / `image.tag` / `image.pullPolicy` | `keep-aiops` / `""` (appVersion) / `IfNotPresent` | Shared image for all pods |
| `environment` | `dev` | `AIOPS_ENVIRONMENT` / `MCP_ENVIRONMENT` |
| `aiopsApi.replicaCount` | `1` | Replicas when HPA is off |
| `aiopsApi.keepApiUrl` | `http://keep-backend.keep.svc…:8080` | Keep API base URL (`AIOPS_KEEP_API_URL`) |
| `aiopsApi.keepApiKey.value` / `.existingSecret` / `.existingSecretKey` | `dev-key` / `""` / `keep-api-key` | `AIOPS_KEEP_API_KEY` source (existingSecret wins) |
| `aiopsApi.webhookSecret.value` / `.existingSecret` / `.existingSecretKey` | `dev-webhook-secret` / `""` / `webhook-secret` | `AIOPS_WEBHOOK_SECRET` source |
| `aiopsApi.otel.exporterOtlpEndpoint` / `.consoleExport` | `""` / `false` | OTEL export (`AIOPS_OTEL_*`) |
| `aiopsApi.resources` | 50m/128Mi → 500m/512Mi | Small requests/limits |
| `aiopsApi.autoscaling.enabled` / `.minReplicas` / `.maxReplicas` / `.targetCPUUtilizationPercentage` | `false` / `1` / `4` / `80` | HPA for aiops-api |
| `aiopsApi.pdb.enabled` / `.minAvailable` | `true` / `1` | PodDisruptionBudget |
| `mcpGateway.replicaCount` | `1` | Gateway replicas |
| `mcpGateway.k8sMode` | `stub` | `MCP_K8S_MODE`: `stub` (canned payloads) or `live` (in-cluster API; needs RBAC + image built with `keep-aiops[live]`) |
| `mcpGateway.auditLogPath` | `""` | Optional JSONL audit file (`MCP_AUDIT_LOG_PATH`) |
| `mcpGateway.otel.*` | same shape as api | `MCP_OTEL_*` |
| `mcpGateway.resources` | 25m/64Mi → 250m/256Mi | Small requests/limits |
| `mcpGateway.pdb.*` | `true` / `1` | PodDisruptionBudget |
| `mcpGateway.serviceAccount.create` / `.name` / `.annotations` | `true` / `""` / `{}` | Dedicated SA for the gateway |
| `mcpGateway.rbac.create` / `.rules` | `false` / pods get,list · pods/log get · events get,list,watch | Namespaced Role for live K8s reads; use a ClusterRole for cross-namespace reads |
| `database.url` | `""` | Full `AIOPS_DATABASE_URL` override (external/managed Postgres) |
| `database.existingSecret` / `.existingSecretKey` | `""` / `database-url` | Secret holding the URL (takes precedence; prod path) |
| `database.autoCreateTables` | `false` | `AIOPS_AUTO_CREATE_TABLES`; keep false — alembic owns schema |
| `postgresql.enabled` | `true` | Bundled bitnami/postgresql (dev default; set false in prod) |
| `postgresql.auth.postgresPassword` / `.database` | `aiops-dev-password` / `aiops` | Dev credentials for the bundled DB — change for shared use |
| `migrate.enabled` / `.backoffLimit` | `true` / `3` | alembic upgrade-head hook Job |
| `networkPolicy.enabled` | `false` | Enable once the CNI enforces NetworkPolicy |
| `networkPolicy.allowFromNamespaces` | `[keep]` | Namespaces allowed to reach aiops-api (keep-backend, keep-ui) |
| `networkPolicy.dnsNamespace` | `kube-system` | Where kube-dns/coredns runs (egress DNS rule) |

## NetworkPolicies

With `networkPolicy.enabled=true`:

- **aiops-api** — ingress TCP/8080 only from `allowFromNamespaces` (default
  `keep`); egress to DNS, keep-api (TCP/8080 in those namespaces), mcp-gateway
  (TCP/8090, same namespace), Postgres (TCP/5432), OTLP (TCP/4317,4318).
- **mcp-gateway** — ingress TCP/8090 only from aiops-api pods; egress to DNS
  and the Kubernetes API (TCP/443, 6443) for `k8sMode=live`.

Pods not selected by a policy (e.g. the migrate Job) stay unrestricted; if
your cluster applies a default-deny baseline, allow the migrate pods egress to
Postgres/DNS accordingly.

## Probes

- aiops-api: liveness `/healthz`, readiness `/readyz` on port 8080.
- mcp-gateway: liveness + readiness `/healthz` on port 8090.
