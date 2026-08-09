"""Turn token counts into money.

The budget caps `tool_calls`, `wall_time` and `llm_tokens`. None of those is a
currency, so "what did this investigation cost, and was it worth it" had no
answer — and the chain that spends the money is multiplicative: correlation
proposes a rule, the rule creates incidents, each incident starts an
investigation, each investigation calls tools and a model. The concurrency
gate protects the connection pool. Nothing protected the bill.

Prices are per million tokens, configured rather than compiled in, because
they change more often than this code will. An unknown model costs 0 — and
that is reported as unknown rather than free, since silently pricing an
unpriced model at zero is how a cost report becomes a lie.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# USD per million tokens, input/output. Deliberately small and explicit: this
# is a default to make the feature useful out of the box, not a price list to
# maintain. Override with AIOPS_LLM_PRICES.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-sonnet": (3.00, 15.00),
    "claude-haiku": (0.80, 4.00),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


@dataclass(frozen=True)
class Cost:
    """What a completion cost, and whether we actually know.

    ``priced`` is the honest bit. A model absent from the table yields
    ``usd=0.0, priced=False``, which a caller must render as "unknown" — never
    as free. The distinction is the same one the provenance work draws between
    "no data" and "zero".
    """

    usd: float
    priced: bool
    model: str


def _prices() -> dict[str, tuple[float, float]]:
    raw = os.environ.get("AIOPS_LLM_PRICES", "").strip()
    if not raw:
        return DEFAULT_PRICES
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("AIOPS_LLM_PRICES is not valid JSON; using defaults", exc_info=True)
        return DEFAULT_PRICES
    merged = dict(DEFAULT_PRICES)
    for name, pair in loaded.items():
        try:
            merged[str(name).lower()] = (float(pair[0]), float(pair[1]))
        except (TypeError, ValueError, IndexError):
            logger.warning("ignoring malformed price entry for %s", name)
    return merged


def _match(model: str, table: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Find a price for a model name.

    Providers prefix and suffix freely — ``deepseek/deepseek-chat``,
    ``gpt-4o-2024-08-06`` — so an exact lookup would miss almost everything
    real. Longest matching key wins, so ``gpt-4o-mini`` is not priced as
    ``gpt-4o``.
    """
    name = (model or "").lower()
    if not name:
        return None
    best: tuple[int, tuple[float, float]] | None = None
    for key, pair in table.items():
        if key in name and (best is None or len(key) > best[0]):
            best = (len(key), pair)
    return best[1] if best else None


def price_completion(model: str, prompt_tokens: int, completion_tokens: int) -> Cost:
    """Cost of one completion. Never raises."""
    pair = _match(model, _prices())
    if pair is None:
        logger.info("no price known for model %r; cost reported as unknown", model)
        return Cost(usd=0.0, priced=False, model=model)
    input_rate, output_rate = pair
    usd = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
    return Cost(usd=round(usd, 6), priced=True, model=model)
