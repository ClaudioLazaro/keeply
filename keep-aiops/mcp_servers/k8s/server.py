"""Kubernetes MCP server — read-only cluster inspection for investigations.

Speaks the Model Context Protocol over streamable HTTP, so ContextForge (or
any MCP client) federates it directly. This replaces the hand-rolled REST
tools in ``mcp_gateway/tools/k8s.py``, which borrowed MCP's vocabulary
without speaking it.

Two contracts this server exists to enforce:

1. **Provenance is structural.** Every tool returns a model whose ``backend``
   and ``cluster`` fields have no default, so MCP puts them in
   ``outputSchema.required``. A caller can always tell real telemetry from a
   demo payload, and from *which* cluster it came.

2. **The target is explicit.** ``cluster`` is a required argument on every
   tool. There is no "current cluster" to fall back to — the failure this
   replaces was precisely an implicit target that silently answered about the
   wrong system. Call ``list_clusters`` to discover valid names.

Failures come back as ``backend="gap"`` with an ``error``, not as a protocol
error: a gap is evidence about the investigation (we looked and could not
see) and the orchestrator records it as such.
"""

from __future__ import annotations

import logging
import os

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from mcp_servers.k8s import clusters, stubs
from mcp_servers.k8s.models import (
    ClusterInfo,
    ClustersResult,
    EventsResult,
    EventSummary,
    LogsResult,
    PodsResult,
    PodSummary,
)

logger = logging.getLogger(__name__)

def _transport_security() -> TransportSecuritySettings:
    """Hosts allowed to reach this server, for DNS-rebinding protection.

    The SDK defends against a browser on someone's machine being tricked into
    driving this server, by rejecting requests whose Host header it does not
    recognise (HTTP 421). That default is right, and it also means the
    hostname a federating gateway uses has to be declared: inside Kubernetes
    that is a service name, in local Docker it is the bridge gateway address.

    ``MCP_K8S_ALLOWED_HOSTS`` is a comma-separated list. Empty disables the
    check, which is only reasonable when something in front already
    terminates and validates the hostname.
    """
    raw = os.environ.get("MCP_K8S_ALLOWED_HOSTS", "").strip()
    if not raw:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts],
    )


mcp = MCPServer(
    name="keeply-kubernetes",
    title="Keeply Kubernetes",
    version="1.0.0",
    instructions=(
        "Read-only Kubernetes inspection for incident investigation. Every tool "
        "requires an explicit `cluster` argument — call list_clusters first. "
        "Every result reports `backend` (live/stub/gap) and the `cluster` that "
        "answered; treat anything other than backend='live' as unverified."
    ),
)


# Every tool on this server only reads. Declaring it in the protocol is what
# lets the policy gate derive execution_class from the catalog itself instead
# of a table it has to be kept in sync with. The gate stays fail-closed: a
# tool that does NOT carry this is treated as mutating and denied, so
# forgetting the annotation makes a tool unavailable rather than unguarded.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def _gap(model, cluster: str, error: str, **fields):
    """Build a gap result: the call failed and the absence is the finding."""
    logger.warning("evidence gap on cluster %s: %s", cluster, error)
    return model(backend="gap", cluster=cluster, error=error, **fields)


def _pod_reason(pod) -> str | None:
    """Waiting reason, else the last terminated reason (OOMKilled etc.)."""
    statuses = pod.status.container_statuses or []
    if not statuses:
        return None
    state = statuses[0].state
    if state and state.waiting and state.waiting.reason:
        return state.waiting.reason
    last = statuses[0].last_state
    if last and last.terminated and last.terminated.reason:
        return last.terminated.reason
    return None


@mcp.tool(
    annotations=READ_ONLY,
    title="List clusters",
    description="Registered clusters this server can inspect, and whether each serves live or stub data.",
)
def list_clusters() -> ClustersResult:
    return ClustersResult(
        clusters=[
            ClusterInfo(name=spec.name, mode=spec.mode, description=spec.description)
            for spec in sorted(clusters.registry().values(), key=lambda s: s.name)
        ]
    )


@mcp.tool(
    annotations=READ_ONLY,
    title="Get pods",
    description=(
        "Pods in a cluster, optionally filtered to one namespace. Prefer passing "
        "the namespace of the affected service: an unfiltered query returns every "
        "workload on the cluster, including ones unrelated to the incident."
    ),
)
def get_pods(cluster: str, namespace: str | None = None) -> PodsResult:
    scope = namespace or "all"
    try:
        spec = clusters.get(cluster)
    except clusters.UnknownCluster as exc:
        return _gap(PodsResult, cluster, str(exc), namespace=scope)

    if spec.mode == "stub":
        pods = [p for p in stubs.STUB_PODS if not namespace or p.namespace == namespace]
        return PodsResult(backend="stub", cluster=cluster, namespace=scope, pods=pods)

    try:
        api = clusters.core_v1(spec)
        items = (
            api.list_namespaced_pod(namespace).items
            if namespace
            else api.list_pod_for_all_namespaces().items
        )
    except clusters.ClusterUnavailable as exc:
        return _gap(PodsResult, cluster, str(exc), namespace=scope)
    except Exception as exc:  # noqa: BLE001 — any backend failure is a gap, never a silent empty list
        return _gap(PodsResult, cluster, f"{type(exc).__name__}: {exc}", namespace=scope)

    return PodsResult(
        backend="live",
        cluster=cluster,
        namespace=scope,
        pods=[
            PodSummary(
                name=p.metadata.name,
                namespace=p.metadata.namespace,
                phase=p.status.phase,
                ready=bool((p.status.container_statuses or [None])[0] and p.status.container_statuses[0].ready),
                restarts=sum(cs.restart_count for cs in (p.status.container_statuses or [])),
                container=(p.spec.containers[0].name if p.spec.containers else None),
                node=p.spec.node_name,
                reason=_pod_reason(p),
            )
            for p in items
        ],
    )


@mcp.tool(
    annotations=READ_ONLY,
    title="Get events",
    description="Recent Kubernetes events in a cluster, optionally filtered to one namespace.",
)
def get_events(cluster: str, namespace: str | None = None) -> EventsResult:
    scope = namespace or "all"
    try:
        spec = clusters.get(cluster)
    except clusters.UnknownCluster as exc:
        return _gap(EventsResult, cluster, str(exc), namespace=scope)

    if spec.mode == "stub":
        events = [e for e in stubs.STUB_EVENTS if not namespace or e.namespace == namespace]
        return EventsResult(backend="stub", cluster=cluster, namespace=scope, events=events)

    try:
        api = clusters.core_v1(spec)
        items = (
            api.list_namespaced_event(namespace).items
            if namespace
            else api.list_event_for_all_namespaces().items
        )
    except clusters.ClusterUnavailable as exc:
        return _gap(EventsResult, cluster, str(exc), namespace=scope)
    except Exception as exc:  # noqa: BLE001
        return _gap(EventsResult, cluster, f"{type(exc).__name__}: {exc}", namespace=scope)

    return EventsResult(
        backend="live",
        cluster=cluster,
        namespace=scope,
        events=[
            EventSummary(
                type=e.type,
                reason=e.reason,
                namespace=e.metadata.namespace,
                object=f"{e.involved_object.kind}/{e.involved_object.name}".lower()
                if e.involved_object
                else None,
                message=e.message,
                count=e.count or 0,
                last_timestamp=str(e.last_timestamp) if e.last_timestamp else None,
            )
            for e in items
        ],
    )


@mcp.tool(
    annotations=READ_ONLY,
    title="Get pod logs",
    description="Tail the logs of one pod. Namespace is required — the interesting pod is rarely in 'default'.",
)
def get_logs(cluster: str, pod: str, namespace: str, tail_lines: int = 100) -> LogsResult:
    try:
        spec = clusters.get(cluster)
    except clusters.UnknownCluster as exc:
        return _gap(LogsResult, cluster, str(exc), pod=pod, namespace=namespace)

    if spec.mode == "stub":
        return LogsResult(
            backend="stub", cluster=cluster, pod=pod, namespace=namespace, lines=stubs.STUB_LOG_LINES
        )

    try:
        api = clusters.core_v1(spec)
        text = api.read_namespaced_pod_log(name=pod, namespace=namespace, tail_lines=tail_lines)
    except clusters.ClusterUnavailable as exc:
        return _gap(LogsResult, cluster, str(exc), pod=pod, namespace=namespace)
    except Exception as exc:  # noqa: BLE001
        # Includes 404 for a pod that does not exist. Reported as a gap with
        # the reason rather than as a broken server: the backend answered,
        # the resource simply is not there.
        return _gap(LogsResult, cluster, f"{type(exc).__name__}: {exc}", pod=pod, namespace=namespace)

    return LogsResult(
        backend="live",
        cluster=cluster,
        pod=pod,
        namespace=namespace,
        lines=(text or "").splitlines(),
    )


app = mcp.streamable_http_app(transport_security=_transport_security())

if os.environ.get("MCP_K8S_LOG_REQUESTS", "").lower() == "true":
    # Diagnostic only: federating gateways negotiate the handshake
    # differently, and a rejected one surfaces as a bare 400 with nothing
    # to debug from. Logs the method, path and headers of every request.
    _inner = app

    async def app(scope, receive, send):  # type: ignore[misc]
        if scope["type"] == "http":
            headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
            logger.warning(
                "request %s %s headers=%s", scope.get("method"), scope.get("path"), headers
            )
        await _inner(scope, receive, send)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("MCP_K8S_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_K8S_PORT", "8765")),
    )


if __name__ == "__main__":
    main()
