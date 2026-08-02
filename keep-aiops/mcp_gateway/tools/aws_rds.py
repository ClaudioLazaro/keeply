"""AWS RDS MCP tool server (M3, read-only).

``MCP_RDS_MODE=stub`` (default) returns canned RDS instance data.

``MCP_RDS_MODE=live`` uses boto3 (optional dep) to list instances and read
status. Missing credentials or unreachable API surface as
:class:`RdsBackendUnavailable`, which the gateway maps to 503.

All tools are read-class (ADR-0003).
"""

from __future__ import annotations

from typing import Any

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings
from mcp_gateway.tools import register_tool
from mcp_gateway.tools.backend import BackendUnavailable


class RdsBackendUnavailable(BackendUnavailable):
    """Raised when the live AWS RDS backend cannot serve a request."""


RDS_LIST_INSTANCES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

RDS_DESCRIBE_INSTANCE_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"instance_id": {"type": "string", "minLength": 1}},
    "required": ["instance_id"],
    "additionalProperties": False,
}


_STUB_INSTANCES: list[dict[str, Any]] = [
    {"id": "payments-db", "engine": "postgres", "version": "15.4", "status": "available", "class": "db.m6g.large"},
    {"id": "settlements-db", "engine": "postgres", "version": "15.4", "status": "available", "class": "db.m6g.xlarge"},
]

_STUB_INSTANCE_STATUS: dict[str, dict[str, Any]] = {
    "payments-db": {
        "id": "payments-db",
        "cpu_utilization": 78.4,
        "connections": 412,
        "replication_lag_seconds": 0.0,
        "freeable_memory_mb": 312,
        "status": "available",
    },
    "settlements-db": {
        "id": "settlements-db",
        "cpu_utilization": 22.1,
        "connections": 81,
        "replication_lag_seconds": 0.3,
        "freeable_memory_mb": 4096,
        "status": "available",
    },
}


def _live_client() -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise RdsBackendUnavailable("boto3 not installed; pip install keep-aiops[live]") from exc
    try:
        return boto3.client("rds")
    except Exception as exc:  # noqa: BLE001
        raise RdsBackendUnavailable(f"cannot create RDS client: {exc}") from exc


@register_tool(
    name="rds_list_instances",
    description="List RDS DB instances in the current region (live mode requires AWS credentials).",
    input_schema=RDS_LIST_INSTANCES_SCHEMA,
    mode_setting="rds_mode",
)
def rds_list_instances() -> dict[str, Any]:
    if integrations.mode("rds") == "live":
        try:
            client = _live_client()
            data = client.describe_db_instances()
        except RdsBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RdsBackendUnavailable(f"RDS describe_db_instances failed: {exc}") from exc
        return {"backend": "live", "instances": [{"id": d["DBInstanceIdentifier"]} for d in data["DBInstances"]]}
    return {"backend": "stub", "instances": _STUB_INSTANCES}


@register_tool(
    name="rds_describe_instance_status",
    description="Read health metrics (CPU, connections, lag) for one RDS instance.",
    input_schema=RDS_DESCRIBE_INSTANCE_STATUS_SCHEMA,
    mode_setting="rds_mode",
)
def rds_describe_instance_status(instance_id: str) -> dict[str, Any]:
    if integrations.mode("rds") == "live":
        try:
            client = _live_client()
            data = client.describe_db_instances(DBInstanceIdentifier=instance_id)
            inst = data["DBInstances"][0]
            result: dict[str, Any] = {
                "id": inst["DBInstanceIdentifier"],
                "status": inst["DBInstanceStatus"],
                "engine": inst["Engine"],
                "class": inst["DBInstanceClass"],
            }
        except RdsBackendUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RdsBackendUnavailable(f"RDS describe failed: {exc}") from exc
        return {"backend": "live", **result}
    status = _STUB_INSTANCE_STATUS.get(instance_id)
    if status is None:
        return {"backend": "stub", "id": instance_id, "error": "not found"}
    return {"backend": "stub", **status}
