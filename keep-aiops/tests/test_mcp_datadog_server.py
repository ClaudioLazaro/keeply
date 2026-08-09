"""The Datadog server's contracts, and the second shape of Scope.

Built second on purpose. Kubernetes scopes by cluster and namespace; Datadog
scopes by service, environment and window. If both fit the same target and
provenance contract, the abstraction generalises.
"""

import asyncio

import pytest

from mcp_servers.datadog import targets
from mcp_servers.datadog.server import (
    get_monitors,
    get_trace,
    list_targets,
    mcp,
    search_logs,
)


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setenv(
        "MCP_DATADOG_TARGETS",
        '[{"name":"demo","mode":"stub"},{"name":"prod","mode":"live","site":"datadoghq.eu"}]',
    )
    targets.reset_registry()
    yield
    targets.reset_registry()


def _tools():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


@pytest.mark.parametrize("tool", ["get_monitors", "query_metrics", "get_trace", "search_logs"])
def test_provenance_is_required_by_the_output_schema(tool):
    required = (_tools()[tool].output_schema or {}).get("required", [])
    assert "backend" in required
    assert "target" in required


@pytest.mark.parametrize("tool", ["get_monitors", "query_metrics", "get_trace", "search_logs"])
def test_the_target_is_a_required_argument(tool):
    """No ambient account. The same rule that removed the implicit cluster."""
    assert "target" in (_tools()[tool].input_schema or {}).get("required", [])


def test_stub_data_is_labelled_stub():
    result = get_monitors(target="demo")
    assert result.backend == "stub"
    assert result.monitors


def test_an_unknown_target_is_a_gap_naming_the_valid_ones():
    result = get_monitors(target="nope")
    assert result.backend == "gap"
    assert "demo" in result.error and "prod" in result.error


def test_a_live_target_without_credentials_is_a_gap_not_an_empty_result():
    """Absent keys must not read as "this account has no monitors"."""
    result = get_monitors(target="prod")
    assert result.backend == "gap"
    assert result.monitors == []
    assert "provider in Keep" in result.error


def test_the_trace_names_the_failing_hop():
    """The reason this server exists: Kubernetes reports the pod Running."""
    result = get_trace(target="demo", trace_id="anything")
    assert result.failing_service == "identity-svc"
    assert any(s.error for s in result.spans)


def test_logs_carry_the_trace_id_that_links_them_to_the_failure():
    lines = search_logs(target="demo", query="x").lines
    assert lines
    assert all(line.trace_id for line in lines)


def test_targets_are_discoverable_so_a_caller_never_guesses():
    modes = {t.name: t.mode for t in list_targets().targets}
    assert modes == {"demo": "stub", "prod": "live"}
