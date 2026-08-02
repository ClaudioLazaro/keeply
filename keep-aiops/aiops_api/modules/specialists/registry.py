"""Registry of built-in specialists.

The default set covers the M3 scope (kubernetes, prometheus, datadog, eks,
rds, argocd, jira, slack, bitbucket, backstage). New specialists must be
added to :data:`default_specialists` AND register their MCP tools in
``mcp_gateway.tools``.
"""

from __future__ import annotations

from aiops_api.modules.specialists.base import Specialist
from aiops_api.modules.specialists.builtin import (
    ArgoCdSpecialist,
    AwsEksSpecialist,
    AwsRdsSpecialist,
    BackstageSpecialist,
    BitbucketSpecialist,
    DatadogSpecialist,
    JiraSpecialist,
    KubernetesSpecialist,
    PrometheusSpecialist,
    SlackSpecialist,
)

_BUILTINS: tuple[Specialist, ...] = (
    KubernetesSpecialist(),
    PrometheusSpecialist(),
    DatadogSpecialist(),
    AwsEksSpecialist(),
    AwsRdsSpecialist(),
    ArgoCdSpecialist(),
    JiraSpecialist(),
    SlackSpecialist(),
    BitbucketSpecialist(),
    BackstageSpecialist(),
)

_REGISTRY: dict[str, Specialist] = {spec.name: spec for spec in _BUILTINS}


def get_specialist(name: str) -> Specialist | None:
    return _REGISTRY.get(name)


def default_specialists() -> tuple[Specialist, ...]:
    """Return the ordered default roster (stable order for tests)."""
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))
