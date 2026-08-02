"""AWS EKS MCP tool server (M3, read-only).

``MCP_EKS_MODE=stub`` (default) returns canned EKS cluster and nodegroup
data aligned with the M0 ``payments-prod`` cluster.

``MCP_EKS_MODE=live`` uses boto3 (optional dep, install with
``pip install keep-aiops[live]``) to list clusters and describe
nodegroups. Missing AWS credentials or unreachable API surface as
:class:`EksBackendUnavailable`, which the gateway maps to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class EksBackendUnavailable(BackendUnavailable):
    """Raised when the live AWS EKS backend cannot serve a request."""


EKS_LIST_CLUSTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

EKS_DESCRIBE_NODEGROUPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cluster_name": {"type": "string", "minLength": 1}},
    "required": ["cluster_name"],
    "additionalProperties": False,
}


_STUB_CLUSTERS: list[dict[str, Any]] = [
    {"name": "payments-prod", "region": "us-east-1", "status": "ACTIVE", "version": "1.29"},
    {"name": "payments-staging", "region": "us-east-1", "status": "ACTIVE", "version": "1.29"},
]


_STUB_NODEGROUPS: dict[str, list[dict[str, Any]]] = {
    "payments-prod": [
        {"name": "ng-payments-1", "instanceType": "m6i.xlarge", "desiredSize": 3, "minSize": 3, "maxSize": 6, "health": "HEALTHY"},
        {"name": "ng-payments-2", "instanceType": "m6i.2xlarge", "desiredSize": 2, "minSize": 2, "maxSize": 5, "health": "DEGRADED"},
    ],
    "payments-staging": [
        {"name": "ng-staging", "instanceType": "m6i.large", "desiredSize": 1, "minSize": 1, "maxSize": 2, "health": "HEALTHY"},
    ],
}


def _live_client() -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise EksBackendUnavailable("boto3 not installed; pip install keep-aiops[live]") from exc
    try:
        return boto3.client("eks")
    except Exception as exc:  # noqa: BLE001
        raise EksBackendUnavailable(f"cannot create EKS client: {exc}") from exc


@register_tool(
    name="eks_list_clusters",
    description="List EKS clusters in the current account (live mode requires AWS credentials).",
    input_schema=EKS_LIST_CLUSTERS_SCHEMA,
    mode_setting="eks_mode",
)
def eks_list_clusters() -> dict[str, Any]:
    if integrations.mode("eks") == "live":
        try:
            client = _live_client()
            names = client.list_clusters()["clusters"]
        except EksBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EksBackendUnavailable(f"EKS list_clusters failed: {exc}") from exc
        return {"backend": "live", "clusters": [{"name": n} for n in names]}
    return {"backend": "stub", "clusters": _STUB_CLUSTERS}


@register_tool(
    name="eks_describe_nodegroups",
    description="Describe nodegroups for an EKS cluster (live mode requires AWS credentials).",
    input_schema=EKS_DESCRIBE_NODEGROUPS_SCHEMA,
    mode_setting="eks_mode",
)
def eks_describe_nodegroups(cluster_name: str) -> dict[str, Any]:
    if integrations.mode("eks") == "live":
        try:
            client = _live_client()
            data = client.list_nodegroups(clusterName=cluster_name)
            groups: list[dict[str, Any]] = []
            for ng in data.get("nodegroups", []):
                groups.append(
                    client.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng)["nodegroup"]
                )
        except EksBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EksBackendUnavailable(f"EKS describe_nodegroup failed: {exc}") from exc
        return {"backend": "live", "cluster": cluster_name, "nodegroups": groups}
    groups = _STUB_NODEGROUPS.get(cluster_name, [])
    return {"backend": "stub", "cluster": cluster_name, "nodegroups": groups}
