"""Built-in specialist implementations (M3).

Each specialist owns a fixed set of MCP tool names. ``gather`` is the single
entry point and MUST never raise out of a tool failure: every call's
exception is converted into a :class:`ToolCall` with ``error`` set so the
coordinator can record an evidence gap.

Specialists are intentionally minimal — no LLM calls, no Keep calls. The
RCA writer is the only consumer of the LLM.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from aiops_api.modules.specialists.base import (
    Budget,
    BudgetTracker,
    Scope,
    Specialist,
    SpecialistResult,
    ToolCall,
)

logger = logging.getLogger(__name__)

# Type alias for the gateway invoke closure the coordinator hands to each
# specialist. Returns ``(result, audit_id_or_none)`` or raises.
InvokeFn = Callable[[str, dict[str, Any]], tuple[Any, str | None]]


def _safe_call(
    tool: str,
    arguments: dict[str, Any],
    invoke: InvokeFn,
    used: BudgetTracker,
) -> ToolCall:
    """Invoke one tool, incrementing the budget counter and converting errors.

    The specialist never sees the exception: we always return a
    :class:`ToolCall` so the coordinator can persist it as evidence.
    """
    used.record_tool_call()
    try:
        result, audit_id = invoke(tool, arguments)
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        return ToolCall(tool=tool, arguments=arguments, error=f"{type(exc).__name__}: {exc}")
    return ToolCall(tool=tool, arguments=arguments, result=result, audit_id=audit_id)


def _summarize(tool: str, result: Any) -> str:
    """Best-effort one-line summary for the evidence table."""
    if isinstance(result, dict):
        for key in ("pods", "items", "events", "alerts", "results", "applications", "repositories"):
            value = result.get(key)
            if isinstance(value, list):
                return f"{tool}: {len(value)} {key} returned"
        if isinstance(result.get("logs"), str):
            lines = result["logs"].count("\n") + 1
            return f"{tool}: {lines} log lines returned"
        if isinstance(result.get("lines"), list):
            return f"{tool}: {len(result['lines'])} log lines returned"
        if "result" in result and isinstance(result["result"], list):
            return f"{tool}: {len(result['result'])} results returned"
    if isinstance(result, list):
        return f"{tool}: {len(result)} items returned"
    return f"{tool}: result received ({type(result).__name__})"


def _pod_identity(pod: Any) -> tuple[str, str | None] | None:
    """Extract ``(name, namespace)`` from a pod entry, or None if unusable.

    The namespace matters: pod names are only unique within one, and
    ``get_logs`` defaults to ``default`` when it is omitted. Dropping it is
    how every investigation ended up with a failing ``get_logs`` call.
    """
    if isinstance(pod, str):
        return pod, None
    if not isinstance(pod, dict):
        return None
    name = pod.get("name")
    namespace = pod.get("namespace")
    if not isinstance(name, str):
        metadata = pod.get("metadata")
        if isinstance(metadata, dict):
            name = metadata.get("name")
            namespace = metadata.get("namespace", namespace)
    if not isinstance(name, str):
        return None
    return name, namespace if isinstance(namespace, str) else None


def _pod_trouble_score(pod: Any) -> int:
    """Rank how interesting a pod is for root-cause analysis.

    Logs from a healthy pod are noise. An investigation should read the
    logs of whatever looks broken, so prefer (in order) a pod that is
    waiting on something like CrashLoopBackOff, then one that is not
    ready, then one that has restarted.
    """
    if not isinstance(pod, dict):
        return 0
    score = 0
    state = pod.get("state")
    if isinstance(state, dict) and isinstance(state.get("waiting"), dict):
        score += 100
    if pod.get("phase") not in (None, "Running", "Succeeded"):
        score += 50
    if pod.get("ready") is False:
        score += 25
    restarts = pod.get("restarts")
    if isinstance(restarts, int):
        score += min(restarts, 20)
    return score


def _requires(catalog: dict[str, Any], tool: str, argument: str) -> bool:
    """Whether the live catalog says this tool requires this argument.

    Lets one specialist serve both tool meshes. The legacy HTTP tools resolved
    their Kubernetes target implicitly and took no ``cluster``; the MCP server
    requires it, precisely so the target can never be implicit again. Reading
    the schema instead of branching on a transport flag means the specialist
    keeps working through either, and keeps working when a tool's contract
    tightens.
    """
    entry = catalog.get(tool) or {}
    schema = entry.get("input_schema") or entry.get("inputSchema") or {}
    return argument in (schema.get("required") or [])


def _accepts(catalog: dict[str, Any], tool: str, argument: str) -> bool:
    """Whether the tool takes this argument at all, per the live catalog."""
    entry = catalog.get(tool) or {}
    schema = entry.get("input_schema") or entry.get("inputSchema") or {}
    properties = schema.get("properties")
    # A tool with no published schema is assumed to accept it: the legacy
    # gateway published none and took `namespace` happily, and passing an
    # argument that is ignored is harmless next to not scoping at all.
    return argument in properties if isinstance(properties, dict) else True


def _target_args(catalog: dict[str, Any], tool: str) -> dict[str, Any]:
    """Cluster scoping for tools that demand it.

    The cluster name is configuration today (``AIOPS_MCP_DEFAULT_CLUSTER``).
    Deriving it from the incident's affected services is the next step and the
    one that actually closes the gap — a configured default is still a single
    target, it is just an *honest* one: it is declared, and every result says
    which cluster answered rather than leaving it to be inferred.
    """
    if not _requires(catalog, tool, "cluster"):
        return {}
    from aiops_api.settings import get_settings

    return {"cluster": get_settings().mcp_default_cluster}


def _locate_services(
    catalog: dict[str, Any],
    invoke: InvokeFn,
    used: BudgetTracker,
    scope: Scope,
) -> tuple[list[str], ToolCall | None]:
    """Find the namespaces the incident's services actually run in.

    Discovery beats convention. Mapping a service to a namespace by assuming
    they share a name works until it doesn't — a service called
    ``my-service-brasil`` may live in a namespace named something else
    entirely, while a pod called ``my-service-brasil-aja6sa`` sits there
    plainly saying so. One cluster-wide lookup answers that, and every query
    after it is scoped.

    The lookup is recorded as evidence, carrying the ``matched_by`` reason for
    each service. That is the point of doing the matching here rather than
    asking a model: an operator can check "we looked in `payments` because a
    pod named `my-service-brasil-aja6sa` is running there". A grouping nobody
    can audit is one nobody can correct.

    Falls back to the configured guess when the tool is unavailable (the
    legacy mesh has no such tool), and returns no namespaces when the incident
    named no service — the caller then sweeps, and labels the sweep.
    """
    if not scope.services or "find_workload" not in catalog:
        return list(scope.namespaces), None

    args = _target_args(catalog, "find_workload")
    args["services"] = list(scope.services)
    call = _safe_call("find_workload", args, invoke, used)
    if call.is_gap or not isinstance(call.result, dict):
        # Discovery failed; the configured guess is better than a sweep.
        return list(scope.namespaces), call

    matches = call.result.get("matches") or []
    namespaces: list[str] = []
    reasons: list[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        namespace = match.get("namespace")
        if isinstance(namespace, str) and namespace and namespace not in namespaces:
            namespaces.append(namespace)
        reasons.append(
            f"{match.get('service')} -> {namespace} ({match.get('matched_by')}"
            + (f", e.g. {match['sample_pod']}" if match.get("sample_pod") else "")
            + ")"
        )

    unresolved = [str(s) for s in (call.result.get("unresolved") or [])]
    summary = f"find_workload: located {len(namespaces)} namespace(s) — " + "; ".join(reasons)
    if unresolved:
        # Naming what could NOT be found matters: those services are absent
        # from the evidence that follows, and silence would hide that.
        summary += f". Not found: {', '.join(unresolved)}"
    call.summary = summary

    return namespaces or list(scope.namespaces), call


def _select_pod(pods_result: Any) -> tuple[str, str | None] | None:
    """Pick the pod whose logs are most likely to explain the incident."""
    items = None
    if isinstance(pods_result, dict):
        items = pods_result.get("pods") or pods_result.get("items")
    elif isinstance(pods_result, list):
        items = pods_result
    if not items:
        return None
    best = max(items, key=_pod_trouble_score)
    return _pod_identity(best)


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #


class KubernetesSpecialist:
    name = "kubernetes"
    tools: tuple[str, ...] = ("get_pods", "get_events", "get_logs", "find_workload")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget  # not used directly; the tracker enforces
        calls: list[ToolCall] = []

        namespaces, locate_call = _locate_services(catalog, invoke, used, scope)
        if locate_call is not None:
            calls.append(locate_call)

        # Ask each namespace the incident points at, rather than sweeping the
        # cluster and picking something afterwards. The sweep is what let a
        # stranger's CrashLoopBackOff become evidence for this incident.
        for namespace in namespaces or [None]:
            for tool in ("get_pods", "get_events"):
                args = _target_args(catalog, tool)
                if namespace and _accepts(catalog, tool, "namespace"):
                    args["namespace"] = namespace
                call = _safe_call(tool, args, invoke, used)
                if namespace is None and not call.is_gap:
                    # Say it plainly: this is everything the credential can
                    # see, not the incident's blast radius.
                    call.summary = (
                        f"{_summarize(tool, call.result)} "
                        "(UNSCOPED — the incident named no service to aim at)"
                    )
                calls.append(call)

        pods_call = next(
            (c for c in calls if c.tool == "get_pods" and not c.is_gap and _select_pod(c.result)),
            next((c for c in calls if c.tool == "get_pods"), None),
        )
        selected = _select_pod(pods_call.result) if pods_call and not pods_call.is_gap else None
        pod_name, pod_namespace = selected if selected else (None, None)

        if pod_name:
            logs_args: dict[str, Any] = {"pod": pod_name, "tail_lines": 100}
            logs_args.update(_target_args(catalog, "get_logs"))
            # Namespace is required: get_logs defaults to "default", and the
            # most interesting pod is rarely there.
            if pod_namespace:
                logs_args["namespace"] = pod_namespace
            calls.append(_safe_call("get_logs", logs_args, invoke, used))
        else:
            # No pod to read: record the gap explicitly instead of calling
            # get_logs with an empty name and letting the backend 500.
            calls.append(
                ToolCall(
                    tool="get_logs",
                    arguments={},
                    error="no pod available to read logs from (get_pods returned nothing usable)",
                )
            )

        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(
            specialist=self.name,
            calls=calls,
            extra_evidence={"pod_name": pod_name, "pod_namespace": pod_namespace},
        )


# --------------------------------------------------------------------------- #
# Prometheus
# --------------------------------------------------------------------------- #


class PrometheusSpecialist:
    name = "prometheus"
    tools: tuple[str, ...] = ("prom_alerts", "prom_query", "prom_query_range")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = []
        calls.append(_safe_call("prom_alerts", {}, invoke, used))
        calls.append(
            _safe_call(
                "prom_query",
                {"query": 'sum by (service) (rate(http_requests_total{status=~"5..",service="payment-api"}[5m]))'},
                invoke,
                used,
            )
        )
        calls.append(
            _safe_call(
                "prom_query_range",
                {
                    "query": 'sum(rate(http_requests_total{status=~"5..",service="payment-api"}[5m]))',
                },
                invoke,
                used,
            )
        )
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# Datadog
# --------------------------------------------------------------------------- #


class DatadogSpecialist:
    """Read metrics and events from Datadog's HTTP API (MCP read-only)."""

    name = "datadog"
    tools: tuple[str, ...] = ("dd_query_metrics", "dd_list_events")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call(
                "dd_query_metrics",
                {"query": "avg:http.request.error_rate{service:payment-api}", "window": "15m"},
                invoke,
                used,
            ),
            _safe_call(
                "dd_list_events",
                {"tags": "service:payment-api", "window": "30m"},
                invoke,
                used,
            ),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# AWS EKS
# --------------------------------------------------------------------------- #


class AwsEksSpecialist:
    """Read EKS cluster metadata + nodegroup health via boto3 (live mode)."""

    name = "aws_eks"
    tools: tuple[str, ...] = ("eks_list_clusters", "eks_describe_nodegroups")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call("eks_list_clusters", {}, invoke, used),
            _safe_call("eks_describe_nodegroups", {"cluster_name": "payments-prod"}, invoke, used),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# AWS RDS
# --------------------------------------------------------------------------- #


class AwsRdsSpecialist:
    """Read RDS instance health (CPU, connections, replication lag)."""

    name = "aws_rds"
    tools: tuple[str, ...] = ("rds_list_instances", "rds_describe_instance_status")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call("rds_list_instances", {}, invoke, used),
            _safe_call("rds_describe_instance_status", {"instance_id": "payments-db"}, invoke, used),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# ArgoCD
# --------------------------------------------------------------------------- #


class ArgoCdSpecialist:
    """Read ArgoCD Applications / ApplicationSets (GitOps drift detection)."""

    name = "argocd"
    tools: tuple[str, ...] = ("argocd_list_apps", "argocd_get_app")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call("argocd_list_apps", {}, invoke, used),
            _safe_call("argocd_get_app", {"name": "payment-api"}, invoke, used),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# Jira
# --------------------------------------------------------------------------- #


class JiraSpecialist:
    """Read recent Jira issues for the affected service (correlation with incidents)."""

    name = "jira"
    tools: tuple[str, ...] = ("jira_search_issues",)

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call(
                "jira_search_issues",
                {"jql": 'project = PAY AND summary ~ "payment-api" ORDER BY created DESC', "max_results": 5},
                invoke,
                used,
            ),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #


class SlackSpecialist:
    """Read recent Slack messages mentioning the service (human signal)."""

    name = "slack"
    tools: tuple[str, ...] = ("slack_search_messages",)

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call(
                "slack_search_messages",
                {"query": "payment-api", "window_hours": 24, "max_results": 10},
                invoke,
                used,
            ),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# Bitbucket
# --------------------------------------------------------------------------- #


class BitbucketSpecialist:
    """Read recent commits / PRs touching the affected service."""

    name = "bitbucket"
    tools: tuple[str, ...] = ("bb_list_recent_commits", "bb_list_open_pull_requests")

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call("bb_list_recent_commits", {"repo": "payments/payment-api", "max_results": 10}, invoke, used),
            _safe_call("bb_list_open_pull_requests", {"repo": "payments/payment-api"}, invoke, used),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)


# --------------------------------------------------------------------------- #
# Backstage
# --------------------------------------------------------------------------- #


class BackstageSpecialist:
    """Read the Backstage service catalog for ownership and on-call info."""

    name = "backstage"
    tools: tuple[str, ...] = ("backstage_get_entity",)

    def gather(
        self,
        *,
        catalog: dict[str, dict[str, Any]],
        invoke: InvokeFn,
        budget: Budget,
        used: BudgetTracker,
        scope: Scope,
    ) -> SpecialistResult:
        del budget, catalog
        del scope  # only the kubernetes specialist aims by namespace so far
        calls: list[ToolCall] = [
            _safe_call("backstage_get_entity", {"kind": "Component", "name": "payment-api"}, invoke, used),
        ]
        for call in calls:
            if call.summary is None and call.result is not None:
                call.summary = _summarize(call.tool, call.result)
        return SpecialistResult(specialist=self.name, calls=calls)
