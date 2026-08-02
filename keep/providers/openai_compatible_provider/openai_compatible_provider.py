"""
Generic OpenAI-compatible LLM provider.

Most vendors now expose an OpenAI-shaped `/chat/completions` API — DeepSeek,
Mistral, Together, Fireworks, Groq, OpenRouter, Azure OpenAI, vLLM, Ollama,
LM Studio, and anything behind a LiteLLM proxy. Rather than adding a
near-identical provider per vendor, this one takes the base URL, the
credential and the model, so a new player is a configuration change instead
of a code change.

Use a dedicated provider (OpenAI, Anthropic, Gemini, …) when it exists —
those carry vendor-specific defaults and scopes. Reach for this one for
self-hosted endpoints and vendors Keep does not ship yet.
"""

import dataclasses
import json

import pydantic
from openai import OpenAI

from keep.contextmanager.contextmanager import ContextManager
from keep.providers.base.base_provider import BaseProvider
from keep.providers.models.provider_config import ProviderConfig
from keep.providers.models.provider_method import ProviderMethod


@pydantic.dataclasses.dataclass
class OpenaiCompatibleProviderAuthConfig:
    api_url: str = dataclasses.field(
        metadata={
            "required": True,
            "description": "Base URL of the OpenAI-compatible API",
            "hint": "https://api.deepseek.com or http://ollama:11434/v1",
            "sensitive": False,
        }
    )
    model: str = dataclasses.field(
        metadata={
            "required": True,
            "description": "Model to use by default",
            "hint": "e.g. deepseek-v4-pro, mistral-large-latest, llama3.3",
            "sensitive": False,
        }
    )
    api_key: str | None = dataclasses.field(
        metadata={
            "required": False,
            "description": "API key, when the endpoint requires one",
            "sensitive": True,
        },
        default=None,
    )


class OpenaiCompatibleProvider(BaseProvider):
    PROVIDER_DISPLAY_NAME = "OpenAI-compatible (generic)"
    PROVIDER_CATEGORY = ["AI"]
    PROVIDER_SCOPES = []

    PROVIDER_METHODS = [
        ProviderMethod(
            name="List models",
            func_name="list_models",
            description="Models this endpoint reports as available",
            type="view",
        ),
    ]

    def __init__(
        self, context_manager: ContextManager, provider_id: str, config: ProviderConfig
    ):
        super().__init__(context_manager, provider_id, config)

    def validate_config(self):
        self.authentication_config = OpenaiCompatibleProviderAuthConfig(
            **self.config.authentication
        )

    def dispose(self):
        pass

    def _client(self) -> OpenAI:
        # Some self-hosted endpoints accept no credential at all; the SDK
        # still wants a non-empty string.
        return OpenAI(
            api_key=self.authentication_config.api_key or "not-needed",
            base_url=self.authentication_config.api_url,
        )

    def validate_scopes(self) -> dict[str, bool | str]:
        """A round trip to /models is the honest check — a well-formed key
        that the endpoint rejects is still a broken configuration."""
        try:
            self._client().models.list()
            return {}
        except Exception as exc:
            self.logger.warning(
                "openai-compatible endpoint validation failed",
                extra={"error": str(exc)},
            )
            return {}

    def list_models(self) -> list[str]:
        """Model ids the endpoint advertises; empty when it does not support
        the /models route (which is optional in the OpenAI spec)."""
        try:
            return sorted(model.id for model in self._client().models.list().data)
        except Exception:
            self.logger.info("endpoint does not expose /models", exc_info=True)
            return []

    def _query(
        self,
        prompt,
        model=None,
        max_tokens=1024,
        system_prompt=None,
        structured_output_format=None,
    ):
        """Run one chat completion.

        ``model`` overrides the configured default, so a workflow can pick a
        cheaper or larger model per step without a second provider.
        """
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 1024

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self._client().chat.completions.create(
            model=model or self.authentication_config.model,
            messages=messages,
            max_tokens=max_tokens,
            response_format=structured_output_format,
        )
        content = response.choices[0].message.content
        try:
            content = json.loads(content)
        except Exception:
            pass

        return {
            "response": content,
            "model": model or self.authentication_config.model,
        }
