"""Turning tokens into money, and being honest when we cannot."""

import pytest

from aiops_api.modules.rca.pricing import DEFAULT_PRICES, price_completion


def test_a_known_model_is_priced_from_the_token_split():
    # deepseek-chat: 0.27 in / 1.10 out per million.
    cost = price_completion("deepseek-chat", 1_000_000, 1_000_000)
    assert cost.priced is True
    assert cost.usd == pytest.approx(1.37)


def test_provider_prefixes_and_date_suffixes_still_resolve():
    """Real model names are decorated; an exact lookup would price nothing."""
    assert price_completion("deepseek/deepseek-chat", 1000, 0).priced is True
    assert price_completion("gpt-4o-2024-08-06", 1000, 0).priced is True


def test_the_longest_match_wins_so_mini_is_not_priced_as_full():
    """gpt-4o-mini contains gpt-4o; pricing it as the larger model would
    overstate spend by ~17x and quietly discredit the whole figure."""
    mini = price_completion("gpt-4o-mini", 1_000_000, 0)
    full = price_completion("gpt-4o", 1_000_000, 0)
    assert mini.usd < full.usd
    assert mini.usd == pytest.approx(DEFAULT_PRICES["gpt-4o-mini"][0])


def test_an_unknown_model_is_unpriced_not_free():
    """Zero-cost and unknown-cost are different claims.

    Reporting an unpriced model as free is the same class of error as showing
    stub evidence as live: a number that looks like a measurement and is not.
    """
    cost = price_completion("some-local-llama", 5000, 5000)
    assert cost.priced is False
    assert cost.usd == 0.0


def test_prices_can_be_overridden_without_a_deploy(monkeypatch):
    monkeypatch.setenv("AIOPS_LLM_PRICES", '{"my-model": [10.0, 20.0]}')
    cost = price_completion("my-model", 1_000_000, 1_000_000)
    assert cost.priced is True
    assert cost.usd == pytest.approx(30.0)


def test_a_malformed_price_table_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.setenv("AIOPS_LLM_PRICES", "{not json")
    assert price_completion("deepseek-chat", 1_000_000, 0).priced is True


def test_pricing_never_raises_on_odd_input():
    assert price_completion("", 0, 0).priced is False
    assert price_completion(None, 0, 0).priced is False  # type: ignore[arg-type]
