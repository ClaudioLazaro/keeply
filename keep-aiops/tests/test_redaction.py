"""Removing credentials without removing the finding.

Two failure modes, and they are not symmetric. Missing a secret leaks it.
Over-redacting deletes the evidence an investigation exists to surface — and a
redacted RCA looks exactly as confident as a complete one, which is the same
trap as stub evidence rendering like live telemetry.
"""

import pytest

from mcp_servers.redaction import redact, redact_lines


@pytest.mark.parametrize(
    "text,label",
    [
        ("Authorization: Bearer sk-abc123def456ghi789jkl", "bearer-token"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p", "jwt"),
        ("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
        ("conn=postgres://app:hunter2secret@db:5432/prod", "url-credentials"),
        ('api_key: "tok_live_9f8e7d6c5b4a"', "assigned-secret"),
        ("password=correcthorsebattery", "assigned-secret"),
        # Bare `token` is the commonest form and was missed for exactly the
        # reason this list exists: the tests only covered what was written.
        ("token=abc123secretvalue", "assigned-secret"),
        ('TOKEN: "ghp_9f8e7d6c5b4a3210"', "assigned-secret"),
        ("passphrase=hunter2horsebattery", "assigned-secret"),
        ("credential = s3cr3tvalue", "assigned-secret"),
    ],
)
def test_credential_shapes_are_removed(text, label):
    result = redact(text)
    assert result.changed, f"{label} passed through"
    assert label in result.removed


def test_the_identifier_survives_so_the_line_still_reads():
    """A log line stripped to nothing is as useless as one that leaked."""
    result = redact("conn=postgres://app:hunter2secret@db:5432/prod")
    assert "postgres://app:" in result.text
    assert "db:5432/prod" in result.text
    assert "hunter2secret" not in result.text


def test_removal_is_marked_so_the_reader_knows_something_stood_there():
    assert "[redacted: aws-access-key]" in redact("key=AKIAIOSFODNN7EXAMPLE").text


@pytest.mark.parametrize(
    "line",
    [
        "starting settlement batch id=b-5512 size=4096",
        "java.lang.OutOfMemoryError: Java heap space",
        "Back-off restarting failed container payment-api",
        "Readiness probe failed: Get http://10.244.1.37:8080/healthz",
        "trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7",
        "duration=1523ms status=503 upstream=identity-svc",
        # `token` as part of a name, not an assignment — a looser pattern
        # would delete the service that failed.
        "upstream timeout after 3000ms calling token-store",
        "tokens_used=1523 prompt_tokens=980",
    ],
)
def test_diagnostic_lines_are_left_alone(line):
    """These are the findings. Removing them would be the worse failure."""
    assert redact(line).text == line
    assert not redact(line).changed


def test_a_trace_id_is_not_mistaken_for_a_secret():
    """High-entropy hex is the correlation key this product depends on.

    A loose "looks random, redact it" rule would delete exactly the identifier
    that links an incident to the call that failed.
    """
    line = "GET /orders trace_id=4bf92f3577b34da6a3ce929d0e0e4736"
    assert redact(line).text == line


def test_line_order_and_count_survive_redaction():
    lines = ["one", "password=supersecret", "three"]
    out, removed = redact_lines(lines)
    assert len(out) == 3
    assert out[0] == "one" and out[2] == "three"
    assert removed


def test_empty_input_is_not_an_error():
    assert redact("").text == ""
    assert redact_lines([]) == ([], ())
