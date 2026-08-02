"""Generic OpenAI-compatible provider: one code path for every vendor
exposing an OpenAI-shaped API, including self-hosted ones."""

import json
from unittest.mock import MagicMock, patch

from keep.providers.openai_compatible_provider.openai_compatible_provider import (
    OpenaiCompatibleProvider,
)


def _make(**auth_overrides):
    auth = {"api_url": "http://ollama:11434/v1", "model": "llama3.3"}
    auth.update(auth_overrides)
    provider = OpenaiCompatibleProvider.__new__(OpenaiCompatibleProvider)
    provider.config = MagicMock(authentication=auth)
    provider.logger = MagicMock()
    provider.validate_config()
    return provider


def test_config_requires_url_and_model():
    provider = _make()

    assert provider.authentication_config.api_url == "http://ollama:11434/v1"
    assert provider.authentication_config.model == "llama3.3"
    assert provider.authentication_config.api_key is None


def test_keyless_self_hosted_endpoints_get_a_placeholder_key():
    """The OpenAI SDK refuses an empty api_key even when the endpoint
    accepts none — the placeholder is what makes Ollama/LM Studio work."""
    provider = _make()

    with patch(
        "keep.providers.openai_compatible_provider.openai_compatible_provider.OpenAI"
    ) as openai_cls:
        provider._client()

    assert openai_cls.call_args.kwargs["api_key"] == "not-needed"
    assert openai_cls.call_args.kwargs["base_url"] == "http://ollama:11434/v1"


def test_configured_model_is_the_default():
    provider = _make()

    with patch.object(OpenaiCompatibleProvider, "_client") as client_factory:
        create = client_factory.return_value.chat.completions.create
        create.return_value = completion_with_content('{"ok": true}')
        provider._query(prompt="hi")

    assert create.call_args.kwargs["model"] == "llama3.3"


def test_explicit_model_overrides_the_configured_default():
    """A workflow can pick a cheaper or larger model per step."""
    provider = _make()

    with patch.object(OpenaiCompatibleProvider, "_client") as client_factory:
        create = client_factory.return_value.chat.completions.create
        create.return_value = completion_with_content("plain answer")
        result = provider._query(prompt="hi", model="llama3.3:70b")

    assert create.call_args.kwargs["model"] == "llama3.3:70b"
    assert result["model"] == "llama3.3:70b"


def test_json_responses_are_parsed_but_plain_text_survives():
    provider = _make()

    with patch.object(OpenaiCompatibleProvider, "_client") as client_factory:
        client_factory.return_value.chat.completions.create.return_value = (
            completion_with_content("not json at all")
        )
        result = provider._query(prompt="hi")

    assert result["response"] == "not json at all"


def test_missing_models_route_returns_empty_not_error():
    """/models is optional in the OpenAI spec; a provider without it must
    not fail scope validation flows."""
    provider = _make()

    with patch.object(OpenaiCompatibleProvider, "_client") as client_factory:
        provider._client.return_value.models.list.side_effect = Exception("404")
        assert provider.list_models() == []


def test_list_models_returns_ids_when_supported():
    provider = _make()

    with patch.object(OpenaiCompatibleProvider, "_client") as client_factory:
        model_a = MagicMock(id="b-model")
        model_b = MagicMock(id="a-model")
        provider._client.return_value.models.list.return_value = MagicMock(
            data=[model_a, model_b]
        )
        assert provider.list_models() == ["a-model", "b-model"]


def completion_with_content(content: str):
    message = MagicMock(content=content)
    choice = MagicMock(message=message)
    return MagicMock(choices=[choice])


# --------------------------------------------------------------------------- #
# The `model` field now works on the vendor-specific AI providers too
# --------------------------------------------------------------------------- #


def test_deepseek_provider_accepts_a_model_field():
    from keep.providers.deepseek_provider.deepseek_provider import (
        DeepseekProviderAuthConfig,
    )

    config = DeepseekProviderAuthConfig(api_key="k", model="deepseek-v4-pro")
    assert config.model == "deepseek-v4-pro"


def test_openai_provider_accepts_a_model_field():
    from keep.providers.openai_provider.openai_provider import (
        OpenaiProviderAuthConfig,
    )

    config = OpenaiProviderAuthConfig(api_key="k", model="gpt-4o-mini")
    assert config.model == "gpt-4o-mini"
