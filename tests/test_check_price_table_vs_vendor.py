"""tests/test_check_price_table_vs_vendor.py — deterministic tests for
scripts/check_price_table_vs_vendor.py against recorded excerpts of what each vendor's page
returned when fetched live on 2026-09-20. No live network in CI.

The checker's contract, tested below: EVERY table entry ends in exactly one status; only VERIFIED
and an explicit, reasoned SKIPPED are non-failures; anything it cannot check (unmapped entry, no
cached rate to check the multiplier against, page not fetched) is UNVERIFIED and fails the run.
Its previous version verified 18 of 22 entries, silently skipped the long-context tiers, never
compared cached rates, and stayed red for three weeks without anyone acting -- so the tests here
pin the behaviours that failure mode needs: cached/tier/promo mismatches are caught, and nothing
is skipped silently.
"""

from __future__ import annotations

import copy
from datetime import date
from unittest.mock import patch

import pytest
from scripts.check_price_table_vs_vendor import (
    FetchError,
    Rate,
    audit,
    main,
    parse_anthropic_markdown,
    parse_google_html,
    parse_openai_markdown,
)

_ANTHROPIC_MD = """---
title: Pricing
---

## Model pricing

The following table shows pricing for all Claude models:

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Fable 5.1 | $10 / MTok | $12.50 / MTok | $20 / MTok | $0.25 / MTok<sup>1</sup> | $50 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock](https://example.invalid)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |

## Next section

| Model | Some Other Column |
| --- | --- |
| Claude Opus 5 | $999 / MTok |
"""

_OPENAI_MD = """# Pricing

### Standard pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 | $4.00 | $0.40 | $5.00 | $18.00 |
| gpt-5.1 | $1.25 | $0.125 | - | $10.00 | - | - | - | - |
| gpt-5.5-pro (<272K context length) | $30.00 | - | - | $180.00 | $60.00 | - | - | $270.00 |

### Batch pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-terra | $1.00 | $0.10 | $1.25 | $6.00 | $2.00 | $0.20 | $2.50 | $9.00 |
"""

# Faithful to the live markup: literal "<=" inside a cell, <br> separators, storage price line.
_GOOGLE_HTML = """
<h2 id="gemini-2.5-pro" data-text="Gemini 2.5 Pro">Gemini 2.5 Pro</h2>
<section><table class="pricing-table"><thead><tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr></thead><tbody>
<tr><td>Input price</td><td>Free of charge</td><td>$1.25, prompts <= 200k tokens<br>$2.50, prompts > 200k tokens</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$10.00, prompts <= 200k tokens<br>$15.00, prompts > 200k</td></tr>
<tr><td>Context caching price</td><td>Not available</td><td>$0.125, prompts <= 200k tokens<br>$0.25, prompts > 200k<br>$4.50 / 1,000,000 tokens per hour (storage price)</td></tr>
</tbody></table></section>
<h2 id="gemini-2.5-flash-lite" data-text="Gemini 2.5 Flash-Lite">Gemini 2.5 Flash-Lite</h2>
<section><table class="pricing-table"><thead><tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr></thead><tbody>
<tr><td>Input price (text, image, video)</td><td>Free of charge</td><td>$0.10 (text / image / video)<br>$0.30 (audio)</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$0.40</td></tr>
<tr><td>Context caching price</td><td>Not available</td><td>$0.01 (text / image / video)<br>$0.03 (audio)<br>$1.00 / 1,000,000 tokens per hour (storage price)</td></tr>
</tbody></table></section>
<h2 id="gemini-3.6-flash" data-text="Gemini 3.6 Flash">Gemini 3.6 Flash</h2>
<section><table class="pricing-table"><thead><tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr></thead><tbody>
<tr><td>Input price</td><td>Free of charge</td><td>$0.75 through December 31, 2026.<br>$1.50 starting January 1, 2027.</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$3.75 through December 31, 2026.<br>$7.50 starting January 1, 2027.</td></tr>
<tr><td>Context caching price</td><td>Free of charge</td><td>$0.075 through December 31, 2026.<br>$0.15 starting January 1, 2027.<br>$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.<br>$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027.</td></tr>
</tbody></table></section>
<h2 id="gemini-no-cache" data-text="No cache">No cache model</h2>
<section><table class="pricing-table"><thead><tr><th></th><th>Free Tier</th><th>Paid Tier</th></tr></thead><tbody>
<tr><td>Input price</td><td>-</td><td>$1.00</td></tr>
<tr><td>Output price</td><td>-</td><td>$2.00</td></tr>
<tr><td>Context caching price</td><td>-</td><td>Not available</td></tr>
</tbody></table></section>
"""

_PRICES = {
    "cache_multipliers": {"read": 0.1},
    "models": {
        "claude-opus-5": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 25.0},
        "gpt-5.6-terra": {"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 12.0},
        "gpt-5.1": {"input_usd_per_mtok": 1.25, "output_usd_per_mtok": 10.0},
        "gemini-2.5-pro": {"input_usd_per_mtok": 1.25, "output_usd_per_mtok": 10.0},
        "gemini-2.5-pro-long-context": {"input_usd_per_mtok": 2.5, "output_usd_per_mtok": 15.0},
        "gemini-2.5-flash-lite": {"input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.40},
        "gemini-3.6-flash": {
            "input_usd_per_mtok": 0.75,
            "output_usd_per_mtok": 3.75,
            "promo_until": "2026-12-31",
            "standard_rate": {"input_usd_per_mtok": 1.5, "output_usd_per_mtok": 7.5},
        },
        "gemini-2.0-flash": {
            "input_usd_per_mtok": 0.1,
            "output_usd_per_mtok": 0.4,
            "retired": True,
            "retired_on": "2026-06-01",
        },
        "__local_zero_cost__": {"input_usd_per_mtok": 0.0, "output_usd_per_mtok": 0.0},
    },
}


def _vendor():
    return (
        parse_anthropic_markdown(_ANTHROPIC_MD),
        parse_openai_markdown(_OPENAI_MD),
        _GOOGLE_HTML,
    )


def _audit(prices=None):
    an, op, g = _vendor()
    return {r.key: r for r in audit(prices or _PRICES, an, op, g)}


def _mutated(mutate):
    p = copy.deepcopy(_PRICES)
    mutate(p)
    return _audit(p)


# --- parsers ---------------------------------------------------------------------------------


def test_anthropic_parses_input_cached_and_output():
    table = parse_anthropic_markdown(_ANTHROPIC_MD)
    assert table["Claude Opus 5"] == Rate(5.0, 25.0, 0.50)


def test_anthropic_cache_hit_footnote_markup_does_not_break_the_cached_rate():
    # "$0.25 / MTok<sup>1</sup>" -- a real cell on the live page (Claude Fable 5.1, 0.025x input).
    assert parse_anthropic_markdown(_ANTHROPIC_MD)["Claude Fable 5.1"] == Rate(10.0, 50.0, 0.25)


def test_anthropic_strips_markdown_link_from_retired_model_name():
    assert "Claude Opus 4.1" in parse_anthropic_markdown(_ANTHROPIC_MD)


def test_anthropic_locates_columns_by_header_text_not_position():
    md = (
        "## Model pricing\n\n"
        "| Model | Output tokens | Cache hits and refreshes | Base input tokens |\n"
        "| --- | --- | --- | --- |\n"
        "| Claude Opus 5 | $25 / MTok | $0.50 / MTok | $5 / MTok |\n"
    )
    assert parse_anthropic_markdown(md)["Claude Opus 5"] == Rate(5.0, 25.0, 0.50)


def test_anthropic_does_not_read_past_the_model_pricing_section():
    assert parse_anthropic_markdown(_ANTHROPIC_MD)["Claude Opus 5"].input == 5.0


def test_anthropic_missing_table_returns_empty_not_raise():
    assert parse_anthropic_markdown("# unrelated page") == {}


def test_openai_parses_standard_short_context_only():
    table = parse_openai_markdown(_OPENAI_MD)
    assert table["gpt-5.6-terra"] == Rate(2.0, 12.0, 0.20)  # NOT the Batch tier's $1/$6


def test_openai_dash_cached_cell_is_none_not_zero():
    assert parse_openai_markdown(_OPENAI_MD)["gpt-5.5-pro (<272K context length)"].cached is None


def test_openai_gpt_5_1_cached_rate():
    assert parse_openai_markdown(_OPENAI_MD)["gpt-5.1"] == Rate(1.25, 10.0, 0.125)


def test_openai_missing_table_returns_empty_not_raise():
    assert parse_openai_markdown("# nothing here") == {}


def test_google_parses_standard_rates_ignoring_audio():
    model = parse_google_html(_GOOGLE_HTML, "gemini-2.5-flash-lite")
    assert model is not None
    assert model.standard == Rate(0.10, 0.40, 0.01)
    assert model.long_context is None and model.promo_until is None
    assert model.storage_usd_per_mtok_hour == 1.00


def test_google_parses_the_long_context_tier_despite_a_literal_less_than_in_the_cell():
    model = parse_google_html(_GOOGLE_HTML, "gemini-2.5-pro")
    assert model is not None
    assert model.standard == Rate(1.25, 10.0, 0.125)
    assert model.long_context == Rate(2.5, 15.0, 0.25)
    assert model.storage_usd_per_mtok_hour == 4.50


def test_google_parses_a_promo_window_and_the_post_promo_rate():
    model = parse_google_html(_GOOGLE_HTML, "gemini-3.6-flash")
    assert model is not None
    assert model.standard == Rate(0.75, 3.75, 0.075)
    assert model.promo_until == date(2026, 12, 31)
    assert model.after_promo == Rate(1.5, 7.5, 0.15)


def test_google_cached_not_available_is_none():
    model = parse_google_html(_GOOGLE_HTML, "gemini-no-cache")
    assert model is not None and model.standard.cached is None


def test_google_missing_slug_returns_none_not_raise():
    assert parse_google_html(_GOOGLE_HTML, "gemini-does-not-exist") is None


def test_google_slug_present_but_no_pricing_table_returns_none():
    assert parse_google_html('<h2 id="gemini-x">x</h2><p>no table</p>', "gemini-x") is None


# --- audit(): every entry gets exactly one status --------------------------------------------


def test_every_entry_gets_exactly_one_row():
    rows = _audit()
    assert set(rows) == set(_PRICES["models"])


def test_clean_table_verifies_every_checkable_entry_and_skips_only_with_a_reason():
    rows = _audit()
    statuses = {k: r.status for k, r in rows.items()}
    assert (
        statuses["gemini-2.0-flash"] == "SKIPPED"
        and "retired" in rows["gemini-2.0-flash"].detail[0]
    )
    assert statuses["__local_zero_cost__"] == "SKIPPED" and rows["__local_zero_cost__"].detail
    assert all(s == "VERIFIED" for k, s in statuses.items() if s != "SKIPPED"), statuses


def test_input_mismatch_is_caught():
    rows = _mutated(lambda p: p["models"]["gpt-5.6-terra"].update(input_usd_per_mtok=2.5))
    assert rows["gpt-5.6-terra"].status == "MISMATCH"
    assert "input: ours $2.5 vs vendor $2" in rows["gpt-5.6-terra"].detail[0]


def test_output_mismatch_is_caught_with_both_numbers():
    rows = _mutated(lambda p: p["models"]["claude-opus-5"].update(output_usd_per_mtok=30.0))
    assert rows["claude-opus-5"].status == "MISMATCH"
    assert "ours $30 vs vendor $25" in rows["claude-opus-5"].detail[0]


def test_cached_ratio_mismatch_is_caught_via_the_global_multiplier():
    rows = _mutated(lambda p: p["cache_multipliers"].update(read=0.25))
    assert rows["claude-opus-5"].status == "MISMATCH"
    assert "table assumes 0.25x" in rows["claude-opus-5"].detail[0]


def test_a_model_with_a_different_real_cache_ratio_is_flagged():
    # Claude Fable 5.1's live cache-hit rate is 0.025x input -- a table entry for it under the
    # global 0.1x multiplier would overprice cache hits 4x, and this is exactly how that shows up.
    p = copy.deepcopy(_PRICES)
    p["models"]["claude-fable-5.1"] = {"input_usd_per_mtok": 10.0, "output_usd_per_mtok": 50.0}
    with patch.dict(
        "scripts.check_price_table_vs_vendor.ANTHROPIC_MODEL_NAMES",
        {"claude-fable-5.1": "Claude Fable 5.1"},
    ):
        rows = _audit(p)
    assert rows["claude-fable-5.1"].status == "MISMATCH"
    assert "0.025x" in rows["claude-fable-5.1"].detail[0]


def test_long_context_tier_is_checked_not_silently_skipped():
    rows = _mutated(
        lambda p: p["models"]["gemini-2.5-pro-long-context"].update(output_usd_per_mtok=16.0)
    )
    assert rows["gemini-2.5-pro-long-context"].status == "MISMATCH"


def test_promo_until_mismatch_is_caught():
    rows = _mutated(lambda p: p["models"]["gemini-3.6-flash"].update(promo_until="2026-11-30"))
    assert rows["gemini-3.6-flash"].status == "MISMATCH"
    assert "promo_until" in rows["gemini-3.6-flash"].detail[0]


def test_post_promo_standard_rate_mismatch_is_caught():
    rows = _mutated(
        lambda p: p["models"]["gemini-3.6-flash"]["standard_rate"].update(input_usd_per_mtok=1.6)
    )
    assert rows["gemini-3.6-flash"].status == "MISMATCH"
    assert "standard_rate" in rows["gemini-3.6-flash"].detail[0]


def test_a_promo_the_vendor_lists_but_the_entry_lacks_is_a_mismatch():
    def drop_promo(p):
        e = p["models"]["gemini-3.6-flash"]
        del e["promo_until"], e["standard_rate"]

    assert _mutated(drop_promo)["gemini-3.6-flash"].status == "MISMATCH"


def test_a_one_percent_price_drift_is_caught():
    rows = _mutated(lambda p: p["models"]["claude-opus-5"].update(input_usd_per_mtok=5.05))
    assert rows["claude-opus-5"].status == "MISMATCH"


def test_unmapped_entry_is_refused_not_skipped():
    rows = _mutated(
        lambda p: p["models"].update(
            {"gemini-9.9-new": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0}}
        )
    )
    assert rows["gemini-9.9-new"].status == "UNVERIFIED"
    assert "no vendor mapping" in rows["gemini-9.9-new"].detail[0]


def test_entry_missing_from_the_vendor_page_is_unverified():
    rows = _mutated(
        lambda p: p["models"].update(
            {"gpt-9": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0}}
        )
    )
    assert rows["gpt-9"].status == "UNVERIFIED"


def test_vendor_with_no_published_cached_rate_leaves_the_multiplier_unverified():
    with patch.dict(
        "scripts.check_price_table_vs_vendor.GOOGLE_MODEL_SLUGS",
        {"gemini-no-cache": "gemini-no-cache"},
    ):
        rows = _mutated(
            lambda p: p["models"].update(
                {"gemini-no-cache": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0}}
            )
        )
    assert rows["gemini-no-cache"].status == "UNVERIFIED"
    assert "cached rate" in rows["gemini-no-cache"].detail[0]


def test_an_unfetched_vendor_page_makes_its_entries_unverified_not_ok():
    an, op, g = _vendor()
    rows = {r.key: r for r in audit(_PRICES, an, None, g)}
    assert (
        rows["gpt-5.6-terra"].status == "UNVERIFIED"
        and "not fetched" in rows["gpt-5.6-terra"].detail[0]
    )


def test_explicit_cache_storage_is_reported_as_not_priced():
    rows = _audit()
    assert any("storage" in d and "NOT priced" in d for d in rows["gemini-2.5-pro"].detail)


# --- main(): exit codes ----------------------------------------------------------------------


def _fake_fetch(url):
    return (
        _ANTHROPIC_MD
        if "claude.com" in url
        else _OPENAI_MD
        if "openai.com" in url
        else _GOOGLE_HTML
    )


def test_fetch_raises_fetch_error_not_a_silent_default():
    from scripts.check_price_table_vs_vendor import _fetch

    with pytest.raises(FetchError):
        _fetch("https://example.invalid/pricing")


def test_main_returns_nonzero_when_all_vendor_fetches_fail():
    with patch("scripts.check_price_table_vs_vendor._fetch", side_effect=FetchError("outage")):
        assert main() == 1


def test_main_returns_zero_on_a_clean_table_and_prints_a_row_per_entry(capsys):
    with (
        patch("scripts.check_price_table_vs_vendor._fetch", side_effect=_fake_fetch),
        patch("scripts.check_price_table_vs_vendor.load_gemini_prices", return_value=_PRICES),
    ):
        assert main() == 0
    out = capsys.readouterr().out
    for key in _PRICES["models"]:
        assert key in out
    assert "OK: 9 entries audited (2 SKIPPED, 7 VERIFIED)" in out


def test_main_returns_nonzero_on_a_real_mismatch():
    prices = copy.deepcopy(_PRICES)
    prices["models"]["claude-opus-5"]["output_usd_per_mtok"] = 999.0
    with (
        patch("scripts.check_price_table_vs_vendor._fetch", side_effect=_fake_fetch),
        patch("scripts.check_price_table_vs_vendor.load_gemini_prices", return_value=prices),
    ):
        assert main() == 1


def test_main_returns_nonzero_when_an_entry_cannot_be_verified():
    prices = copy.deepcopy(_PRICES)
    prices["models"]["gemini-9.9-new"] = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0}
    with (
        patch("scripts.check_price_table_vs_vendor._fetch", side_effect=_fake_fetch),
        patch("scripts.check_price_table_vs_vendor.load_gemini_prices", return_value=prices),
    ):
        assert main() == 1
