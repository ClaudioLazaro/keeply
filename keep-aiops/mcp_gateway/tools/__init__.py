"""MCP tool registry.

Tools self-register via the ``@register_tool`` decorator; adding a new MCP
server means adding one module under ``mcp_gateway/tools/`` and importing it
at the bottom of this file. The gateway's policy gate only lets
``execution_class == "read"`` tools through (M0 is suggest-only, fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    execution_class: str = "read"
    # Name of the settings attribute holding this tool's stub/live mode
    # (e.g. "k8s_mode"). The catalog resolves it so consumers can tell
    # real evidence from canned demo payloads — a stub result is
    # indistinguishable from a live one once it is a dict.
    mode_setting: str | None = None


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    execution_class: str = "read",
    mode_setting: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator registering ``fn`` as an MCP tool."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise ValueError(f"duplicate tool registration: {name}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
            execution_class=execution_class,
            mode_setting=mode_setting,
        )
        return fn

    return decorator


def unregister_tool(name: str) -> None:
    """Remove a tool from the registry (primarily for tests)."""
    _REGISTRY.pop(name, None)


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def tool_mode(spec: ToolSpec) -> str:
    """Resolve a tool's current stub/live mode.

    Goes through the same resolution the tool itself uses (operator config
    over env default) so the catalog can never advertise a mode that
    differs from what an invocation would actually do.

    Returns "unknown" when the tool declares no mode setting — better an
    explicit unknown than silently claiming the result is live.
    """
    if spec.mode_setting is None:
        return "unknown"
    from mcp_gateway import integrations

    # mode_setting is "<integration>_mode"; the resolver keys on the
    # integration name.
    return integrations.mode(spec.mode_setting.removesuffix("_mode"))


def catalog() -> list[dict[str, Any]]:
    """Catalog entries: name/description/execution_class/input_schema/mode.

    ``mode`` tells the consumer whether invoking this tool yields real data
    ("live") or a canned demo payload ("stub"). Without it, an RCA built on
    stub evidence looks exactly like one built on production telemetry.
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "execution_class": spec.execution_class,
            "input_schema": spec.input_schema,
            "mode": tool_mode(spec),
        }
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.name)
    ]


# ---------------------------------------------------------------------------
# Minimal JSON-Schema validation (object level: required, properties, types,
# additionalProperties, minimum/maximum, enum). Enough for M0 tool schemas
# without pulling in a full jsonschema dependency.
# ---------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def validate_arguments(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Return a list of validation errors; empty means valid."""
    if not isinstance(arguments, dict):
        return ["arguments must be a JSON object"]
    errors: list[str] = []
    properties: dict[str, Any] = schema.get("properties") or {}

    for key in schema.get("required") or []:
        if key not in arguments:
            errors.append(f"missing required argument '{key}'")

    if schema.get("additionalProperties") is False:
        for key in arguments:
            if key not in properties:
                errors.append(f"unexpected argument '{key}'")

    for key, value in arguments.items():
        spec = properties.get(key)
        if not spec:
            continue
        expected = spec.get("type")
        if expected:
            check = _TYPE_CHECKS.get(expected)
            if check is not None and not check(value):
                errors.append(f"argument '{key}' must be of type '{expected}'")
                continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append(f"argument '{key}' must be >= {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                errors.append(f"argument '{key}' must be <= {spec['maximum']}")
        if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
            errors.append(f"argument '{key}' must have length >= {spec['minLength']}")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"argument '{key}' must be one of {spec['enum']}")
    return errors


# Tool servers self-register on import. Add new servers here.
from mcp_gateway.tools import k8s as k8s  # noqa: E402,F401
from mcp_gateway.tools import prometheus as prometheus  # noqa: E402,F401
from mcp_gateway.tools import datadog as datadog  # noqa: E402,F401
from mcp_gateway.tools import aws_eks as aws_eks  # noqa: E402,F401
from mcp_gateway.tools import aws_rds as aws_rds  # noqa: E402,F401
from mcp_gateway.tools import argocd as argocd  # noqa: E402,F401
from mcp_gateway.tools import jira as jira  # noqa: E402,F401
from mcp_gateway.tools import slack as slack  # noqa: E402,F401
from mcp_gateway.tools import bitbucket as bitbucket  # noqa: E402,F401
from mcp_gateway.tools import backstage as backstage  # noqa: E402,F401
