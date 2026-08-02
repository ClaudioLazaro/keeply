"""Declarative catalog of the integrations an operator can configure.

Single source of truth for: which integrations exist, which settings each
one needs, and which of those are secret. The API, the UI form and the
gateway resolution all derive from this, so adding an integration is one
entry here plus the tool module — not four parallel edits that drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    """One configurable value. ``secret=True`` means it is encrypted at
    rest and never returned by the API (only a masked tail)."""

    name: str
    label: str
    secret: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class IntegrationSpec:
    """One backend the MCP gateway can talk to.

    ``settings_prefix`` maps to the gateway's env settings (``k8s`` ->
    ``MCP_K8S_MODE``/``k8s_mode``), which stay the bootstrap defaults when
    nothing is configured through the UI.
    """

    name: str
    label: str
    settings_prefix: str
    tools: tuple[str, ...]
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)
    # Some backends need no configuration to go live (in-cluster K8s uses
    # the mounted ServiceAccount; AWS uses the ambient credential chain).
    live_requires_config: bool = True
    notes: str = ""


URL = lambda name="url", label="URL", placeholder="": FieldSpec(  # noqa: E731
    name=name, label=label, placeholder=placeholder
)

INTEGRATIONS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        name="k8s",
        label="Kubernetes",
        settings_prefix="k8s",
        tools=("get_pods", "get_events", "get_logs"),
        live_requires_config=False,
        notes=(
            "Live mode uses the pod's ServiceAccount. It needs the read-only "
            "`keep-mcp-reader` ClusterRole to be bound."
        ),
    ),
    IntegrationSpec(
        name="prometheus",
        label="Prometheus",
        settings_prefix="prometheus",
        tools=("prom_alerts", "prom_query", "prom_query_range"),
        fields=(URL(placeholder="http://prometheus:9090"),),
    ),
    IntegrationSpec(
        name="datadog",
        label="Datadog",
        settings_prefix="datadog",
        tools=("dd_query_metrics", "dd_list_events"),
        fields=(
            URL(placeholder="https://api.datadoghq.com"),
            FieldSpec("api_key", "API key", secret=True),
            FieldSpec("app_key", "Application key", secret=True),
        ),
    ),
    IntegrationSpec(
        name="eks",
        label="AWS EKS",
        settings_prefix="eks",
        tools=("eks_list_clusters", "eks_describe_nodegroups"),
        live_requires_config=False,
        notes="Live mode uses the ambient AWS credential chain (IRSA, env, or profile).",
    ),
    IntegrationSpec(
        name="rds",
        label="AWS RDS",
        settings_prefix="rds",
        tools=("rds_list_instances", "rds_describe_instance_status"),
        live_requires_config=False,
        notes="Live mode uses the ambient AWS credential chain (IRSA, env, or profile).",
    ),
    IntegrationSpec(
        name="argocd",
        label="ArgoCD",
        settings_prefix="argocd",
        tools=("argocd_list_apps", "argocd_get_app"),
        fields=(
            URL(placeholder="https://argocd.example.com"),
            FieldSpec("token", "API token", secret=True),
        ),
    ),
    IntegrationSpec(
        name="jira",
        label="Jira",
        settings_prefix="jira",
        tools=("jira_search_issues",),
        fields=(
            URL(placeholder="https://your-org.atlassian.net"),
            FieldSpec("token", "API token", secret=True),
        ),
    ),
    IntegrationSpec(
        name="slack",
        label="Slack",
        settings_prefix="slack",
        tools=("slack_search_messages",),
        fields=(
            URL(placeholder="https://slack.com/api"),
            FieldSpec("token", "Bot token", secret=True, placeholder="xoxb-…"),
        ),
    ),
    IntegrationSpec(
        name="bitbucket",
        label="Bitbucket",
        settings_prefix="bitbucket",
        tools=("bb_list_recent_commits", "bb_list_open_pull_requests"),
        fields=(
            URL(placeholder="https://api.bitbucket.org/2.0"),
            FieldSpec("user", "Username"),
            FieldSpec("token", "App password", secret=True),
        ),
    ),
    IntegrationSpec(
        name="backstage",
        label="Backstage",
        settings_prefix="backstage",
        tools=("backstage_get_entity",),
        fields=(URL(placeholder="https://backstage.example.com"),),
    ),
)

BY_NAME = {spec.name: spec for spec in INTEGRATIONS}


def get_spec(name: str) -> IntegrationSpec | None:
    return BY_NAME.get(name)


def secret_fields(spec: IntegrationSpec) -> tuple[str, ...]:
    return tuple(item.name for item in spec.fields if item.secret)
