"""Output models for the Kubernetes MCP server.

Every result type carries two fields with **no default**: ``backend`` and
``cluster``. That is the whole point of this module.

MCP derives a tool's ``outputSchema`` from its return type and requires the
server to conform, so a field without a default lands in
``outputSchema.required``. Provenance stops being a convention we remember to
apply and becomes something the protocol refuses to let us omit.

``backend`` answers "is this real?" — the question the previous gateway
already asked. ``cluster`` answers "real about *what*?", which it could not
ask at all: the old tools resolved their target implicitly from wherever the
gateway pod happened to run, so evidence from an unrelated cluster arrived
labelled ``live`` and was trusted accordingly. A result that cannot say which
cluster answered is not auditable evidence.
"""

from typing import Literal

from pydantic import BaseModel, Field

# live = the tool reached a real cluster
# stub = a canned demo payload, indistinguishable from live once it is a dict
# gap  = the call failed; the absence is itself evidence and must be visible
Provenance = Literal["live", "stub", "gap"]


class ClusterScoped(BaseModel):
    """Fields every result carries so a reader can judge what it is worth."""

    backend: Provenance = Field(
        description="Provenance of this payload: live (real cluster), stub (demo data), gap (call failed)."
    )
    cluster: str = Field(
        description="Name of the cluster that answered, as registered in the server's cluster registry."
    )
    error: str | None = Field(
        default=None,
        description="Why the call failed. Set only when backend is 'gap'.",
    )


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str | None = None
    ready: bool = False
    restarts: int = 0
    container: str | None = None
    node: str | None = None
    reason: str | None = Field(
        default=None, description="Waiting/terminated reason, e.g. CrashLoopBackOff or OOMKilled."
    )


class PodsResult(ClusterScoped):
    namespace: str = Field(description="Namespace queried, or 'all'.")
    pods: list[PodSummary] = Field(default_factory=list)


class EventSummary(BaseModel):
    type: str | None = None
    reason: str | None = None
    namespace: str | None = None
    object: str | None = None
    message: str | None = None
    count: int = 0
    last_timestamp: str | None = None


class EventsResult(ClusterScoped):
    namespace: str = Field(description="Namespace queried, or 'all'.")
    events: list[EventSummary] = Field(default_factory=list)


class LogsResult(ClusterScoped):
    pod: str
    namespace: str
    lines: list[str] = Field(default_factory=list)


class NamespacesResult(ClusterScoped):
    namespaces: list[str] = Field(default_factory=list)


# How a service was located, strongest evidence first. The value is carried
# into the investigation so an operator can read why we looked where we did —
# "a pod named my-service-brasil-aja6sa lives there" is checkable in a way
# that "the model decided" is not.
MatchReason = Literal[
    "namespace_exact",     # a namespace has exactly this name
    "pod_prefix",          # a pod is named <service>-<hash>, the usual k8s shape
    "namespace_contains",  # the namespace name embeds the service, or vice versa
    "pod_contains",        # weakest: the service name appears somewhere in a pod name
]


class WorkloadMatch(BaseModel):
    service: str
    namespace: str
    matched_by: MatchReason = Field(description="Why this namespace was proposed for this service.")
    sample_pod: str | None = Field(
        default=None, description="A pod that produced the match, when one did."
    )


class WorkloadLocationResult(ClusterScoped):
    """Where the named services appear to run.

    ``unresolved`` matters as much as ``matches``: a service nothing could be
    found for must not silently become a cluster-wide query.
    """

    matches: list[WorkloadMatch] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class ClusterInfo(BaseModel):
    name: str
    mode: Literal["live", "stub"]
    description: str | None = None
    reachable: bool | None = Field(
        default=None,
        description="Whether the server could reach this cluster's API on the last check. None = not probed.",
    )


class ClustersResult(BaseModel):
    """Discovery: which targets exist, so a caller never has to guess one.

    Deliberately not ClusterScoped — this describes the registry itself
    rather than an answer from any single cluster.
    """

    clusters: list[ClusterInfo] = Field(default_factory=list)
