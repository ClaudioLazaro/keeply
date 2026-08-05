# MCP servers

Real Model Context Protocol servers, federated by [IBM ContextForge](https://github.com/IBM/mcp-context-forge).

This replaces `mcp_gateway/`, which borrowed MCP's vocabulary — a "tool catalog",
a "tool invoke" — over a plain REST API that no MCP client could speak to. These
servers speak the protocol, so ContextForge federates them directly and any
third-party MCP server can sit beside them without an adapter.

## What each layer owns

| Layer | Owns |
|---|---|
| **MCP servers** (here) | Talking to a backend, and reporting honestly what it returned |
| **ContextForge** | Federation, the tool registry, transport, auth, rate limiting, retries |
| **aiops-api coordinator** | Policy (fail-closed), the per-investigation budget, evidence classification |

Policy and budget deliberately did **not** move into ContextForge. They are
investigation semantics, not gateway features.

## The provenance contract

Every tool returns a model whose `backend` and `cluster` fields have **no
default**, so MCP puts them in `outputSchema.required` and the protocol will
not let a result omit them.

- `backend`: `live` (a real backend answered), `stub` (canned demo payload),
  `gap` (the call failed — the absence is itself the finding)
- `cluster`: which target answered

Verified end to end through ContextForge: the federated tool record keeps
`outputSchema.required = ["backend", "cluster", "namespace"]`, and all three
provenance states arrive intact at an MCP client on the far side.

A third-party MCP server will not report `backend`. The coordinator classifies
those as `unknown` — never as `live`.

## Kubernetes server

`cluster` is a **required argument** on every tool. There is no current-cluster
default, on purpose: the previous implementation resolved its target from
`load_incluster_config()`, so it answered about whichever cluster the gateway
pod ran in and never recorded which one that was. On a shared cluster it
returned every namespace it could see, the coordinator picked the most
troubled-looking pod, and evidence about an unrelated project was filed
against your incident and stamped `live`.

Call `list_clusters` to discover valid names.

```bash
export MCP_K8S_CLUSTERS='[
  {"name":"prod-eu","context":"arn:aws:eks:eu-west-1:...","mode":"live"},
  {"name":"in-cluster","in_cluster":true,"mode":"live"},
  {"name":"demo","mode":"stub"}
]'
python -m mcp_servers.k8s.server
```

Unset falls back to a single `in-cluster` entry honouring `MCP_K8S_MODE`, so
existing deployments keep working — but now every result says which cluster
answered.

## Registering with ContextForge

Five things cost real debugging time. All of them fail with an unhelpful
message, so they are written down here.

**1. Private networks are blocked by default.** ContextForge treats peer
registration as an SSRF risk and rejects any URL resolving to a private
address — which is every in-cluster Service. Allow the specific CIDR, not
`SSRF_ALLOW_PRIVATE_NETWORKS=true`, so the protection still means something:

```bash
-e SSRF_ALLOWED_NETWORKS='["10.42.0.0/16"]'   # the pod CIDR
```

Symptom without it: `422`, and in the log
`Request validation error ... [loc=('body','url') type=value_error]`.

**2. The MCP SDK rejects unknown Host headers.** DNS-rebinding protection
answers `421 Misdirected Request` for any hostname it was not told about, and
the hostname ContextForge dials is not the one you tested with locally:

```bash
export MCP_K8S_ALLOWED_HOSTS="keeply-k8s-mcp:8765,localhost:8765"
```

Symptom without it: registration fails with `Failed to initialize gateway ...
421 Misdirected Request`.

**3. The API is versioned; the README's examples are not.** Use
`POST /v1/gateways` and `POST /v1/servers`. The unversioned paths answer `GET`
but not `POST`.

**4. Virtual server tool association is snake_case.** `associated_tools`
works; `associatedTools` is **silently ignored** and you get a server with an
empty tool list and no error. The response field is camelCase, which makes
this easy to get backwards.

**5. Errors are generic by default.** Every failure above returns
`{"detail":"An error occurred, please try again."}`. Run ContextForge with
`LOG_LEVEL=DEBUG` before debugging anything.

Working sequence:

```bash
TOKEN=$(docker exec cf-gateway python3 -m mcpgateway.utils.create_jwt_token \
  --username admin@keeply.local --exp 10080 --secret "$JWT_SECRET_KEY")

curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"keeply_k8s","url":"http://keeply-k8s-mcp:8765/mcp","transport":"STREAMABLEHTTP"}' \
  http://localhost:4444/v1/gateways

IDS=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:4444/v1/tools \
      | jq -c '[.[].id]')
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"server\":{\"name\":\"keeply_investigation\",\"associated_tools\":$IDS}}" \
  http://localhost:4444/v1/servers
```

Clients then connect to `http://<gateway>/servers/{SERVER_UUID}/mcp` with the
same bearer token.

## Diagnostics

`MCP_K8S_LOG_REQUESTS=true` logs the method, path and headers of every request.
Handshake failures otherwise surface as a bare `400` with nothing to work from.
