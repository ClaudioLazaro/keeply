"""keep_client: auth header, DTO parsing, typed errors on non-2xx."""

import json

import pytest
import respx

from keep_client import KeepApiError, KeepClient, KeepNotFoundError

BASE = "http://keep.test"


@pytest.fixture()
def keep():
    return KeepClient(base_url=BASE, api_key="secret-key")


def test_get_incident_parses_and_sends_api_key(keep):
    with respx.mock:
        route = respx.get(f"{BASE}/incidents/abc").respond(
            200,
            json={
                "id": "abc",
                "status": "acknowledged",
                "severity": "high",
                "user_generated_name": "Disk full",
                "alerts_count": 5,
                "some_future_field": {"nested": True},
            },
        )
        incident = keep.get_incident("abc")
    assert route.calls.last.request.headers["X-API-KEY"] == "secret-key"
    assert incident.id == "abc"
    assert incident.status == "acknowledged"
    assert incident.alerts_count == 5


def test_get_incident_alerts(keep):
    with respx.mock:
        respx.get(f"{BASE}/incidents/abc/alerts").respond(
            200,
            json={"items": [{"id": "a1", "name": "cpu", "severity": "critical"}], "count": 1, "limit": 25, "offset": 0},
        )
        page = keep.get_incident_alerts("abc")
    assert page.count == 1
    assert page.items[0].name == "cpu"


def test_add_comment_uses_incident_status_when_not_given(keep):
    with respx.mock:
        respx.get(f"{BASE}/incidents/abc").respond(200, json={"id": "abc", "status": "acknowledged"})
        comment_route = respx.post(f"{BASE}/incidents/abc/comment").respond(200, json={"id": 1})
        keep.add_comment("abc", "hello")
    body = json.loads(comment_route.calls.last.request.content)
    assert body == {"status": "acknowledged", "comment": "hello", "tagged_users": []}


def test_add_comment_skips_fetch_when_status_given(keep):
    with respx.mock:
        get_route = respx.get(f"{BASE}/incidents/abc").respond(200, json={"id": "abc"})
        respx.post(f"{BASE}/incidents/abc/comment").respond(200, json={"id": 1})
        keep.add_comment("abc", "hello", status="firing")
    assert not get_route.called


def test_enrich_incident_body(keep):
    with respx.mock:
        route = respx.post(f"{BASE}/incidents/abc/enrich").respond(202, json={})
        keep.enrich_incident("abc", {"aiops.status": "rca_ready"})
    body = json.loads(route.calls.last.request.content)
    assert body == {"enrichments": {"aiops.status": "rca_ready"}, "force": False}


def test_404_raises_not_found(keep):
    with respx.mock:
        respx.get(f"{BASE}/incidents/nope").respond(404, json={"detail": "Incident not found"})
        with pytest.raises(KeepNotFoundError) as exc_info:
            keep.get_incident("nope")
    assert exc_info.value.status_code == 404


def test_500_raises_api_error(keep):
    with respx.mock:
        respx.post(f"{BASE}/incidents/abc/enrich").respond(500, text="boom")
        with pytest.raises(KeepApiError) as exc_info:
            keep.enrich_incident("abc", {"k": "v"})
    assert exc_info.value.status_code == 500
