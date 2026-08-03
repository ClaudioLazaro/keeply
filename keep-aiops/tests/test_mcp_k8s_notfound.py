"""A missing resource is a finding, not an outage.

The RCA reports whatever the gateway says. Collapsing "pod not found" into
503 made every investigation of a service that does not run in this cluster
read as though the tooling were broken, sending operators to check a
healthy gateway.
"""

import pytest

from mcp_gateway.tools.backend import BackendUnavailable, ResourceNotFound
from mcp_gateway.tools.k8s import KubernetesBackendUnavailable, _is_not_found, _live_get_logs


class _ApiError(Exception):
    def __init__(self, status):
        self.status = status


def test_a_404_from_kubernetes_is_recognised():
    assert _is_not_found(_ApiError(404)) is True


def test_other_failures_are_not_mistaken_for_absence():
    assert _is_not_found(_ApiError(500)) is False
    assert _is_not_found(_ApiError(403)) is False
    assert _is_not_found(RuntimeError("connection refused")) is False


def test_a_missing_pod_raises_not_found(monkeypatch):
    from mcp_gateway.tools import k8s

    class _Api:
        @staticmethod
        def read_namespaced_pod_log(**kwargs):
            raise _ApiError(404)

    monkeypatch.setattr(k8s, "_live_core_v1", lambda: _Api())

    with pytest.raises(ResourceNotFound) as exc:
        _live_get_logs("ledger-api-xyz", "keeply", 100)

    assert "ledger-api-xyz" in str(exc.value)


def test_a_real_outage_still_raises_unavailable(monkeypatch):
    from mcp_gateway.tools import k8s

    class _Api:
        @staticmethod
        def read_namespaced_pod_log(**kwargs):
            raise _ApiError(500)

    monkeypatch.setattr(k8s, "_live_core_v1", lambda: _Api())

    with pytest.raises(KubernetesBackendUnavailable):
        _live_get_logs("some-pod", "keeply", 100)


def test_not_found_is_not_a_backend_unavailable():
    """They map to different HTTP codes, so they must not be subclasses."""
    assert not issubclass(ResourceNotFound, BackendUnavailable)
