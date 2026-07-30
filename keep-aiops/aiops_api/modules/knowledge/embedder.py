"""Pluggable embedder for the Knowledge Engine (ADR-0005, ADR-0007).

The LLM is OPTIONAL at runtime: when ``AIOPS_LLM_EMBEDDING_MODEL`` is empty,
``get_embedder`` returns None and retrieval falls back to deterministic
keyword-overlap scoring. When set, embeddings are produced through LiteLLM
(imported lazily so the dependency is only paid in embedding mode).
"""

import logging
from collections.abc import Callable

from aiops_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# texts -> one float vector per text, same order.
Embedder = Callable[[list[str]], list[list[float]]]


def get_embedder(settings: Settings | None = None) -> Embedder | None:
    """LiteLLM-backed embedder when configured, else None (keyword mode)."""
    settings = settings or get_settings()
    model = settings.llm_embedding_model
    if not model:
        return None
    api_key = settings.llm_api_key or None

    def embed(texts: list[str]) -> list[list[float]]:
        import litellm  # lazy: only imported when embedding mode is enabled

        response = litellm.embedding(model=model, input=texts, api_key=api_key)
        return [list(item["embedding"]) for item in response["data"]]

    logger.info("knowledge embedder configured", extra={"model": model})
    return embed
