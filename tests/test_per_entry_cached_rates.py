"""Per-entry cached-input rates (0.10.0) replace the one global 0.1x multiplier.

Rates are from the raw vendor pages fetched 2026-10-07 (developers.openai.com/api/docs/pricing.md
sha256 prefix 0d9fb6b240209bb7); expected figures are hand-computed.
"""

from __future__ import annotations

import copy

import pytest

from adk_tracegauge._adapter import price_digest
from adk_tracegauge._cost import SessionDigest, TurnDigest
from adk_tracegauge._pricing import effective_prices, load_gemini_prices


def _cost(model: str, tokens_in: int, cached: int, tokens_out: int, prices=None) -> float:
    digest = SessionDigest("s", [TurnDigest(0, "ai", tokens_in, tokens_out, cached, model=model)])
    return price_digest(digest, prices=prices or load_gemini_prices()).total_usd


def test_m1_gpt_4o_uncached_is_the_published_rate():
    # PRE-REGISTERED (suite M1): 1M in x $2.50/M + 100k out x $10/M = $3.50.
    assert _cost("gpt-4o", 1_000_000, 0, 100_000) == pytest.approx(3.50)


@pytest.mark.parametrize(
    ("model", "cached_rate"),
    [
        ("gpt-4o", 1.25),
        ("gpt-4o-mini", 0.075),
        ("o1", 7.50),
        ("o3-mini", 0.55),
        ("gpt-4.1", 0.50),
        ("gpt-4.1-mini", 0.10),
        ("gpt-4.1-nano", 0.025),
        ("o3", 0.50),
        ("o4-mini", 0.275),
    ],
)
def test_a_fully_cached_prompt_is_priced_at_the_vendor_cached_rate_not_0_1x(model, cached_rate):
    # 1M cached tokens, no output: exactly the published cached $/M.
    assert _cost(model, 1_000_000, 1_000_000, 0) == pytest.approx(cached_rate)


def test_half_cached_gpt_4o_prompt():
    # 500k fresh x $2.50/M + 500k cached x $1.25/M + 100k out x $10/M = 1.25 + 0.625 + 1.0
    assert _cost("gpt-4o", 1_000_000, 500_000, 100_000) == pytest.approx(2.875)


def test_the_old_global_multiplier_would_have_under_priced_gpt_4o_by_5x():
    # Regression guard for the bug this fixes: 0.1x of $2.50 = $0.25/M, but the vendor bills $1.25.
    assert _cost("gpt-4o", 1_000_000, 1_000_000, 0) != pytest.approx(0.25)


def test_a_custom_table_without_a_cached_rate_still_falls_back_to_the_multiplier():
    prices = copy.deepcopy(load_gemini_prices())
    del prices["models"]["gpt-4o"]["cached_input_usd_per_mtok"]
    assert _cost("gpt-4o", 1_000_000, 1_000_000, 0, prices) == pytest.approx(0.25)


def test_a_promo_entrys_cached_rate_follows_the_promo_switch():
    prices = copy.deepcopy(load_gemini_prices())
    entry = prices["models"]["gemini-3.6-flash"]
    entry["promo_until"] = "2099-01-01"
    assert effective_prices(prices)["models"]["gemini-3.6-flash"][
        "cached_input_usd_per_mtok"
    ] == pytest.approx(0.075)
    entry["promo_until"] = "2020-01-01"  # promo over: the standard rate's own cached rate applies
    after = effective_prices(prices)["models"]["gemini-3.6-flash"]
    assert after["input_usd_per_mtok"] == pytest.approx(1.5)
    assert after["cached_input_usd_per_mtok"] == pytest.approx(0.15)


def test_every_bundled_entry_that_can_be_cached_carries_a_verified_rate():
    for key, entry in load_gemini_prices()["models"].items():
        if key == "__local_zero_cost__" or entry.get("retired"):
            continue
        assert "cached_input_usd_per_mtok" in entry, key
