"""tests/test_check_price_table_vs_vendor.py — GG1: deterministic tests for
scripts/check_price_table_vs_vendor.py against recorded HTML/markdown
fixtures (small representative excerpts of what each vendor's page
actually returned when fetched live this session, 2026-08-16) -- no live
network calls in CI, per this repo's existing test-suite convention.

The multi-tier OpenAI trap this file's fixture reproduces is a REAL bug
this script's own first version had: OpenAI's page repeats every model
name across FOUR tables (Standard/Batch/Flex/Fast pricing data), and an
earlier, unbounded version of parse_openai_markdown silently kept whichever
table it read LAST, not Standard -- caught live against the real page, not
invented for this test file.
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.check_price_table_vs_vendor import (
    FetchError,
    main,
    parse_anthropic_markdown,
    parse_google_html,
    parse_openai_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures — small, real excerpts (not synthetic) of each vendor's actual
# response body, recorded live this session.
# ---------------------------------------------------------------------------

_ANTHROPIC_MD_FIXTURE = """---
title: Pricing
---

## Model pricing

The following table shows pricing for all Claude models:

| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Sonnet 5 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock and Google Cloud](https://example.invalid)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |

## Next section

Unrelated content that must not be read as part of the pricing table.

| Model | Some Other Column |
| --- | --- |
| Claude Opus 5 | $999 / MTok |
"""

_OPENAI_MD_FIXTURE = """# Pricing

### Standard pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 | $4.00 | $0.40 | $5.00 | $18.00 |
| gpt-5.1 | $1.25 | $0.125 | - | $10.00 | - | - | - | - |

### Batch pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-terra | $1.00 | $0.10 | $1.25 | $6.00 | $2.00 | $0.20 | $2.50 | $9.00 |

### Fast pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-terra | $4.00 | $0.40 | $5.00 | $24.00 | $8.00 | $0.80 | $10.00 | $36.00 |
"""

_GOOGLE_HTML_FIXTURE = """
<div class="heading-group">
    <h2 id="gemini-2.5-flash-lite" data-text="Gemini 2.5 Flash-Lite" tabindex="-1">Gemini 2.5 Flash-Lite</h2>
    <em><code translate="no" dir="ltr">gemini-2.5-flash-lite</code></em>
</div>
<p>Our smallest and most cost effective model.</p>
<section><h3 id="standard_15" data-text="Standard" tabindex="-1">Standard</h3><table class="pricing-table">
  <thead><tr><th></th><th scope="col">Free Tier</th><th scope="col">Paid Tier, per 1M tokens in USD</th></tr></thead>
  <tbody>
    <tr><td>Input price (text, image, video)</td><td>Free of charge</td><td>$0.10 (text / image / video)<br>$0.30 (audio)</td></tr>
    <tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$0.40</td></tr>
  </tbody>
</table></section>
"""


# ---------------------------------------------------------------------------
# parse_anthropic_markdown
# ---------------------------------------------------------------------------


def test_anthropic_parses_expected_models_and_rates():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert result["Claude Opus 5"] == (5.0, 25.0)
    assert result["Claude Sonnet 5"] == (2.0, 10.0)


def test_anthropic_strips_markdown_link_from_retired_model_name():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert "Claude Opus 4.1" in result
    assert result["Claude Opus 4.1"] == (15.0, 75.0)


def test_anthropic_output_column_is_not_the_cache_hit_column():
    # Regression test for the exact off-by-one this script's first version
    # had: cells[4] (Cache Hits & Refreshes) was read as Output instead of
    # cells[5] (Output Tokens) -- $0.50 (cache-hit) vs $25 (real output)
    # for Claude Opus 5 would have been silently swapped.
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert result["Claude Opus 5"][1] == 25.0
    assert result["Claude Opus 5"][1] != 0.50


def test_anthropic_does_not_read_past_the_model_pricing_section():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    # The "Next section" table's $999 must never be picked up.
    assert result["Claude Opus 5"][0] != 999.0


def test_anthropic_missing_table_header_returns_empty_not_raise():
    assert parse_anthropic_markdown("no pricing table here at all") == {}


# ---------------------------------------------------------------------------
# parse_openai_markdown
# ---------------------------------------------------------------------------


def test_openai_parses_standard_tier_only_not_batch_or_fast():
    # Regression test for the real bug this script's first version had
    # live: gpt-5.6-terra appears in 4 tables (Standard/Batch/Flex/Fast),
    # sharing the same model name -- an unbounded scan silently kept
    # whichever table it read LAST (Fast: $4.00/$24.00), not Standard
    # ($2.00/$12.00). This fixture reproduces exactly that shape.
    result = parse_openai_markdown(_OPENAI_MD_FIXTURE)
    assert result["gpt-5.6-terra"] == (2.0, 12.0)
    assert result["gpt-5.6-terra"] != (1.0, 6.0)  # Batch tier
    assert result["gpt-5.6-terra"] != (4.0, 24.0)  # Fast tier


def test_openai_model_keys_match_directly_no_normalization():
    result = parse_openai_markdown(_OPENAI_MD_FIXTURE)
    assert result["gpt-5.1"] == (1.25, 10.00)


def test_openai_missing_table_header_returns_empty_not_raise():
    assert parse_openai_markdown("no pricing table here at all") == {}


# ---------------------------------------------------------------------------
# parse_google_html
# ---------------------------------------------------------------------------


def test_google_parses_input_and_output_from_standard_tier():
    result = parse_google_html(_GOOGLE_HTML_FIXTURE, "gemini-2.5-flash-lite")
    assert result == (0.10, 0.40)


def test_google_missing_slug_returns_none_not_raise():
    assert parse_google_html(_GOOGLE_HTML_FIXTURE, "gemini-nonexistent-model") is None


def test_google_slug_present_but_no_pricing_table_returns_none():
    html = '<h2 id="gemini-x">Gemini X</h2><p>no table here</p>'
    assert parse_google_html(html, "gemini-x") is None


# ---------------------------------------------------------------------------
# main() — fetch-failure and mismatch behavior, per GG1.3's "two distinct
# failure modes, never conflated" requirement
# ---------------------------------------------------------------------------


def test_fetch_raises_fetch_error_not_a_silent_default():
    # urlopen wraps connection-level failures in urllib.error.URLError
    # (confirmed live this session against a genuinely nonexistent domain,
    # not assumed) -- the mock reproduces that real shape, not a bare
    # OSError urlopen never actually raises directly.
    import urllib.error

    from scripts.check_price_table_vs_vendor import _fetch

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("network unreachable"),
    ):
        try:
            _fetch("https://example.invalid/pricing")
            raise AssertionError("expected FetchError")
        except FetchError:
            pass


def test_main_returns_nonzero_when_all_vendor_fetches_fail():
    # A total outage must fail the run loudly, never report OK as if
    # nothing needed checking (GG1.3's core requirement).
    with patch(
        "scripts.check_price_table_vs_vendor._fetch",
        side_effect=FetchError("simulated total outage"),
    ):
        assert main() == 1


def test_main_returns_zero_when_every_checked_entry_matches_and_all_fetches_succeed():
    with (
        patch(
            "scripts.check_price_table_vs_vendor._fetch",
            side_effect=lambda url: (
                _ANTHROPIC_MD_FIXTURE
                if "claude.com" in url
                else _OPENAI_MD_FIXTURE
                if "openai.com" in url
                else _GOOGLE_HTML_FIXTURE
            ),
        ),
        patch(
            "scripts.check_price_table_vs_vendor.load_gemini_prices",
            return_value={
                "models": {
                    "claude-opus-5": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 25.0},
                    "gpt-5.6-terra": {"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 12.0},
                    "gemini-2.5-flash-lite": {
                        "input_usd_per_mtok": 0.10,
                        "output_usd_per_mtok": 0.40,
                    },
                }
            },
        ),
    ):
        assert main() == 0


def test_main_returns_nonzero_on_a_real_mismatch():
    with (
        patch(
            "scripts.check_price_table_vs_vendor._fetch",
            side_effect=lambda url: (
                _ANTHROPIC_MD_FIXTURE
                if "claude.com" in url
                else _OPENAI_MD_FIXTURE
                if "openai.com" in url
                else _GOOGLE_HTML_FIXTURE
            ),
        ),
        patch(
            "scripts.check_price_table_vs_vendor.load_gemini_prices",
            return_value={
                "models": {
                    # Deliberately wrong -- vendor's real rate is $5/$25.
                    "claude-opus-5": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 999.0},
                }
            },
        ),
    ):
        assert main() == 1
