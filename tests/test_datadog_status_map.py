"""Datadog transition vocabularies — both of them.

A real account's history came back with every alert marked firing,
including ~40% that were recovery notifications. The pull path reads
`monitor.transition.destination_state` ("Alert"/"Warn"/"OK"/"No Data")
while the map only knew the webhook's `alert_transition` vocabulary
("Triggered"/"Recovered"). Nothing overlapped, so every lookup fell through
to the FIRING default and the default looked like it was working.
"""

import pytest

from keep.api.models.alert import AlertStatus
from keep.providers.datadog_provider.datadog_provider import DatadogProvider


@pytest.mark.parametrize(
    "value,expected",
    [
        # Monitor-state vocabulary — the one that was missing entirely.
        ("Alert", AlertStatus.FIRING),
        ("Warn", AlertStatus.FIRING),
        ("OK", AlertStatus.RESOLVED),
        ("No Data", AlertStatus.PENDING),
        # Webhook vocabulary — must keep working.
        ("Triggered", AlertStatus.FIRING),
        ("Re-Triggered", AlertStatus.FIRING),
        ("Recovered", AlertStatus.RESOLVED),
        ("Muted", AlertStatus.SUPPRESSED),
    ],
)
def test_both_vocabularies_map(value, expected):
    assert DatadogProvider._map_status(value) is expected


@pytest.mark.parametrize("value", ["ok", "OK", "Ok", "oK"])
def test_casing_cannot_reinstate_the_bug(value):
    # The original failure was a silent lookup miss. A vendor changing
    # capitalisation would reproduce it exactly.
    assert DatadogProvider._map_status(value) is AlertStatus.RESOLVED


def test_a_recovery_is_never_stored_as_active():
    # The property that actually matters: correlating a resolved alert with
    # a firing one groups a fixed problem into a live incident.
    for value in ("OK", "ok", "Recovered", "recovered"):
        assert DatadogProvider._map_status(value) is not AlertStatus.FIRING


def test_an_unknown_state_still_defaults_to_firing():
    # Firing is the safe default for something we do not recognise: a
    # missed alert is worse than a noisy one.
    assert DatadogProvider._map_status("brand new state") is AlertStatus.FIRING
    assert DatadogProvider._map_status(None) is AlertStatus.FIRING
    assert DatadogProvider._map_status("") is AlertStatus.FIRING
