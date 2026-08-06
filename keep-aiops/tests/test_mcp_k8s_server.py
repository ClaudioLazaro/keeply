"""The Kubernetes MCP server's two contracts.

1. Provenance is structural: ``backend`` and ``cluster`` are required by the
   tools' ``outputSchema``, so a result cannot omit where it came from or
   whether it is real.
2. The target is explicit: ``cluster`` is a required *input*, so no call can
   silently inherit "whichever cluster this process happens to run in" — the
   defect that let evidence from an unrelated cluster be filed as live.
"""

import asyncio

import pytest

from mcp_servers.k8s import clusters
from mcp_servers.k8s.server import get_events, get_logs, get_pods, list_clusters, mcp


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setenv(
        "MCP_K8S_CLUSTERS",
        '[{"name": "demo", "mode": "stub"}, {"name": "prod", "mode": "live", "context": "nope"}]',
    )
    clusters.reset_registry()
    yield
    clusters.reset_registry()


def _tools():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", ["get_pods", "get_events", "get_logs"])
def test_provenance_is_required_by_the_output_schema(tool):
    """A result that cannot say what it is must not typecheck."""
    required = (_tools()[tool].output_schema or {}).get("required", [])
    assert "backend" in required
    assert "cluster" in required


@pytest.mark.parametrize("tool", ["get_pods", "get_events", "get_logs"])
def test_cluster_is_a_required_argument(tool):
    """No implicit target: the caller has to name the cluster it means."""
    assert "cluster" in (_tools()[tool].input_schema or {}).get("required", [])


def test_backend_enum_admits_only_the_three_known_states():
    schema = _tools()["get_pods"].output_schema or {}
    backend = schema["properties"]["backend"]
    enum = backend.get("enum") or backend.get("const")
    assert set(enum) == {"live", "stub", "gap"}


# --------------------------------------------------------------------------- #
# Behaviour
# --------------------------------------------------------------------------- #


def test_stub_cluster_is_labelled_stub_not_live():
    result = get_pods(cluster="demo")
    assert result.backend == "stub"
    assert result.cluster == "demo"
    assert result.pods, "the demo scenario should still return its pods"


def test_unknown_cluster_is_a_gap_that_names_the_valid_ones():
    result = get_pods(cluster="does-not-exist")
    assert result.backend == "gap"
    # The caller has to be able to fix the call from the error alone.
    assert "demo" in result.error and "prod" in result.error


def test_unreachable_live_cluster_is_a_gap_not_an_empty_list():
    """An empty result and a failed lookup must never look the same.

    'prod' points at a kubeconfig context that does not exist, so loading its
    credentials fails. Returning [] here would report "no pods are running"
    for what is actually "we could not look".
    """
    result = get_pods(cluster="prod")
    assert result.backend == "gap"
    assert result.pods == []
    assert result.error


def test_namespace_filter_scopes_the_answer():
    """Unscoped queries are how evidence about the wrong workload gets in."""
    assert get_pods(cluster="demo", namespace="payments").pods
    assert get_pods(cluster="demo", namespace="somewhere-else").pods == []


def test_events_and_logs_carry_the_same_provenance():
    events = get_events(cluster="demo")
    logs = get_logs(cluster="demo", pod="payment-api-7d9f4b6c5-x2vkm", namespace="payments")
    assert events.backend == "stub" and events.cluster == "demo"
    assert logs.backend == "stub" and logs.cluster == "demo"
    assert logs.lines


def test_list_clusters_lets_a_caller_discover_targets():
    names = {c.name: c.mode for c in list_clusters().clusters}
    assert names == {"demo": "stub", "prod": "live"}


# --------------------------------------------------------------------------- #
# Failure text — it is evidence now, so it has to read like evidence
# --------------------------------------------------------------------------- #


def test_a_kubernetes_api_error_is_reduced_to_its_message():
    """ApiException stringifies to a header dump; only one sentence matters.

    This text reaches the RCA prompt, where several hundred characters of
    HTTP headers would crowd out the findings beside it.
    """
    from mcp_servers.k8s.server import describe_error

    exc = Exception()
    exc.status = 403
    exc.reason = "Forbidden"
    exc.body = (
        '{"kind":"Status","message":"pods \\"x\\" is forbidden: cannot get '
        'resource pods/log","code":403}'
    )
    described = describe_error(exc)
    assert described == '403 Forbidden: pods "x" is forbidden: cannot get resource pods/log'
    assert "HTTPHeaderDict" not in described


def test_an_error_without_an_api_body_still_produces_one_line():
    from mcp_servers.k8s.server import describe_error

    described = describe_error(ValueError("something\nbroke"))
    assert described == "ValueError: something broke"
    assert "\n" not in described
