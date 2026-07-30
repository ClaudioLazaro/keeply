"""Event bridge: HMAC verification and envelope validation."""

import json

from tests.conftest import make_event, post_event, sign


def test_bad_signature_rejected(client):
    event = make_event("incident.created")
    body = json.dumps(event).encode()
    response = client.post(
        "/v1/events/keep",
        content=body,
        headers={
            "Content-Type": "application/cloudevents+json",
            "X-Keep-Signature": sign(body, secret="wrong-secret"),
        },
    )
    assert response.status_code == 401


def test_missing_signature_rejected(client):
    body = json.dumps(make_event("incident.created")).encode()
    response = client.post("/v1/events/keep", content=body)
    assert response.status_code == 401


def test_tampered_body_rejected(client):
    event = make_event("incident.created")
    body = json.dumps(event).encode()
    signature = sign(body)
    tampered = json.dumps({**event, "data": {**event["data"], "severity": "low"}}).encode()
    response = client.post(
        "/v1/events/keep",
        content=tampered,
        headers={"X-Keep-Signature": signature},
    )
    assert response.status_code == 401


def test_invalid_envelope_rejected(client):
    body = json.dumps({"specversion": "1.0", "id": "x"}).encode()
    response = client.post(
        "/v1/events/keep",
        content=body,
        headers={"X-Keep-Signature": sign(body)},
    )
    assert response.status_code == 422
