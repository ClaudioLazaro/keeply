"""Integrations derive from Keep's provider system, not a parallel store.

The property under test: the AI plane keeps no second credential store.
Installing a provider in Keep is what turns a specialist real, and there
is no second switch or second key form to forget.
"""

import httpx
import respx

from tests.conftest import KEEP_API_URL, MCP_GATEWAY_URL


def _providers(*installed: dict) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.get(f"{KEEP_API_URL}/providers").respond(
        200, json={"installed_providers": list(installed), "providers": []}
    )
    return router


def datadog_provider(**auth) -> dict:
    return {
        "id": "prov-dd",
        "type": "datadog",
        "display_name": "Datadog",
        "details": {"authentication": {"api_key": "dd-key", "app_key": "dd-app", **auth}},
    }


def _clear_provider_cache():
    from keep_client.providers import invalidate

    invalidate()


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


def test_lists_every_integration(client):
    _clear_provider_cache()
    with _providers():
        body = client.get("/v1/integrations").json()

    assert {item["name"] for item in body} == {
        "k8s", "prometheus", "datadog", "eks", "rds",
        "argocd", "jira", "slack", "bitbucket", "backstage",
    }


def test_shows_no_provider_when_none_is_installed(client):
    _clear_provider_cache()
    with _providers():
        body = {item["name"]: item for item in client.get("/v1/integrations").json()}

    assert body["datadog"]["provider"] is None


def test_links_an_installed_keep_provider_to_its_integration(client):
    _clear_provider_cache()
    with _providers(datadog_provider()):
        body = {item["name"]: item for item in client.get("/v1/integrations").json()}

    assert body["datadog"]["provider"]["type"] == "datadog"
    assert body["datadog"]["provider"]["display_name"] == "Datadog"
    assert body["datadog"]["provider"]["id"] == "prov-dd"


def test_offers_the_keep_provider_types_that_can_back_an_integration(client):
    """So the page can link to the right install flow instead of a form."""
    _clear_provider_cache()
    with _providers():
        body = {item["name"]: item for item in client.get("/v1/integrations").json()}

    assert body["jira"]["provider_types"] == ["jira", "jiraonprem"]
    assert body["datadog"]["provider_types"] == ["datadog"]


def test_no_secret_is_returned_by_the_listing(client):
    _clear_provider_cache()
    with _providers(datadog_provider()):
        body = client.get("/v1/integrations").text

    assert "dd-key" not in body
    assert "dd-app" not in body


def test_reported_mode_is_the_gateway_truth(client):
    """Reporting a stored value could show a live backend as stub."""
    _clear_provider_cache()
    with _providers() as router:
        router.get(f"{MCP_GATEWAY_URL}/v1/mcp/tools").respond(
            200,
            json=[
                {"name": "get_pods", "description": "", "execution_class": "read",
                 "input_schema": {}, "mode": "live"},
                {"name": "dd_list_events", "description": "", "execution_class": "read",
                 "input_schema": {}, "mode": "stub"},
            ],
        )
        body = {item["name"]: item for item in client.get("/v1/integrations").json()}

    assert body["k8s"]["mode"] == "live"
    assert body["datadog"]["mode"] == "stub"


def test_listing_survives_keep_being_unreachable(client):
    _clear_provider_cache()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/providers").mock(side_effect=httpx.ConnectError("down"))
        body = client.get("/v1/integrations").json()

    assert len(body) == 10
    assert all(item["provider"] is None for item in body)


# --------------------------------------------------------------------------- #
# Gateway pull
# --------------------------------------------------------------------------- #


def test_installed_provider_makes_the_integration_live(client):
    """Installing Datadog in Keep is the switch — there is no second one."""
    _clear_provider_cache()
    with _providers(datadog_provider()):
        resolved = client.get("/v1/integrations/resolved").json()["integrations"]

    assert resolved["datadog"]["mode"] == "live"


def test_credentials_are_mapped_from_the_keep_provider(client):
    _clear_provider_cache()
    with _providers(datadog_provider(domain="https://api.datadoghq.com")):
        resolved = client.get("/v1/integrations/resolved").json()["integrations"]

    values = resolved["datadog"]["values"]
    assert values["api_key"] == "dd-key"
    assert values["app_key"] == "dd-app"
    assert values["url"] == "https://api.datadoghq.com"


def test_uninstalled_integrations_are_absent_so_the_gateway_stays_on_env(client):
    """Sending stub for everything would override an operator's env config."""
    _clear_provider_cache()
    with _providers(datadog_provider()):
        resolved = client.get("/v1/integrations/resolved").json()["integrations"]

    assert set(resolved) == {"datadog"}


def test_keep_outage_yields_no_overrides_never_a_false_live(client):
    _clear_provider_cache()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/providers").mock(side_effect=httpx.ConnectError("down"))
        resolved = client.get("/v1/integrations/resolved").json()["integrations"]

    assert resolved == {}
