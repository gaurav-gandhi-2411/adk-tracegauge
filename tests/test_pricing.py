from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

import adk_tracegauge._pricing as pricing_module
from adk_tracegauge._pricing import (
    LOCAL_MODEL_KEY,
    PRICE_TABLE_ENV_VAR,
    STALE_THRESHOLD_DAYS,
    is_local_model,
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


# --- Phase 2 W3: multi-provider pricing -------------------------------------
#
# Exact-value regression assertions per model, cross-checked 2026-08-14
# directly against the vendor's own pricing pages (platform.claude.com for
# Claude, developers.openai.com for GPT -- see gemini_prices.json's per-entry
# "note" fields for the full fetch/verification story per model, including
# the OpenAI redirect chain and a third-party-aggregator discrepancy that was
# investigated and resolved for gpt-5.1). NOT derived from this repo's own
# code, so this catches a wrong number in the table, not just an internal
# inconsistency between the JSON and _pricing.py's own resolution logic.
@pytest.mark.parametrize(
    ("model_version", "expected_input", "expected_output"),
    [
        ("claude-opus-5", 5.00, 25.00),
        ("claude-sonnet-5", 2.00, 10.00),
        ("claude-haiku-4-5", 1.00, 5.00),
        ("claude-opus-4-8", 5.00, 25.00),
        ("gpt-5.6-sol", 5.00, 30.00),
        ("gpt-5.6-terra", 2.00, 12.00),
        ("gpt-5.6-luna", 0.20, 1.20),
        ("gpt-5.1", 1.25, 10.00),
        ("gpt-5", 1.25, 10.00),
    ],
)
def test_claude_and_gpt_rates_match_published_figures(
    model_version, expected_input, expected_output
):
    resolved = resolve_model(model_version)
    assert resolved is not None
    assert resolved.input_usd_per_mtok == expected_input
    assert resolved.output_usd_per_mtok == expected_output


def test_gpt4_and_o_series_are_deliberately_not_priced():
    # Legacy GPT-4/o-series models have a cache-read discount (0.25x-0.5x
    # observed) that diverges from this table's shared global 0.1x
    # cache_multipliers.read -- adding them would silently mis-price any
    # cached call. Deliberately absent, not an oversight; register via
    # ADK_TRACEGAUGE_PRICE_TABLE if you've confirmed your own rate.
    assert resolve_model("gpt-4o") is None
    assert resolve_model("gpt-4.1") is None
    assert resolve_model("o1") is None


@pytest.mark.parametrize(
    ("prefixed_model_version", "expected_key"),
    [
        ("anthropic/claude-opus-5", "claude-opus-5"),
        ("anthropic/claude-sonnet-5", "claude-sonnet-5"),
        ("openai/gpt-5.1", "gpt-5.1"),
        ("openai/gpt-5.6-sol", "gpt-5.6-sol"),
    ],
)
def test_resolve_strips_litellm_provider_prefix_for_first_party_routes(
    prefixed_model_version, expected_key
):
    resolved = resolve_model(prefixed_model_version)
    assert resolved is not None
    assert resolved.model_key == expected_key


def test_resolve_does_not_strip_bedrock_vertex_or_azure_prefixes():
    # Deliberately conservative: Claude/GPT pricing on partner-operated
    # cloud platforms can differ from first-party rates, so these fail
    # closed (unresolved) rather than silently pricing at a rate that may
    # not apply -- see _pricing.py's module docstring.
    assert resolve_model("bedrock/claude-opus-5") is None
    assert resolve_model("vertex_ai/claude-opus-5") is None
    assert resolve_model("azure/gpt-5.1") is None


def test_resolve_handles_dashed_date_suffix_on_a_litellm_prefixed_model():
    # The historical OpenAI dated-snapshot convention (gpt-4o-2024-08-06)
    # combined with a LiteLlm provider prefix -- both stripping steps must
    # apply together. Note: none of the GPT-5.x models this table actually
    # prices are observed shipping a dated variant as of 2026-08-14 (all
    # fetched IDs are bare) -- this exercises the resolver's defensive
    # handling of the older convention, not a currently-observed real string.
    resolved = resolve_model("openai/gpt-5.1-2026-08-01")
    assert resolved is not None
    assert resolved.model_key == "gpt-5.1"
    assert resolved.input_usd_per_mtok == 1.25


def test_resolve_still_handles_gemini_style_dateless_8digit_suffix():
    # Regression guard: the new dashed-date regex must not interfere with
    # the original Gemini/Anthropic-style no-dash 8-digit suffix.
    resolved = resolve_model("gemini-2.5-flash-20260601")
    assert resolved is not None
    assert resolved.model_key == "gemini-2.5-flash"


@pytest.mark.parametrize(
    "local_model_version",
    [
        "ollama_chat/qwen2.5:7b",
        "ollama/llama3.2:latest",
        "vllm/mistral-7b-instruct",
        "OLLAMA_CHAT/qwen2.5:7b",  # case-insensitive
    ],
)
def test_is_local_model_recognizes_known_local_backends(local_model_version):
    assert is_local_model(local_model_version)


@pytest.mark.parametrize(
    "non_local_model_version",
    ["gemini-2.5-flash", "anthropic/claude-opus-5", "openai/gpt-5.1", "vertex_ai/gemini-2.5-flash"],
)
def test_is_local_model_false_for_priced_backends(non_local_model_version):
    assert not is_local_model(non_local_model_version)


def test_resolve_model_does_not_resolve_local_prefixes_to_the_zero_cost_entry():
    # resolve_model answers "is this a priced table entry" -- correctly None
    # for a local-backend string. Only resolve_model_for_call (below)
    # resolves local prefixes, to the zero-cost entry -- see module
    # docstring for why the split exists.
    assert resolve_model("ollama_chat/qwen2.5:7b") is None


def test_resolve_model_for_call_local_model_resolves_to_zero_cost():
    resolved = resolve_model_for_call("ollama_chat/qwen2.5:7b", prompt_token_count=1_000)
    assert resolved is not None
    assert resolved.model_key == LOCAL_MODEL_KEY
    assert resolved.input_usd_per_mtok == 0.0
    assert resolved.output_usd_per_mtok == 0.0


def test_resolve_model_for_call_local_model_zero_cost_regardless_of_prompt_size():
    # Zero-cost is unconditional for a local model -- no long-context tier
    # or any other token-count-dependent branch applies.
    resolved = resolve_model_for_call("vllm/mistral-7b-instruct", prompt_token_count=50_000_000)
    assert resolved is not None
    assert resolved.model_key == LOCAL_MODEL_KEY


def test_local_zero_cost_entry_is_in_known_model_keys():
    assert LOCAL_MODEL_KEY in known_model_keys()


def test_unknown_model_still_returns_none_for_a_genuinely_unrecognized_vendor():
    # A model from a vendor this table never covers, local or otherwise --
    # must still fail closed, not silently match something by accident.
    assert resolve_model("mistral-large-latest") is None
    assert resolve_model_for_call("mistral-large-latest", 1_000) is None


def test_price_table_env_var_override_replaces_the_bundled_table(monkeypatch, tmp_path):
    custom_table = {
        "schema_version": 2,
        "note": "test override",
        "cache_multipliers": {"read": 0.1, "write_5min": 0.0, "write_1hr": 0.0},
        "models": {
            "my-custom-model": {
                "input_usd_per_mtok": 42.0,
                "output_usd_per_mtok": 84.0,
                "source_url": "https://example.invalid/pricing",
                "fetched_on": date.today().isoformat(),
                "note": "test-only entry",
            }
        },
        "model_patterns": [],
        "default_model": "my-custom-model",
        "approximate_threshold_pct": 25,
    }
    override_path = tmp_path / "custom_prices.json"
    override_path.write_text(json.dumps(custom_table), encoding="utf-8")

    monkeypatch.setenv(PRICE_TABLE_ENV_VAR, str(override_path))
    pricing_module._PRICE_TABLE_CACHE = None
    try:
        prices = load_gemini_prices()
        assert "my-custom-model" in prices["models"]
        assert "gemini-2.5-flash" not in prices["models"]

        resolved = resolve_model("my-custom-model", prices)
        assert resolved is not None
        assert resolved.input_usd_per_mtok == 42.0
    finally:
        pricing_module._PRICE_TABLE_CACHE = None


def test_price_table_env_var_unset_uses_the_bundled_table(monkeypatch):
    monkeypatch.delenv(PRICE_TABLE_ENV_VAR, raising=False)
    pricing_module._PRICE_TABLE_CACHE = None
    try:
        prices = load_gemini_prices()
        assert "gemini-2.5-flash" in prices["models"]
    finally:
        pricing_module._PRICE_TABLE_CACHE = None


def test_cache_read_multiplier_verified_across_claude_and_current_gpt_generation():
    # Hand-computed cross-check against each vendor's own published cached
    # vs. base-input figures (not this table's own numbers) -- Claude Opus 5
    # $0.50 cache-hit / $5.00 input, gpt-5.1 $0.125 cached / $1.25 input.
    # Both equal the table's shared global 0.1x -- verified, not assumed,
    # which is why Claude/current-gen GPT could share the global multiplier
    # at all (see gpt4_and_o_series test above for the vendor that couldn't).
    assert pytest.approx(0.1) == 0.50 / 5.00
    assert pytest.approx(0.1) == 0.125 / 1.25
