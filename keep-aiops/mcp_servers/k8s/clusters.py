"""Cluster registry — which Kubernetes clusters this server may talk to.

The gateway this replaces had exactly one target and never named it. It
called ``load_incluster_config()`` with a kubeconfig fallback, so the cluster
it answered about was "wherever this pod happens to run". Nothing in the tool
contract could express a different one, and nothing in the result recorded
which one had answered.

That is not a multi-cluster limitation, it is a correctness one: on a shared
cluster the old ``get_pods`` returned every namespace it could see —
including workloads belonging to unrelated projects — and the coordinator
picked one by a "most troubled pod" heuristic. Evidence about a stranger's
CrashLoopBackOff was filed against your incident, stamped ``live``, and
believed.

So targets are named, declared up front, and required at call time.

Configuration (``MCP_K8S_CLUSTERS``), a JSON array:

    [
      {"name": "prod-eu",  "context": "arn:aws:eks:eu-west-1:...", "mode": "live"},
      {"name": "in-cluster", "in_cluster": true, "mode": "live"},
      {"name": "demo",     "mode": "stub"}
    ]

Unset falls back to a single ``in-cluster`` entry in the mode named by
``MCP_K8S_MODE`` (default ``stub``), which keeps existing deployments working
without a config change — but they now say *which* cluster in every result.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class ClusterUnavailable(Exception):
    """The named cluster exists but could not be reached."""


class UnknownCluster(Exception):
    """The named cluster is not in the registry."""


@dataclass(frozen=True)
class ClusterSpec:
    name: str
    mode: str  # "live" | "stub"
    in_cluster: bool = False
    context: str | None = None  # kubeconfig context name
    kubeconfig: str | None = None  # path, when not the default
    description: str | None = None


def _parse_registry() -> dict[str, ClusterSpec]:
    raw = os.environ.get("MCP_K8S_CLUSTERS", "").strip()
    if not raw:
        # Backwards-compatible single target. Named explicitly so results are
        # still attributable, even though nothing had to be configured.
        mode = os.environ.get("MCP_K8S_MODE", "stub").strip().lower()
        return {
            "in-cluster": ClusterSpec(
                name="in-cluster",
                mode="live" if mode == "live" else "stub",
                in_cluster=True,
                description="Default target: the cluster this server runs in.",
            )
        }

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        # Fail loud but keep serving stubs: a malformed registry that
        # silently became "the local cluster" is the exact failure this
        # module exists to prevent.
        logger.exception("MCP_K8S_CLUSTERS is not valid JSON; falling back to a single stub target")
        return {"in-cluster": ClusterSpec(name="in-cluster", mode="stub", in_cluster=True)}

    registry: dict[str, ClusterSpec] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        mode = str(entry.get("mode", "stub")).lower()
        registry[entry["name"]] = ClusterSpec(
            name=entry["name"],
            mode="live" if mode == "live" else "stub",
            in_cluster=bool(entry.get("in_cluster")),
            context=entry.get("context"),
            kubeconfig=entry.get("kubeconfig"),
            description=entry.get("description"),
        )
    if not registry:
        logger.warning("MCP_K8S_CLUSTERS parsed to no usable entries; falling back to a single stub target")
        return {"in-cluster": ClusterSpec(name="in-cluster", mode="stub", in_cluster=True)}
    return registry


_registry: dict[str, ClusterSpec] | None = None
_registry_lock = threading.Lock()


def registry() -> dict[str, ClusterSpec]:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = _parse_registry()
        return _registry


def reset_registry() -> None:
    """Drop the parsed registry and every cached client (tests)."""
    global _registry
    with _registry_lock:
        _registry = None
    with _clients_lock:
        _clients.clear()


def get(name: str) -> ClusterSpec:
    spec = registry().get(name)
    if spec is None:
        known = ", ".join(sorted(registry())) or "none configured"
        raise UnknownCluster(f"unknown cluster {name!r}; registered clusters: {known}")
    return spec


# --------------------------------------------------------------------------- #
# Live clients
# --------------------------------------------------------------------------- #

# One API client per cluster, built once. The previous implementation called
# load_incluster_config() on EVERY tool invocation, re-reading the service
# account token from disk each time.
_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()


def core_v1(spec: ClusterSpec):
    """Cached CoreV1Api for a live cluster. Raises ClusterUnavailable."""
    with _clients_lock:
        cached = _clients.get(spec.name)
        if cached is not None:
            return cached

    try:
        from kubernetes import client, config
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ClusterUnavailable(
            "kubernetes client not installed; install keep-aiops[live] or set this cluster's mode to stub"
        ) from exc

    try:
        if spec.in_cluster:
            config.load_incluster_config()
            api_client = client.ApiClient()
        else:
            api_client = config.new_client_from_config(
                config_file=spec.kubeconfig, context=spec.context
            )
    except Exception as exc:  # noqa: BLE001 — every load path failure is the same to us
        raise ClusterUnavailable(f"cannot load credentials for cluster {spec.name!r}: {exc}") from exc

    api = client.CoreV1Api(api_client)
    with _clients_lock:
        _clients[spec.name] = api
    return api
