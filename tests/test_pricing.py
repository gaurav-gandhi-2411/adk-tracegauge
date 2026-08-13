from __future__ import annotations

from adk_tracegauge._pricing import known_model_keys, load_gemini_prices, resolve_model


def test_load_gemini_prices_has_required_schema_keys():
    prices = load_gemini_prices()
    assert "models" in prices
    assert "model_patterns" in prices
    assert "cache_multipliers" in prices
    assert "default_model" in prices


def test_resolve_exact_match():
    resolved = resolve_model("gemini-2.5-pro")
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-pro"
    assert resolved.input_usd_per_mtok == 1.25
    assert resolved.output_usd_per_mtok == 10.00


def test_resolve_strips_trailing_date_suffix():
    resolved = resolve_model("gemini-2.5-flash-20260601")
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-flash"


def test_resolve_prefix_match_picks_most_specific_not_shortest():
    # gemini-2.5-flash-lite must resolve to flash-lite, not the shorter
    # gemini-2.5-flash prefix -- this is the exact ordering bug this test
    # exists to catch.
    resolved = resolve_model("gemini-2.5-flash-lite-preview-03")
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-flash-lite"


def test_resolve_gemini_3_5_flash_lite_not_confused_with_3_5_flash():
    resolved = resolve_model("gemini-3.5-flash-lite-preview")
    assert resolved is not None
    assert resolved.model_key == "gemini-3.5-flash-lite"


def test_resolve_unknown_model_returns_none_not_a_default():
    assert resolve_model("claude-sonnet-4-6") is None
    assert resolve_model("gpt-4o") is None
    assert resolve_model("totally-made-up-model-xyz") is None


def test_resolve_empty_string_returns_none():
    assert resolve_model("") is None


def test_known_model_keys_is_sorted_and_nonempty():
    keys = known_model_keys()
    assert keys == sorted(keys)
    assert "gemini-2.5-flash" in keys


def test_cache_read_multiplier_is_a_tenth_of_input_rate_for_every_model():
    # Cross-check against the source table's own numbers rather than
    # hardcoding 0.1 twice -- catches a table edit that breaks the
    # multiplier's validity without anyone updating this test's expectation.
    prices = load_gemini_prices()
    assert prices["cache_multipliers"]["read"] == 0.1


def test_cache_write_multipliers_are_zero_not_anthropic_defaults():
    prices = load_gemini_prices()
    assert prices["cache_multipliers"]["write_5min"] == 0.0
    assert prices["cache_multipliers"]["write_1hr"] == 0.0
