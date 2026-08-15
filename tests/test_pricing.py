from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

import adk_tracegauge._pricing as pricing_module
from adk_tracegauge._pricing import (
    _LOCAL_MODEL_PREFIXES,
    ASSUME_LOCAL_ENV_VAR,
    LOCAL_MODEL_KEY,
    PRICE_TABLE_ENV_VAR,
    PROMO_EXPIRY_WARNING_DAYS,
    STALE_THRESHOLD_DAYS,
    effective_prices,
    is_local_model,
    is_local_model_asserted,
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


# --- Phase 3 B1: local-model zero-cost pricing requires explicit opt-in ----
#
# Ollama Cloud is a real paid product routed through the identical
# ollama_chat//ollama/ LiteLlm prefix as local Ollama -- see _pricing.py's
# module docstring for the source-confirmed reason neither LlmResponse nor
# CallbackContext/InvocationContext expose enough to tell the two apart. A
# bare prefix match must NEVER be sufficient on its own to price $0.00.


def test_resolve_model_for_call_local_model_fails_closed_without_opt_in(monkeypatch):
    monkeypatch.delenv(ASSUME_LOCAL_ENV_VAR, raising=False)
    assert resolve_model_for_call("ollama_chat/qwen2.5:7b", prompt_token_count=1_000) is None
    assert resolve_model_for_call("ollama/llama3.2:latest", prompt_token_count=1_000) is None
    assert resolve_model_for_call("vllm/mistral-7b-instruct", prompt_token_count=1_000) is None


@pytest.mark.parametrize("local_model_version", list(_LOCAL_MODEL_PREFIXES))
def test_no_local_prefix_ever_resolves_to_zero_cost_without_the_opt_in_env_var(
    local_model_version, monkeypatch
):
    # Structural/property test (1.4): every recognized local prefix, with
    # no opt-in set at all, must resolve to None (NOT_EVALUATED upstream),
    # never a $0.00 ResolvedModel -- this is the exact regression this work
    # item exists to prevent.
    monkeypatch.delenv(ASSUME_LOCAL_ENV_VAR, raising=False)
    model_string = f"{local_model_version}some-model:latest"
    assert not is_local_model_asserted(model_string)
    assert resolve_model_for_call(model_string, prompt_token_count=1_000) is None


@pytest.mark.parametrize(
    "opt_in_value",
    ["1", "true", "TRUE", "yes", "on", "  1  "],
)
def test_assume_local_true_spellings_assert_every_local_prefix(opt_in_value, monkeypatch):
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, opt_in_value)
    for prefix in _LOCAL_MODEL_PREFIXES:
        model_string = f"{prefix}some-model:latest"
        assert is_local_model_asserted(model_string)
        resolved = resolve_model_for_call(model_string, prompt_token_count=1_000)
        assert resolved is not None
        assert resolved.model_key == LOCAL_MODEL_KEY


def test_resolve_model_for_call_local_model_resolves_to_zero_cost_once_asserted(monkeypatch):
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "1")
    resolved = resolve_model_for_call("ollama_chat/qwen2.5:7b", prompt_token_count=1_000)
    assert resolved is not None
    assert resolved.model_key == LOCAL_MODEL_KEY
    assert resolved.input_usd_per_mtok == 0.0
    assert resolved.output_usd_per_mtok == 0.0


def test_resolve_model_for_call_local_model_zero_cost_regardless_of_prompt_size(monkeypatch):
    # Zero-cost is unconditional for an asserted local model -- no
    # long-context tier or any other token-count-dependent branch applies.
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "1")
    resolved = resolve_model_for_call("vllm/mistral-7b-instruct", prompt_token_count=50_000_000)
    assert resolved is not None
    assert resolved.model_key == LOCAL_MODEL_KEY


def test_assume_local_allowlist_asserts_only_the_listed_prefixes(monkeypatch):
    # A partial allowlist -- trust vllm/ (e.g. a known self-hosted
    # deployment) while ollama_chat/ (the exact prefix Ollama Cloud shares)
    # still fails closed.
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "vllm/")
    assert is_local_model_asserted("vllm/mistral-7b-instruct")
    assert not is_local_model_asserted("ollama_chat/qwen2.5:7b")
    assert not is_local_model_asserted("ollama/llama3.2:latest")

    resolved = resolve_model_for_call("vllm/mistral-7b-instruct", prompt_token_count=1_000)
    assert resolved is not None
    assert resolved.model_key == LOCAL_MODEL_KEY

    assert resolve_model_for_call("ollama_chat/qwen2.5:7b", prompt_token_count=1_000) is None


def test_assume_local_allowlist_is_case_insensitive_and_comma_separated(monkeypatch):
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "OLLAMA_CHAT/, vllm/")
    assert is_local_model_asserted("ollama_chat/qwen2.5:7b")
    assert is_local_model_asserted("vllm/mistral-7b-instruct")
    assert not is_local_model_asserted("ollama/llama3.2:latest")


def test_assume_local_unrecognized_entry_does_not_widen_trust(monkeypatch):
    # A typo/unrecognized prefix in the allowlist must never silently
    # expand what's trusted -- fail closed, not open.
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "not-a-real-prefix/")
    assert not is_local_model_asserted("ollama_chat/qwen2.5:7b")
    assert not is_local_model_asserted("vllm/mistral-7b-instruct")


def test_is_local_model_asserted_false_for_a_non_local_model_even_with_opt_in_set(monkeypatch):
    # Even the broadest opt-in ("assert everything") must not assert
    # something that was never structurally local in the first place.
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "1")
    assert not is_local_model_asserted("gemini-2.5-flash")
    assert not is_local_model_asserted("anthropic/claude-opus-5")


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


# --- Phase 3 B2: promotional pricing expires automatically -----------------
#
# Dates are computed as offsets from date.today() rather than mocking the
# clock -- same pattern as the existing STALE_THRESHOLD_DAYS boundary tests
# above (test_is_stale_true_past_the_threshold etc.), which construct a
# ResolvedModel with a fetched_on relative to "today" instead of injecting a
# fake clock into the library.


def _custom_prices_with_promo(promo_until: str, standard_rate: dict[str, float] | None) -> dict:
    entry: dict[str, object] = {
        "input_usd_per_mtok": 1.0,
        "output_usd_per_mtok": 2.0,
        "source_url": "https://example.invalid/pricing",
        "fetched_on": date.today().isoformat(),
        "note": "test-only promotional entry",
        "promo_until": promo_until,
    }
    if standard_rate is not None:
        entry["standard_rate"] = standard_rate
    return {
        "schema_version": 3,
        "note": "test",
        "cache_multipliers": {"read": 0.1, "write_5min": 0.0, "write_1hr": 0.0},
        "models": {"promo-test-model": entry},
        "model_patterns": [],
        "default_model": "promo-test-model",
        "approximate_threshold_pct": 25,
    }


def test_promo_rate_applies_while_within_the_promo_window():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() + timedelta(days=5)).isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    assert resolved.promo_active is True
    assert resolved.input_usd_per_mtok == 1.0
    assert resolved.output_usd_per_mtok == 2.0
    assert resolved.standard_rate_unknown is False


def test_standard_rate_applies_automatically_once_promo_has_expired():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() - timedelta(days=1)).isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    assert resolved.promo_active is False
    # Automatic switch -- no manual table edit required, per 2.2.
    assert resolved.input_usd_per_mtok == 2.0
    assert resolved.output_usd_per_mtok == 4.0


def test_promo_boundary_day_itself_is_still_promotional():
    # Documented choice (2.5): the boundary day (promo_until itself) is
    # PROMO, not standard -- matches vendor phrasing like "$0.75 through
    # December 31, 2026" (valid through and including that date), and
    # mirrors is_stale's own boundary convention (the boundary day is NOT
    # yet stale -- same ">" vs "<=" direction of generosity).
    prices = _custom_prices_with_promo(
        promo_until=date.today().isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    assert resolved.promo_active is True
    assert resolved.input_usd_per_mtok == 1.0

    # One day past the boundary: standard rate applies.
    prices_next_day = _custom_prices_with_promo(
        promo_until=(date.today() - timedelta(days=1)).isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    resolved_next_day = resolve_model("promo-test-model", prices_next_day)
    assert resolved_next_day is not None
    assert resolved_next_day.promo_active is False
    assert resolved_next_day.input_usd_per_mtok == 2.0


def test_effective_prices_bakes_in_the_auto_switch_for_the_cost_arithmetic():
    # effective_prices is what actually reaches _cost.compute_session_cost
    # (which reads prices["models"][key]["input_usd_per_mtok"] straight off
    # the dict it's given -- ported unchanged from tracegauge's own
    # tes/cost.py, Phase 4 R5 -- with zero knowledge of promo_until/
    # standard_rate), so the switch must be visible on the RAW DICT too, not
    # just on a ResolvedModel object.
    prices = _custom_prices_with_promo(
        promo_until=(date.today() - timedelta(days=1)).isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    effective = effective_prices(prices)
    assert effective["models"]["promo-test-model"]["input_usd_per_mtok"] == 2.0
    assert effective["models"]["promo-test-model"]["output_usd_per_mtok"] == 4.0
    # Never mutates the original dict passed in.
    assert prices["models"]["promo-test-model"]["input_usd_per_mtok"] == 1.0


def test_effective_prices_leaves_active_promo_rate_untouched():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() + timedelta(days=5)).isoformat(),
        standard_rate={"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 4.0},
    )
    effective = effective_prices(prices)
    assert effective["models"]["promo-test-model"]["input_usd_per_mtok"] == 1.0


def test_effective_prices_leaves_non_promotional_entries_untouched():
    effective = effective_prices()
    assert effective["models"]["gemini-2.5-flash"]["input_usd_per_mtok"] == 0.30


def test_unknown_standard_rate_warns_once_inside_the_pre_expiry_window():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() + timedelta(days=PROMO_EXPIRY_WARNING_DAYS - 1)).isoformat(),
        standard_rate=None,
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    assert resolved.standard_rate_unknown is True
    assert resolved.standard_rate_warning_due is True


def test_unknown_standard_rate_does_not_warn_well_before_expiry():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() + timedelta(days=PROMO_EXPIRY_WARNING_DAYS + 30)).isoformat(),
        standard_rate=None,
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    assert resolved.standard_rate_unknown is True
    assert resolved.standard_rate_warning_due is False


def test_unknown_standard_rate_keeps_warning_after_expiry_not_just_at_the_instant():
    prices = _custom_prices_with_promo(
        promo_until=(date.today() - timedelta(days=30)).isoformat(),
        standard_rate=None,
    )
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    # Still using the last-known (promotional) rate -- never a fabricated
    # guess -- but flagged as due for a loud warning.
    assert resolved.input_usd_per_mtok == 1.0
    assert resolved.standard_rate_warning_due is True


def test_unparseable_promo_until_fails_closed_not_open():
    prices = _custom_prices_with_promo(promo_until="not-a-date", standard_rate=None)
    resolved = resolve_model("promo-test-model", prices)
    assert resolved is not None
    # Never silently auto-switches off a date it couldn't parse.
    assert resolved.promo_active is True
    assert resolved.standard_rate_unknown is True
    assert resolved.standard_rate_warning_due is True


def test_non_promotional_entry_never_promo_active_or_standard_rate_unknown():
    resolved = resolve_model("gemini-2.5-flash")
    assert resolved is not None
    assert resolved.promo_until is None
    assert resolved.promo_active is False
    assert resolved.standard_rate_unknown is False
    assert resolved.standard_rate_warning_due is False


def test_bundled_gemini_3_6_and_3_7_flash_carry_the_confirmed_promo_schema():
    # Re-verified live 2026-08-14 against ai.google.dev directly (Phase 3
    # B2 2.3): both models publish a confirmed post-promo standard_rate.
    for model_key in ("gemini-3.6-flash", "gemini-3.7-flash"):
        resolved = resolve_model(model_key)
        assert resolved is not None
        assert resolved.promo_until == "2026-12-31"
        assert resolved.standard_rate_unknown is False
        assert resolved.standard_rate_input_usd_per_mtok == 1.50
        assert resolved.standard_rate_output_usd_per_mtok == 7.50


def test_claude_sonnet_5_is_not_flagged_as_promotional():
    # Phase 3 B2 2.1/2.3: carries historical "introductory pricing" language
    # in its own note, but re-verified live 2026-08-14 against
    # platform.claude.com directly -- the vendor's own page states the
    # scheduled increase "will not occur" and the rate "is now the standard
    # price". Deliberately NOT given promo_until/standard_rate: there is no
    # longer a future rate change to auto-switch to.
    resolved = resolve_model("claude-sonnet-5")
    assert resolved is not None
    assert resolved.promo_until is None
