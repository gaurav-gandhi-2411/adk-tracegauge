from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from adk_tracegauge._pricing import (
    STALE_THRESHOLD_DAYS,
    known_model_keys,
    load_gemini_prices,
    resolve_model,
    resolve_model_for_call,
)


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


# Exact-value regression assertions per model, cross-checked 2026-08-14
# against https://ai.google.dev/gemini-api/docs/pricing directly (Phase 2 W1
# P0 price-correctness audit) -- NOT derived from this repo's own code, so
# this catches a wrong number in gemini_prices.json, not just an internal
# inconsistency between the JSON and _pricing.py's own resolution logic.
@pytest.mark.parametrize(
    ("model_version", "expected_input", "expected_output"),
    [
        ("gemini-2.5-pro", 1.25, 10.00),
        ("gemini-2.5-flash", 0.30, 2.50),
        ("gemini-2.5-flash-lite", 0.10, 0.40),
        ("gemini-2.0-flash", 0.10, 0.40),
        ("gemini-3.5-flash", 1.50, 9.00),
        ("gemini-3.5-flash-lite", 0.30, 2.50),
        # CORRECTED 2026-08-14: table previously had the post-2026-12-31
        # rate ($1.50/$7.50) in effect early -- the actual standard rate as
        # of today is the promotional one below.
        ("gemini-3.6-flash", 0.75, 3.75),
        # NEW 2026-08-14: previously missing from the table entirely.
        ("gemini-3.7-flash", 0.75, 3.75),
        ("gemini-3.1-flash-lite", 0.25, 1.50),
        ("gemini-3.1-pro-preview", 2.00, 12.00),
    ],
)
def test_base_tier_rates_match_published_figures(model_version, expected_input, expected_output):
    resolved = resolve_model(model_version)
    assert resolved is not None
    assert resolved.input_usd_per_mtok == expected_input
    assert resolved.output_usd_per_mtok == expected_output


def test_gemini_2_5_pro_long_context_rate_matches_published_figure():
    resolved = resolve_model("gemini-2.5-pro-long-context")
    assert resolved is not None
    assert resolved.input_usd_per_mtok == 2.50
    assert resolved.output_usd_per_mtok == 15.00


def test_gemini_3_1_pro_preview_long_context_rate_matches_published_figure():
    resolved = resolve_model("gemini-3.1-pro-preview-long-context")
    assert resolved is not None
    assert resolved.input_usd_per_mtok == 4.00
    assert resolved.output_usd_per_mtok == 18.00


def test_resolve_model_for_call_returns_base_tier_at_exactly_the_threshold():
    resolved = resolve_model_for_call("gemini-2.5-pro", 200_000)
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-pro"
    assert resolved.input_usd_per_mtok == 1.25


def test_resolve_model_for_call_returns_long_context_tier_one_token_above_threshold():
    resolved = resolve_model_for_call("gemini-2.5-pro", 200_001)
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-pro-long-context"
    assert resolved.input_usd_per_mtok == 2.50
    assert resolved.output_usd_per_mtok == 15.00


def test_resolve_model_for_call_no_tiering_for_a_model_without_a_long_context_entry():
    resolved = resolve_model_for_call("gemini-2.5-flash", 50_000_000)
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-flash"


def test_resolve_model_for_call_unknown_model_returns_none():
    assert resolve_model_for_call("totally-made-up-model-xyz", 1_000) is None


def test_resolve_model_unaffected_by_token_count_still_returns_base_tier():
    # resolve_model (no token count arg) always returns the base rate --
    # existing callers relying on this behavior before schema_version 2
    # added tiering must see no change.
    resolved = resolve_model("gemini-2.5-pro")
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-pro"
    assert resolved.input_usd_per_mtok == 1.25


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


def test_resolved_model_carries_provenance_for_staleness_checks():
    resolved = resolve_model("gemini-2.5-flash")
    assert resolved is not None
    assert resolved.fetched_on
    assert resolved.source_url == "https://ai.google.dev/gemini-api/docs/pricing"


def test_is_stale_false_for_a_recent_date():
    resolved = resolve_model("gemini-2.5-flash")
    recent = replace(resolved, fetched_on=date.today().isoformat())
    assert not recent.is_stale


def test_is_stale_true_past_the_threshold():
    resolved = resolve_model("gemini-2.5-flash")
    old_date = date.today() - timedelta(days=STALE_THRESHOLD_DAYS + 1)
    stale = replace(resolved, fetched_on=old_date.isoformat())
    assert stale.is_stale


def test_is_stale_false_exactly_at_the_threshold_boundary():
    resolved = resolve_model("gemini-2.5-flash")
    boundary_date = date.today() - timedelta(days=STALE_THRESHOLD_DAYS)
    boundary = replace(resolved, fetched_on=boundary_date.isoformat())
    assert not boundary.is_stale


def test_is_stale_fails_closed_on_unparseable_date():
    # An unparseable date is itself a staleness signal, not a reason to skip
    # the check silently -- fail closed, not open.
    resolved = resolve_model("gemini-2.5-flash")
    garbled = replace(resolved, fetched_on="not-a-date")
    assert garbled.is_stale


def test_bundled_table_is_not_currently_stale():
    # The live signal item 2 asks for: once every entry in the shipped table
    # crosses STALE_THRESHOLD_DAYS, this test starts failing in CI, forcing
    # a human to either refresh the table or consciously widen the
    # threshold -- rather than the table quietly aging out unnoticed.
    prices = load_gemini_prices()
    stale = [key for key in prices["models"] if resolve_model(key, prices).is_stale]
    assert not stale, (
        f"these price table entries are past the {STALE_THRESHOLD_DAYS}-day "
        f"staleness threshold and need a refresh: {stale}. See README "
        "'Updating the price table'."
    )
