"""Schemas must parse the canonical contract examples verbatim."""

import json
from pathlib import Path

import pytest

from aiops_api.modules.event_bridge.schemas import EventType, KeepEventEnvelope

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "docs" / "aiops" / "contracts" / "examples"


@pytest.mark.parametrize(
    "filename,expected_type",
    [
        ("incident.created.json", EventType.INCIDENT_CREATED),
        ("incident.updated.json", EventType.INCIDENT_UPDATED),
        ("incident.resolved.json", EventType.INCIDENT_RESOLVED),
    ],
)
def test_contract_examples_parse(filename, expected_type):
    payload = json.loads((EXAMPLES_DIR / filename).read_text())
    envelope = KeepEventEnvelope.model_validate(payload)
    assert envelope.type is expected_type
    assert envelope.specversion == "1.0"
    assert envelope.data.incident_id == envelope.subject
