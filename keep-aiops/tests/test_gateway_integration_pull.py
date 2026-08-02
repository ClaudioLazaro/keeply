"""Gateway-side resolution of integration config pulled from aiops-api.

The property that matters: a control-plane failure must never promote a
backend to `live`. Falling back to `stub` on error is the safe direction —
the opposite would have agents hitting production systems because a config
fetch timed out.
"""

import httpx
import pytest
import respx

from mcp_gateway import integrations
from mcp_gateway.settings import get_settings

AIOPS_URL = "http://aiops.test"


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    monkeypatch.setenv("MCP_AIOPS_API_URL", AIOPS_URL)
    monkeypatch.setenv("MCP_DATADOG_MODE", "stub")
    monkeypatch.setenv("MCP_DATADOG_URL", "https://env-default.example.com")
    get_settings.cache_clear()
    integrations.invalidate()
    yield
    integrations.invalidate()
    get_settings.cache_clear()


def _resolved(payload):
    router = respx.mock(assert_all_called=False)
    router.get(f"{AIOPS_URL}/v1/integrations/resolved").respond(200, json=payload)
    return router


def test_stored_mode_overrides_the_env_default():
    with _resolved({"integrations": {"datadog": {"mode": "live", "values": {}}}}):
        assert integrations.mode("datadog") == "live"


def test_env_default_applies_when_nothing_is_stored():
    with _resolved({"integrations": {}}):
        assert integrations.mode("datadog") == "stub"


def test_stored_value_overrides_the_env_default():
    with _resolved(
        {"integrations": {"datadog": {"mode": "live", "values": {"url": "https://stored"}}}}
    ):
        assert integrations.value("datadog", "url") == "https://stored"


def test_env_value_is_used_when_not_stored():
    with _resolved({"integrations": {"datadog": {"mode": "live", "values": {}}}}):
        assert integrations.value("datadog", "url") == "https://env-default.example.com"


def test_control_plane_failure_falls_back_to_stub_never_live():
    """The safe direction: a fetch error must not turn a backend live."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{AIOPS_URL}/v1/integrations/resolved").mock(
            side_effect=httpx.ConnectError("aiops-api down")
        )
        assert integrations.mode("datadog") == "stub"
        assert integrations.status()["last_error"].startswith("ConnectError")


def test_control_plane_failure_keeps_an_env_configured_live_backend():
    """Env-configured live must survive a control-plane outage — the pull
    is an override layer, not the source of truth."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{AIOPS_URL}/v1/integrations/resolved").mock(
            side_effect=httpx.ConnectError("down")
        )
        get_settings.cache_clear()
        import os

        os.environ["MCP_K8S_MODE"] = "live"
        get_settings.cache_clear()
        try:
            assert integrations.mode("k8s") == "live"
        finally:
            os.environ["MCP_K8S_MODE"] = "stub"
            get_settings.cache_clear()


def test_pull_is_cached_between_calls():
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{AIOPS_URL}/v1/integrations/resolved").respond(
            200, json={"integrations": {"datadog": {"mode": "live", "values": {}}}}
        )
        integrations.mode("datadog")
        integrations.mode("datadog")
        integrations.value("datadog", "url")
        assert route.call_count == 1


def test_a_failing_pull_is_also_cached():
    """Otherwise every tool call pays the HTTP timeout while the control
    plane is down."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{AIOPS_URL}/v1/integrations/resolved").mock(
            side_effect=httpx.ConnectError("down")
        )
        integrations.mode("datadog")
        integrations.mode("datadog")
        integrations.value("datadog", "url")
        assert route.call_count == 1


def test_disabled_pull_uses_env_only(monkeypatch):
    monkeypatch.setenv("MCP_AIOPS_API_URL", "")
    get_settings.cache_clear()
    integrations.invalidate()

    # No HTTP mock at all: a request would raise, proving none is made.
    assert integrations.mode("datadog") == "stub"


def test_catalog_mode_matches_what_an_invocation_would_do():
    """The catalog must never advertise a mode the tool would not use."""
    from mcp_gateway.tools import catalog

    with _resolved({"integrations": {"datadog": {"mode": "live", "values": {}}}}):
        entries = {tool["name"]: tool["mode"] for tool in catalog()}
        assert entries["dd_query_metrics"] == "live"
        assert entries["dd_list_events"] == "live"
        assert entries["get_pods"] == "stub"
