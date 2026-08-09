"""Canned demo payloads (the M0 payment-api CrashLoopBackOff / OOMKilled story).

Carried over from ``mcp_gateway/tools/k8s.py`` so the demo narrative and the
golden-set fixtures keep telling the same story. The difference is that these
now travel inside a result whose ``backend`` field says ``stub`` — the reader
cannot mistake them for telemetry, which is the whole reason the field has no
default.
"""

from mcp_servers.k8s.models import EventSummary, PodSummary

STUB_NAMESPACE = "payments"

STUB_PODS = [
    PodSummary(
        name="payment-api-7d9f4b6c5-x2vkm",
        namespace=STUB_NAMESPACE,
        phase="Running",
        ready=False,
        restarts=14,
        container="payment-api",
        node="kind-worker2",
        reason="CrashLoopBackOff",
    ),
    PodSummary(
        name="api-gateway-6b8c7d9f4-q1zrt",
        namespace=STUB_NAMESPACE,
        phase="Running",
        ready=True,
        restarts=0,
        container="api-gateway",
        node="kind-worker",
    ),
    PodSummary(
        name="settlement-worker-5c6f7d8b9-m4pln",
        namespace=STUB_NAMESPACE,
        phase="Running",
        ready=True,
        restarts=1,
        container="settlement-worker",
        node="kind-worker2",
    ),
]

STUB_EVENTS = [
    EventSummary(
        type="Warning",
        reason="BackOff",
        namespace=STUB_NAMESPACE,
        object="pod/payment-api-7d9f4b6c5-x2vkm",
        message="Back-off restarting failed container payment-api",
        count=12,
        last_timestamp="2026-07-29T10:14:52Z",
    ),
    EventSummary(
        type="Warning",
        reason="Unhealthy",
        namespace=STUB_NAMESPACE,
        object="pod/payment-api-7d9f4b6c5-x2vkm",
        message="Liveness probe failed: Get http://10.244.1.37:8080/healthz: context deadline exceeded",
        count=6,
        last_timestamp="2026-07-29T10:13:55Z",
    ),
]

STUB_LOG_LINES = [
    "2026-07-29T10:14:41.882Z INFO  payment-api  starting settlement batch id=b-5512 size=4096",
    "2026-07-29T10:14:48.117Z WARN  payment-api  heap usage 91% (max 512Mi)",
    "2026-07-29T10:14:51.043Z ERROR payment-api  java.lang.OutOfMemoryError: Java heap space",
    "2026-07-29T10:14:51.044Z ERROR payment-api      at com.acme.payments.SettlementBatch.load(SettlementBatch.java:88)",
    "2026-07-29T10:14:52.901Z ERROR payment-api  container terminated: OOMKilled (exit 137)",
]
