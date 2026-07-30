"""Structured audit for tool invocations (ADR-0002: every call is audited).

One JSON line per invocation with ts, tenant_id, investigation_id, tool,
args-hash, outcome, duration_ms. Emitted to the ``mcp_gateway.audit`` logger
and optionally appended to ``MCP_AUDIT_LOG_PATH``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp_gateway.audit")


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Stable short hash of arguments; raw args (may hold secrets) are never logged."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_audit_entry(
    *,
    audit_id: str,
    tenant_id: str,
    investigation_id: str,
    tool: str,
    arguments: dict[str, Any],
    outcome: str,
    duration_ms: float,
) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "audit_id": audit_id,
        "tenant_id": tenant_id,
        "investigation_id": investigation_id,
        "tool": tool,
        "args_hash": hash_arguments(arguments),
        "outcome": outcome,
        "duration_ms": round(duration_ms, 2),
    }


def record_audit(entry: dict[str, Any], audit_log_path: str | None) -> None:
    line = json.dumps(entry)
    logger.info(line)
    if audit_log_path:
        try:
            with Path(audit_log_path).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("failed to write audit log to %s", audit_log_path)
