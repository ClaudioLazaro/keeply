"""Kubernetes MCP tool server (M0).

``MCP_K8S_MODE=stub`` (default) returns realistic canned payloads for the M0
demo: a CrashLoopBackOff ``payment-api`` pod, matching warning events, and log
lines with a plausible OOM stack trace.

``MCP_K8S_MODE=live`` lazily uses the optional ``kubernetes`` client (install
with ``pip install keep-aiops[live]``), loading in-cluster config first and
falling back to kubeconfig. Live backend failures surface as
:class:`KubernetesBackendUnavailable`, which the gateway maps to 503 with a
retry hint.
"""

from __future__ import annotations

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable, ResourceNotFound


class KubernetesBackendUnavailable(BackendUnavailable):
    """Raised when the live Kubernetes backend cannot serve a request."""


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

GET_PODS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "namespace": {
            "type": "string",
            "description": "Namespace filter; all namespaces when omitted",
        },
    },
    "required": [],
    "additionalProperties": False,
}

GET_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "namespace": {
            "type": "string",
            "description": "Namespace filter; all namespaces when omitted",
        },
    },
    "required": [],
    "additionalProperties": False,
}

GET_LOGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pod": {"type": "string", "description": "Pod name"},
        "namespace": {"type": "string", "default": "default"},
        "tail_lines": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 100},
    },
    "required": ["pod"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Stub payloads (M0 demo scenario: payment-api CrashLoopBackOff / OOMKilled)
# ---------------------------------------------------------------------------

_STUB_PODS: list[dict[str, Any]] = [
    {
        "name": "payment-api-7d9f4b6c5-x2vkm",
        "namespace": "payments",
        "phase": "Running",
        "ready": False,
        "restarts": 14,
        "container": "payment-api",
        "node": "kind-worker2",
        "age": "47m",
        "state": {
            "waiting": {
                "reason": "CrashLoopBackOff",
                "message": "back-off 5m0s restarting failed container=payment-api pod=payment-api-7d9f4b6c5-x2vkm",
            }
        },
        "last_terminated": {"reason": "OOMKilled", "exit_code": 137},
    },
    {
        "name": "api-gateway-6b8c7d9f4-q1zrt",
        "namespace": "payments",
        "phase": "Running",
        "ready": True,
        "restarts": 0,
        "container": "api-gateway",
        "node": "kind-worker",
        "age": "3d",
        "state": {"running": {"started_at": "2026-07-26T08:02:11Z"}},
        "last_terminated": None,
    },
    {
        "name": "settlement-worker-5c6f7d8b9-m4pln",
        "namespace": "payments",
        "phase": "Running",
        "ready": True,
        "restarts": 1,
        "container": "settlement-worker",
        "node": "kind-worker2",
        "age": "2d",
        "state": {"running": {"started_at": "2026-07-27T19:44:03Z"}},
        "last_terminated": None,
    },
]

_STUB_EVENTS: list[dict[str, Any]] = [
    {
        "type": "Warning",
        "reason": "BackOff",
        "namespace": "payments",
        "object": "pod/payment-api-7d9f4b6c5-x2vkm",
        "message": "Back-off restarting failed container payment-api",
        "count": 12,
        "first_timestamp": "2026-07-29T09:27:41Z",
        "last_timestamp": "2026-07-29T10:14:52Z",
    },
    {
        "type": "Warning",
        "reason": "Unhealthy",
        "namespace": "payments",
        "object": "pod/payment-api-7d9f4b6c5-x2vkm",
        "message": "Liveness probe failed: Get http://10.244.1.37:8080/healthz: context deadline exceeded",
        "count": 6,
        "first_timestamp": "2026-07-29T09:26:10Z",
        "last_timestamp": "2026-07-29T10:13:55Z",
    },
    {
        "type": "Warning",
        "reason": "OOMKilling",
        "namespace": "payments",
        "object": "node/kind-worker2",
        "message": "Memory cgroup out of memory: Killed process 18234 (payment-api) total-vm:1845260kB, anon-rss:1048576kB",
        "count": 3,
        "first_timestamp": "2026-07-29T09:25:58Z",
        "last_timestamp": "2026-07-29T10:14:47Z",
    },
    {
        "type": "Normal",
        "reason": "Scheduled",
        "namespace": "payments",
        "object": "pod/payment-api-7d9f4b6c5-x2vkm",
        "message": "Successfully assigned payments/payment-api-7d9f4b6c5-x2vkm to kind-worker2",
        "count": 1,
        "first_timestamp": "2026-07-29T09:25:31Z",
        "last_timestamp": "2026-07-29T09:25:31Z",
    },
]

_STUB_LOG_LINES: list[str] = [
    "2026-07-29T10:14:02.112Z INFO  [payment-api] Starting PaymentApiApplication v2.14.3 (pid 1)",
    "2026-07-29T10:14:03.481Z INFO  [payment-api] Bootstrapped connectors: postgres=ok redis=ok kafka=ok",
    "2026-07-29T10:14:05.906Z INFO  [settlement] SettlementBatchProcessor: loading pending settlements window=24h",
    "2026-07-29T10:14:31.220Z WARN  [settlement] Loaded 412388 settlement rows; heap usage 912Mi/1024Mi",
    "2026-07-29T10:14:44.517Z ERROR [payment-api] Uncaught exception in thread settlement-batch-1",
    "java.lang.OutOfMemoryError: Java heap space",
    "\tat com.keep.payments.settlement.SettlementBatchProcessor.loadPendingSettlements(SettlementBatchProcessor.java:148)",
    "\tat com.keep.payments.settlement.SettlementBatchProcessor.run(SettlementBatchProcessor.java:92)",
    "\tat java.base/java.util.concurrent.Executors$RunnableAdapter.call(Executors.java:539)",
    "\tat java.base/java.util.concurrent.FutureTask.runAndReset(FutureTask.java:305)",
    "2026-07-29T10:14:47.903Z ERROR [payment-api] Fatal error, container exiting (reason=OOMKilled, exit_code=137)",
    "2026-07-29T10:14:48.004Z INFO  [payment-api] Shutdown hook skipped: JVM already terminating",
]

# ---------------------------------------------------------------------------
# Live backend (optional dependency)
# ---------------------------------------------------------------------------


def _live_core_v1():
    try:
        from kubernetes import client, config
    except ImportError as exc:
        raise KubernetesBackendUnavailable(
            "kubernetes client not installed; install keep-aiops[live] or set MCP_K8S_MODE=stub"
        ) from exc
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config()
        except Exception as exc:
            raise KubernetesBackendUnavailable(f"cannot load kubeconfig: {exc}") from exc
    return client.CoreV1Api()


def _summarize_pod(pod: Any) -> dict[str, Any]:
    container_statuses = pod.status.container_statuses or []
    first = container_statuses[0] if container_statuses else None
    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "ready": bool(first and first.ready),
        "restarts": first.restart_count if first else 0,
        "container": first.name if first else None,
        "node": pod.spec.node_name,
        "state": first.state.to_dict() if first and first.state else {},
    }


def _is_not_found(exc: Exception) -> bool:
    """Whether a kubernetes client error means "absent", not "unreachable".

    Matched on the client's own status attribute rather than the message,
    so a pod whose name happens to contain "404" cannot fake it.
    """
    return getattr(exc, "status", None) == 404


def _live_get_pods(namespace: str | None) -> dict[str, Any]:
    try:
        api = _live_core_v1()
        if namespace:
            items = api.list_namespaced_pod(namespace).items
        else:
            items = api.list_pod_for_all_namespaces().items
    except KubernetesBackendUnavailable:
        raise
    except Exception as exc:
        raise KubernetesBackendUnavailable(f"kubernetes API call failed: {exc}") from exc
    return {"backend": "live", "namespace": namespace or "all", "pods": [_summarize_pod(p) for p in items]}


def _live_get_events(namespace: str | None) -> dict[str, Any]:
    try:
        api = _live_core_v1()
        if namespace:
            items = api.list_namespaced_event(namespace).items
        else:
            items = api.list_event_for_all_namespaces().items
    except KubernetesBackendUnavailable:
        raise
    except Exception as exc:
        raise KubernetesBackendUnavailable(f"kubernetes API call failed: {exc}") from exc
    events = [
        {
            "type": e.type,
            "reason": e.reason,
            "namespace": e.metadata.namespace,
            "object": f"{e.involved_object.kind.lower()}/{e.involved_object.name}",
            "message": e.message,
            "count": e.count,
            "last_timestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
        }
        for e in items
    ]
    return {"backend": "live", "namespace": namespace or "all", "events": events}


def _live_get_logs(pod: str, namespace: str, tail_lines: int) -> dict[str, Any]:
    try:
        api = _live_core_v1()
        text = api.read_namespaced_pod_log(name=pod, namespace=namespace, tail_lines=tail_lines)
    except KubernetesBackendUnavailable:
        raise
    except Exception as exc:
        # A pod that is not there is an answer about the system, not a
        # failure of the tooling. Investigations routinely ask about
        # services that do not run in this cluster, and calling that
        # "backend unavailable" pointed the RCA at a healthy gateway.
        if _is_not_found(exc):
            raise ResourceNotFound(
                f"pod '{pod}' not found in namespace '{namespace}'"
            ) from exc
        raise KubernetesBackendUnavailable(f"kubernetes API call failed: {exc}") from exc
    return {"backend": "live", "pod": pod, "namespace": namespace, "lines": text.splitlines()}


# ---------------------------------------------------------------------------
# Tool registrations (all read-class)
# ---------------------------------------------------------------------------


@register_tool(
    name="get_pods",
    description="List Kubernetes pods with phase, readiness, restarts and waiting state (e.g. CrashLoopBackOff).",
    input_schema=GET_PODS_SCHEMA,
    mode_setting="k8s_mode",
)
def get_pods(namespace: str | None = None) -> dict[str, Any]:
    if integrations.mode("k8s") == "live":
        return _live_get_pods(namespace)
    pods = _STUB_PODS if namespace is None else [p for p in _STUB_PODS if p["namespace"] == namespace]
    return {"backend": "stub", "namespace": namespace or "all", "pods": pods}


@register_tool(
    name="get_events",
    description="List Kubernetes events (warnings such as BackOff / OOMKilling first-class for RCA).",
    input_schema=GET_EVENTS_SCHEMA,
    mode_setting="k8s_mode",
)
def get_events(namespace: str | None = None) -> dict[str, Any]:
    if integrations.mode("k8s") == "live":
        return _live_get_events(namespace)
    events = _STUB_EVENTS if namespace is None else [e for e in _STUB_EVENTS if e["namespace"] == namespace]
    return {"backend": "stub", "namespace": namespace or "all", "events": events}


@register_tool(
    name="get_logs",
    description="Fetch tail log lines for a pod (default 100 lines).",
    input_schema=GET_LOGS_SCHEMA,
    mode_setting="k8s_mode",
)
def get_logs(pod: str, namespace: str = "default", tail_lines: int = 100) -> dict[str, Any]:
    if integrations.mode("k8s") == "live":
        return _live_get_logs(pod, namespace, tail_lines)
    return {
        "backend": "stub",
        "pod": pod,
        "namespace": namespace,
        "container": pod.split("-")[0] if "-" in pod else pod,
        "lines": _STUB_LOG_LINES[-tail_lines:],
    }
