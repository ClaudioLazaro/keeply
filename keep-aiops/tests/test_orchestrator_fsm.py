"""FSM happy path: queued -> gathering -> rca_ready with mocked backends."""

import json

from tests.conftest import TENANT_ID, make_event, post_event


def test_happy_path_reaches_rca_ready_with_evidence(client, mocked_backends):
    event = make_event("incident.created")
    response = post_event(client, event)
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "event_id": event["id"]}

    # Background task ran synchronously under TestClient: FSM completed.
    investigations = client.get("/v1/investigations", params={"incident_id": event["subject"]}).json()
    assert len(investigations) == 1
    investigation = investigations[0]
    assert investigation["tenant_id"] == TENANT_ID
    assert investigation["status"] == "rca_ready"
    assert investigation["mode"] == "suggest"
    assert investigation["error"] is None

    draft = investigation["rca_draft"]
    assert "suggest-only" in draft
    assert "no actions taken" in draft
    assert event["subject"] in draft

    evidence = client.get(f"/v1/investigations/{investigation['id']}/evidence").json()
    assert len(evidence) == 3
    assert [item["tool"] for item in evidence] == ["get_pods", "get_events", "get_logs"]
    assert all("evidence gap" not in item["summary"] for item in evidence)

    # get_logs was called with the pod name discovered via get_pods.
    logs_call = mocked_backends.get_logs_route.calls.last
    logs_body = json.loads(logs_call.request.content)
    assert logs_body["arguments"]["pod"] == "payment-api-7d9f"
    assert logs_body["investigation_id"] == investigation["id"]
    assert logs_body["tenant_id"] == TENANT_ID


def test_writeback_comment_and_enrichment(client, mocked_backends):
    event = make_event("incident.created")
    assert post_event(client, event).status_code == 202

    incident_id = event["subject"]
    comment_route = mocked_backends.comment_route
    enrich_route = mocked_backends.enrich_route
    assert comment_route.called
    assert enrich_route.called

    comment_request = comment_route.calls.last.request
    assert comment_request.headers["X-API-KEY"] == "test-api-key"
    assert f"/incidents/{incident_id}/comment" in str(comment_request.url)
    comment_body = json.loads(comment_request.content)
    assert comment_body["status"] == "firing"
    assert "RCA draft" in comment_body["comment"]
    assert "suggest-only" in comment_body["comment"]
    assert "get_pods" in comment_body["comment"]

    enrich_request = enrich_route.calls.last.request
    assert f"/incidents/{incident_id}/enrich" in str(enrich_request.url)
    enrichments = json.loads(enrich_request.content)["enrichments"]
    assert enrichments["aiops.status"] == "rca_ready"
    investigation = client.get("/v1/investigations", params={"incident_id": incident_id}).json()[0]
    assert enrichments["aiops.investigation_id"] == investigation["id"]


def test_idempotent_creation_reuses_investigation(client, mocked_backends):
    incident_id = make_event()["subject"]
    first = make_event("incident.created", incident_id=incident_id)
    second = make_event("incident.created", incident_id=incident_id)

    assert post_event(client, first).status_code == 202
    assert post_event(client, second).status_code == 202

    investigations = client.get("/v1/investigations", params={"incident_id": incident_id}).json()
    assert len(investigations) == 1

    # The duplicate incident.created did not re-run the FSM: exactly one
    # comment writeback happened.
    assert mocked_backends.comment_route.call_count == 1


def test_duplicate_event_id_is_deduped(client, mocked_backends):
    event = make_event("incident.created")
    assert post_event(client, event).status_code == 202
    second = post_event(client, event)
    assert second.status_code == 202
    assert second.json()["duplicate"] is True

    investigations = client.get("/v1/investigations").json()
    assert len(investigations) == 1


def test_low_severity_is_not_investigated(client, mocked_backends):
    event = make_event("incident.created", severity="info")
    assert post_event(client, event).status_code == 202
    assert client.get("/v1/investigations").json() == []


def test_incident_updated_is_noop_and_resolved_marks_flag(client, mocked_backends):
    created = make_event("incident.created")
    assert post_event(client, created).status_code == 202
    investigation = client.get("/v1/investigations").json()[0]

    updated = make_event("incident.updated", incident_id=created["subject"], status="acknowledged")
    assert post_event(client, updated).status_code == 202
    assert client.get(f"/v1/investigations/{investigation['id']}").json()["status"] == "rca_ready"

    resolved = make_event("incident.resolved", incident_id=created["subject"], status="resolved")
    assert post_event(client, resolved).status_code == 202
    after = client.get(f"/v1/investigations/{investigation['id']}").json()
    # Documented choice: status stays rca_ready; incident_resolved flag set.
    assert after["status"] == "rca_ready"
    assert after["incident_resolved"] is True
