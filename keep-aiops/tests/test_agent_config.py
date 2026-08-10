"""Agent configuration: persisted overrides, secret hygiene, runtime effect."""

import pytest

from aiops_api.modules.config import get_effective_config, invalidate_cache
from tests.conftest import make_event, post_event


@pytest.fixture(autouse=True)
def _clear_config_cache():
    invalidate_cache()
    yield
    invalidate_cache()


# --------------------------------------------------------------------------- #
# Defaults + round trip
# --------------------------------------------------------------------------- #


def test_config_defaults_to_env_when_nothing_is_stored(client):
    body = client.get("/v1/config").json()

    assert body["budget_max_tool_calls"] == 50
    assert body["budget_max_wall_time_seconds"] == 120.0
    assert body["budget_max_llm_tokens"] == 200_000
    assert sorted(body["auto_investigate_severities"]) == ["critical", "high"]
    assert body["llm_model"] is None
    assert body["llm_enabled"] is False
    assert body["disabled_specialists"] == []
    assert len(body["available_specialists"]) == 10


def test_put_persists_and_is_readable(client):
    client.put("/v1/config", json={"budget_max_tool_calls": 7})

    assert client.get("/v1/config").json()["budget_max_tool_calls"] == 7


def test_put_is_partial_and_leaves_other_fields_alone(client):
    client.put("/v1/config", json={"budget_max_tool_calls": 7})
    client.put("/v1/config", json={"llm_provider": "anthropic"})

    body = client.get("/v1/config").json()
    assert body["budget_max_tool_calls"] == 7
    assert body["llm_provider"] == "anthropic"


def test_explicit_null_resets_a_field_to_the_env_default(client):
    client.put("/v1/config", json={"budget_max_tool_calls": 7})
    client.put("/v1/config", json={"budget_max_tool_calls": None})

    assert client.get("/v1/config").json()["budget_max_tool_calls"] == 50


# --------------------------------------------------------------------------- #
# Secret hygiene — the whole point of the env-reference design
# --------------------------------------------------------------------------- #


def test_raw_api_key_is_rejected_not_stored(client):
    response = client.put(
        "/v1/config", json={"llm_api_key_env": "sk-ant-api03-realkeyshapedstring"}
    )

    assert response.status_code == 422
    assert "never the key itself" in response.text


@pytest.mark.parametrize(
    "value", ["xoxb-123", "ghp_abc", "AKIAIOSFODNN7EXAMPLE", "Bearer abc", "not-an-env-var"]
)
def test_only_env_var_names_are_accepted(client, value):
    assert client.put("/v1/config", json={"llm_api_key_env": value}).status_code == 422


def test_env_var_name_is_accepted(client):
    response = client.put("/v1/config", json={"llm_api_key_env": "ANTHROPIC_API_KEY"})

    assert response.status_code == 200
    assert response.json()["llm_api_key"]["env_var"] == "ANTHROPIC_API_KEY"


def test_response_reports_key_presence_without_revealing_it(client, monkeypatch):
    client.put("/v1/config", json={"llm_api_key_env": "MY_TEST_KEY"})

    assert client.get("/v1/config").json()["llm_api_key"]["present"] is False

    monkeypatch.setenv("MY_TEST_KEY", "super-secret-value")
    invalidate_cache()
    body = client.get("/v1/config").json()

    assert body["llm_api_key"]["present"] is True
    assert "super-secret-value" not in client.get("/v1/config").text


def test_llm_is_only_enabled_when_model_and_key_both_resolve(client, monkeypatch):
    client.put(
        "/v1/config", json={"llm_model": "anthropic/claude-sonnet-4-5", "llm_api_key_env": "MY_TEST_KEY"}
    )
    # Model set but credential missing -> still disabled, no silent failure.
    assert client.get("/v1/config").json()["llm_enabled"] is False

    monkeypatch.setenv("MY_TEST_KEY", "k")
    invalidate_cache()
    assert client.get("/v1/config").json()["llm_enabled"] is True


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_unknown_severity_is_rejected(client):
    response = client.put("/v1/config", json={"auto_investigate_severities": ["critical", "bogus"]})

    assert response.status_code == 422
    assert "unknown severities" in response.text


def test_unknown_specialist_is_rejected(client):
    response = client.put("/v1/config", json={"disabled_specialists": ["kubernetes", "nope"]})

    assert response.status_code == 422
    assert "unknown specialists" in response.text


def test_budget_bounds_are_enforced(client):
    assert client.put("/v1/config", json={"budget_max_tool_calls": 0}).status_code == 422
    assert client.put("/v1/config", json={"budget_max_wall_time_seconds": 99999}).status_code == 422


# --------------------------------------------------------------------------- #
# Runtime effect — config that does not change behaviour is just a table
# --------------------------------------------------------------------------- #


def test_severity_config_gates_investigation_creation(client, mocked_backends):
    """Narrowing severities must actually stop an investigation being made."""
    client.put("/v1/config", json={"auto_investigate_severities": ["critical"]})

    post_event(client, make_event(severity="high"))
    assert client.get("/v1/investigations").json() == []

    post_event(client, make_event(severity="critical"))
    assert len(client.get("/v1/investigations").json()) == 1


def test_budget_config_is_enforced_on_a_real_investigation(client, mocked_backends):
    client.put("/v1/config", json={"budget_max_tool_calls": 1})

    post_event(client, make_event())

    investigation = client.get("/v1/investigations").json()[0]
    assert investigation["status"] == "failed"
    assert "BudgetExceeded(tool_calls)" in investigation["error"]


def test_disabled_specialist_does_not_run(client, mocked_backends):
    """Cutting a specialist must remove its evidence, not just hide it."""
    client.put("/v1/config", json={"disabled_specialists": ["kubernetes"]})

    post_event(client, make_event())

    investigation_id = client.get("/v1/investigations").json()[0]["id"]
    evidence = client.get(f"/v1/investigations/{investigation_id}/evidence").json()
    assert evidence == []


def test_effective_config_falls_back_to_env_when_table_is_missing(client, monkeypatch):
    """Pending migrations must not take the investigation path down."""
    import aiops_api.modules.config.service as service

    monkeypatch.setattr(
        service, "_row_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table"))
    )
    invalidate_cache()

    config = get_effective_config("t1")

    assert config.budget_max_tool_calls == 50


# --------------------------------------------------------------------------- #
# Provider catalogue for the settings UI
# --------------------------------------------------------------------------- #


def test_provider_list_reflects_what_is_installed_in_keep(client):
    """Not a hardcoded catalog: Keep's provider system decides what exists
    and what is configured, so there is one place to install and rotate."""
    import respx

    from tests.conftest import KEEP_API_URL
    from keep_client.providers import invalidate

    invalidate()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/providers").respond(
            200,
            json={
                "installed_providers": [
                    {
                        "id": "prov-ds",
                        "type": "deepseek",
                        "display_name": "DeepSeek",
                        "details": {"authentication": {"api_key": "k"}},
                    },
                    # Non-AI providers must not be offered for LLM routing.
                    {
                        "id": "prov-dd",
                        "type": "datadog",
                        "display_name": "Datadog",
                        "details": {"authentication": {"api_key": "k"}},
                    },
                ],
                "providers": [],
            },
        )
        body = client.get("/v1/config/llm-providers").json()
    invalidate()

    assert [p["type"] for p in body["providers"]] == ["deepseek"]
    assert body["providers"][0]["configured"] is True
    assert body["install_url"] == "/providers"


def test_provider_list_is_empty_when_nothing_is_installed(client):
    """The UI then points at /providers instead of asking for a key."""
    import respx

    from tests.conftest import KEEP_API_URL
    from keep_client.providers import invalidate

    invalidate()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{KEEP_API_URL}/providers").respond(
            200, json={"installed_providers": [], "providers": []}
        )
        body = client.get("/v1/config/llm-providers").json()
    invalidate()

    assert body["providers"] == []


# --------------------------------------------------------------------------- #
# The rejection itself must not leak the value
# --------------------------------------------------------------------------- #


def test_rejected_key_is_not_echoed_in_the_error_body(client):
    """FastAPI's default 422 includes `input`, which would hand the pasted
    key back to the caller and into every access log on the way."""
    leaked = "sk-abcdef0123456789abcdef0123456789"

    response = client.put("/v1/config", json={"llm_api_key_env": leaked})

    assert response.status_code == 422
    assert leaked not in response.text
    assert "[redacted]" in response.text


def test_redaction_keeps_ordinary_validation_errors_useful(client):
    """Only credential-shaped fields are redacted."""
    response = client.put("/v1/config", json={"budget_max_tool_calls": 0})

    assert response.status_code == 422
    assert "[redacted]" not in response.text


def test_validation_errors_stay_json_serialisable(client):
    """pydantic puts the raw exception in `ctx`; serialising it naively
    turns a 422 into a 500."""
    response = client.put("/v1/config", json={"auto_investigate_severities": ["bogus"]})

    assert response.status_code == 422
    assert "unknown severities" in response.json()["detail"][0]["msg"]


# --------------------------------------------------------------------------- #
# Per-function assistant routing
# --------------------------------------------------------------------------- #


def test_every_declared_function_is_listed_even_when_unconfigured(client):
    # A feature that routes to an LLM but was never touched is exactly the
    # one an operator needs to find. Building the list from stored keys
    # would hide it.
    body = client.get("/v1/config").json()

    functions = {item["function"] for item in body["assistants"]}
    assert functions == {"workflow_builder", "incident_chat", "ai_summary", "rca"}
    for item in body["assistants"]:
        assert item["purpose"]


def test_unconfigured_function_inherits_the_tenant_default(client):
    client.put("/v1/config", json={"llm_provider": "deepseek", "llm_model": "deepseek-chat"})

    builder = _function(client, "workflow_builder")

    assert builder["provider"] == "deepseek"
    assert builder["model"] == "deepseek-chat"
    # And says so, rather than presenting the fallback as a choice.
    assert builder["inherited"] == ["model", "provider"]


def test_a_function_can_use_a_different_model_from_the_default(client):
    # The point of the whole feature: a cheap model drafting workflows, a
    # strong one writing the RCA.
    client.put("/v1/config", json={"llm_provider": "deepseek", "llm_model": "deepseek-reasoner"})
    client.put(
        "/v1/config",
        json={"assistants": {"workflow_builder": {"model": "deepseek-chat"}}},
    )

    assert _function(client, "workflow_builder")["model"] == "deepseek-chat"
    assert _function(client, "rca")["model"] == "deepseek-reasoner"
    # Provider still falls through — only the model was overridden.
    assert _function(client, "workflow_builder")["provider"] == "deepseek"
    assert "provider" in _function(client, "workflow_builder")["inherited"]


def test_saving_one_function_does_not_wipe_another(client):
    # The settings page saves a card at a time; a wholesale assignment
    # would silently drop everything the form did not include.
    client.put("/v1/config", json={"assistants": {"workflow_builder": {"model": "a"}}})
    client.put("/v1/config", json={"assistants": {"incident_chat": {"model": "b"}}})

    assert _function(client, "workflow_builder")["model"] == "a"
    assert _function(client, "incident_chat")["model"] == "b"


def test_thinking_defaults_to_auto(client):
    assert _function(client, "workflow_builder")["thinking"] == "auto"


def test_unknown_function_is_rejected_rather_than_stored(client):
    # Stored, it would render as a configured feature and route nothing —
    # the operator would believe the builder was pointed somewhere it isn't.
    response = client.put("/v1/config", json={"assistants": {"workflow_bulider": {"model": "x"}}})

    assert response.status_code == 422
    assert "workflow_bulider" in response.text


def test_unknown_thinking_mode_is_rejected(client):
    response = client.put(
        "/v1/config",
        json={"assistants": {"workflow_builder": {"thinking": "maybe"}}},
    )

    assert response.status_code == 422


def test_unexpected_field_inside_a_function_is_rejected(client):
    response = client.put(
        "/v1/config",
        json={"assistants": {"workflow_builder": {"temperature": 0.5}}},
    )

    assert response.status_code == 422


def test_a_credential_pasted_as_a_model_is_rejected(client):
    response = client.put(
        "/v1/config",
        json={"assistants": {"workflow_builder": {"model": "sk-abcdef123456"}}},
    )

    assert response.status_code == 422
    assert "sk-abcdef123456" not in response.text


# --------------------------------------------------------------------------- #
# Learned capabilities
# --------------------------------------------------------------------------- #


def test_nothing_is_known_before_a_model_has_been_tried(client):
    body = client.get("/v1/config/llm-capabilities").json()

    assert body["capabilities"] == []
    assert "tool_choice" in body["known_downgrades"]


def test_a_reported_downgrade_is_stored_with_its_cause(client):
    client.post(
        "/v1/config/llm-capabilities",
        json={
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "downgrades": ["tool_choice"],
            "evidence": "400 Thinking mode does not support this tool_choice",
        },
    )

    stored = client.get("/v1/config/llm-capabilities").json()["capabilities"]
    assert len(stored) == 1
    assert stored[0]["downgrades"] == ["tool_choice"]
    # A downgrade with no cause on record is indistinguishable from a bug.
    assert "Thinking mode" in stored[0]["evidence"]


def test_downgrades_accumulate_as_a_model_reveals_them(client):
    # Which is how this was actually found: one 400 at a time, each after
    # the previous fix shipped.
    for name, evidence in (
        ("tool_choice", "400 Thinking mode does not support this tool_choice"),
        ("reasoning_content", "400 The reasoning_content must be passed back"),
    ):
        client.post(
            "/v1/config/llm-capabilities",
            json={"provider": "deepseek", "model": "m", "downgrades": [name], "evidence": evidence},
        )

    stored = client.get("/v1/config/llm-capabilities").json()["capabilities"]
    assert stored[0]["downgrades"] == ["reasoning_content", "tool_choice"]


def test_an_unrecognised_downgrade_name_is_dropped(client):
    # Stored, it would show in the UI as an applied workaround and mean
    # nothing to whoever reads it.
    client.post(
        "/v1/config/llm-capabilities",
        json={"provider": "deepseek", "model": "m", "downgrades": ["tool_choice", "invented"]},
    )

    stored = client.get("/v1/config/llm-capabilities").json()["capabilities"]
    assert stored[0]["downgrades"] == ["tool_choice"]


def test_a_model_that_accepted_everything_is_distinguishable_from_untried(client):
    client.post("/v1/config/llm-capabilities", json={"model": "strong", "downgrades": []})

    stored = client.get("/v1/config/llm-capabilities").json()["capabilities"]
    assert len(stored) == 1
    assert stored[0]["downgrades"] == []


def test_what_was_learned_surfaces_on_the_function_using_that_model(client):
    client.put("/v1/config", json={"llm_provider": "deepseek", "llm_model": "deepseek-v4-flash"})
    client.post(
        "/v1/config/llm-capabilities",
        json={
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "downgrades": ["tool_choice"],
            "evidence": "400 Thinking mode does not support this tool_choice",
        },
    )

    builder = _function(client, "workflow_builder")
    assert builder["detected_downgrades"] == ["tool_choice"]
    assert "Thinking mode" in builder["detected_evidence"]


def test_detected_downgrades_are_not_written_into_operator_settings(client):
    # What the system found out must never be presented as what the
    # operator asked for.
    client.put("/v1/config", json={"llm_provider": "deepseek", "llm_model": "m"})
    client.post(
        "/v1/config/llm-capabilities",
        json={"provider": "deepseek", "model": "m", "downgrades": ["tool_choice"]},
    )

    builder = _function(client, "workflow_builder")
    assert builder["thinking"] == "auto"  # unchanged by the discovery
    assert builder["detected_downgrades"] == ["tool_choice"]


def _function(client, name: str) -> dict:
    body = client.get("/v1/config").json()
    return next(item for item in body["assistants"] if item["function"] == name)


def test_a_credential_nested_in_a_rejected_object_is_not_echoed(client):
    # The field names here are all innocuous, so name-based redaction alone
    # would return the key: the whole object is echoed when the parent is
    # rejected.
    response = client.put(
        "/v1/config",
        json={"assistants": {"workflow_builder": {"model": "x", "provider": "sk-live-secret"}}},
    )

    assert response.status_code == 422
    assert "sk-live-secret" not in response.text
    # Still says which function and field were wrong.
    assert "workflow_builder" in response.text


def test_scrubbing_leaves_ordinary_values_readable(client):
    from aiops_api.modules.config.errors import scrub

    assert scrub({"model": "deepseek-chat"}) == {"model": "deepseek-chat"}
    assert scrub("unknown assistant functions: ['typo']") == "unknown assistant functions: ['typo']"
