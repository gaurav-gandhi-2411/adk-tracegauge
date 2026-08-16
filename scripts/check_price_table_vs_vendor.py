"""scripts/check_price_table_vs_vendor.py — GG1: fetches each vendor's own
published pricing page and compares it against every entry in this repo's
own price table (src/adk_tracegauge/data/gemini_prices.json). Replaces the
FF4.3 cross-repo comparison plan (never implemented) -- that plan compared
two repos' tables against EACH OTHER, which stays green even if both drift
into staleness together (the failure that actually occurred, see
docs/audit/PHASE8_PLAN.md FF2.2). This checks against the one source that
actually matters: the vendor's own current published rate.

TWO DISTINCT FAILURE MODES, never conflated (GG1.3):
1. FETCH/PARSE FAILURE -- the vendor page couldn't be reached, or its
   structure has changed enough that this script's parser can't find the
   expected table/rows. This means "we don't know if our price is right",
   NOT "our price is right". Reported as `retryable_errors` and always
   fails the run -- a page that moved or a scrape that breaks must fail
   visibly, never silently pass as if nothing needed checking.
2. MISMATCH -- the page was fetched and parsed successfully, and a rate we
   found disagrees with our table. Reported as `mismatches`.

VENDOR FEASIBILITY (verified live this session, VERIFIED not assumed):
- Anthropic: real, purpose-built markdown export at
  https://platform.claude.com/docs/en/about-claude/pricing.md -- a clean
  "| Model | Base Input Tokens | ... | Output Tokens |" table. Most robust
  of the three.
- OpenAI: real, purpose-built markdown export at
  https://developers.openai.com/api/docs/pricing.md -- a clean
  "| Model | Short context input | ... | Short context output | ... |"
  table under a "Standard pricing data" heading. Model names already match
  this repo's lowercase-hyphen keys directly (e.g. "gpt-5.1").
- Google: NO markdown export (the .md URL just re-serves the same HTML
  page) -- but the HTML itself has a clean, real (server-rendered, not
  JS-only) `<table class="pricing-table">` per model section, each
  preceded by `<h2 id="MODEL-SLUG">`. Parsed via a narrow, explicit
  per-model slug map (GOOGLE_MODEL_SLUGS below) rather than an algorithmic
  slug-guessing transform, since the slug format (e.g.
  "gemini-2-5-flash-lite" for "gemini-2.5-flash-lite") isn't a simple
  reversible rule and guessing it wrong would silently check the wrong
  model.

Only NON-deprecated/retired entries are checked -- an entry the vendor no
longer lists on its live page (e.g. gemini-2.0-flash, shut down 2026-06-01,
kept here only for pricing historical sessions -- see that entry's own
note in the JSON) cannot be verified against a page that doesn't list it;
see SKIP_ENTRIES.

Zero-cost, no paid API calls -- plain HTTP GET via stdlib `urllib.request`
only, no new dependency added for this.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._pricing import load_gemini_prices  # noqa: E402

_USER_AGENT = "Mozilla/5.0 (compatible; adk-tracegauge-price-vendor-check/1.0)"
_TIMEOUT_SECONDS = 20

ANTHROPIC_MD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
GOOGLE_HTML_URL = "https://ai.google.dev/gemini-api/docs/pricing"
OPENAI_MD_URL = "https://developers.openai.com/api/docs/pricing.md"

#: Deprecated/shut-down entries the vendor's current page no longer lists --
#: cannot be verified against a live page, same reasoning the existing
#: staleness guard already applies to "retired" entries in principle.
SKIP_ENTRIES = frozenset({"gemini-2.0-flash", "__local_zero_cost__"})

#: Google's `<h2 id="...">` slugs were verified live this session to match
#: this repo's own model keys EXACTLY (dots, not hyphens -- e.g.
#: `id="gemini-2.5-flash-lite"`, not a hyphenated transform of it). Listed
#: explicitly anyway, one entry per Gemini model this table currently
#: prices, rather than silently assuming every future model key will keep
#: matching Google's id format -- adding a new Gemini model requires
#: adding it here too, deliberately, not automatically.
GOOGLE_MODEL_SLUGS: dict[str, str] = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.6-flash": "gemini-3.6-flash",
    "gemini-3.7-flash": "gemini-3.7-flash",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
}

#: Anthropic's page displays "Claude Opus 5"; our table key is
#: "claude-opus-5" -- mapped explicitly rather than algorithmically
#: normalized, since e.g. "Claude Opus 4.8" -> "claude-opus-4-8" needs the
#: dot-to-hyphen rule applied only in the version segment.
ANTHROPIC_MODEL_NAMES: dict[str, str] = {
    "claude-opus-5": "Claude Opus 5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-opus-4-8": "Claude Opus 4.8",
}


class FetchError(Exception):
    """A vendor page could not be fetched or parsed as expected -- see
    module docstring's "TWO DISTINCT FAILURE MODES"."""


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"{url}: HTTP {status}")
            body: bytes = resp.read()
            return body.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise FetchError(f"{url}: {e}") from e
    except TimeoutError as e:
        raise FetchError(f"{url}: timed out after {_TIMEOUT_SECONDS}s") from e


def parse_anthropic_markdown(md: str) -> dict[str, tuple[float, float]]:
    """{display_name: (input_usd_per_mtok, output_usd_per_mtok)} from the
    '## Model pricing' table's 'Base Input Tokens'/'Output Tokens' columns.
    Returns an empty dict (never raises) if the table header isn't found --
    the caller (main()) treats that as a parse failure for every model
    this vendor is expected to cover."""
    # Columns: Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes
    # | Cache Hits & Refreshes | Output Tokens -- Output is cells[5], NOT
    # cells[4] (that's the cache-hit column) -- confirmed by live column
    # count this session, not assumed from the header text alone.
    result: dict[str, tuple[float, float]] = {}
    idx = md.find("## Model pricing")
    if idx == -1:
        return result
    for line in md[idx:].splitlines():
        if line.startswith("#") and "Model pricing" not in line:
            break  # next section -- stop, do not silently read past our table
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        raw_name = cells[0]
        if raw_name.lower() == "model" or set(raw_name) <= {"-", " ", ":"}:
            continue
        # Strip a trailing markdown link, e.g. "Claude Opus 4.1 ([retired,
        # ...](url))" -> "Claude Opus 4.1" -- only the display name matters.
        display_name = re.sub(r"\s*\(\[.*", "", raw_name).strip()
        input_match = re.search(r"\$([\d.]+)\s*/\s*MTok", cells[1])
        output_match = re.search(r"\$([\d.]+)\s*/\s*MTok", cells[5])
        if not input_match or not output_match:
            continue
        result[display_name] = (float(input_match.group(1)), float(output_match.group(1)))
    return result


def parse_openai_markdown(md: str) -> dict[str, tuple[float, float]]:
    """{model_key: (input_usd_per_mtok, output_usd_per_mtok)} from the
    'Standard pricing data' table's 'Short context input'/'Short context
    output' columns -- model keys here already match this repo's own keys
    directly, no name mapping needed."""
    # This page has FOUR pricing tiers (Standard, Batch, Flex, Fast), each
    # its own "### <Tier> pricing data" table, all sharing the same model
    # names -- confirmed live this session (a real bug caught here: an
    # earlier, unbounded version of this parser silently kept overwriting
    # each model's entry with whichever tier's table it read LAST, since
    # every tier reuses the same model names). Only the Standard tier
    # matches this repo's own table (which never models tiered pricing);
    # the scan MUST stop at the next "#"-heading, never read past it.
    result: dict[str, tuple[float, float]] = {}
    idx = md.find("Standard pricing data")
    if idx == -1:
        return result
    for line in md[idx:].splitlines():
        if line.startswith("#") and "Standard pricing data" not in line:
            break
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        model_key = cells[0]
        if model_key.lower() == "model" or set(model_key) <= {"-", " ", ":"}:
            continue
        input_match = re.match(r"\$([\d.]+)", cells[1])
        output_match = re.match(r"\$([\d.]+)", cells[4])
        if not input_match or not output_match:
            continue
        result[model_key] = (float(input_match.group(1)), float(output_match.group(1)))
    return result


def parse_google_html(html: str, slug: str) -> tuple[float, float] | None:
    """Parses the Standard-tier pricing-table immediately following
    `<h2 id="{slug}">` -- returns None (a parse failure, not a value) if
    the slug isn't found or the expected row labels aren't present."""
    marker = f'id="{slug}"'
    idx = html.find(marker)
    if idx == -1:
        return None
    section = html[idx : idx + 4000]
    table_idx = section.find('<table class="pricing-table">')
    if table_idx == -1:
        return None
    table = section[table_idx : table_idx + 2500]
    input_match = re.search(r"Input price[^<]*</td>\s*<td>[^<]*</td>\s*<td>\$([\d.]+)", table)
    output_match = re.search(r"Output price[^<]*</td>\s*<td>[^<]*</td>\s*<td>\$([\d.]+)", table)
    if not input_match or not output_match:
        return None
    return float(input_match.group(1)), float(output_match.group(1))


def main() -> int:
    prices = load_gemini_prices()
    models: dict[str, dict[str, object]] = prices["models"]

    retryable_errors: list[str] = []
    unmapped: list[str] = []
    mismatches: list[tuple[str, float, float, float, float]] = []
    verified = 0

    try:
        anthropic_md = _fetch(ANTHROPIC_MD_URL)
        anthropic_table = parse_anthropic_markdown(anthropic_md)
        if not anthropic_table:
            raise FetchError(f"{ANTHROPIC_MD_URL}: '## Model pricing' table not found")
    except FetchError as e:
        retryable_errors.append(str(e))
        anthropic_table = None

    try:
        openai_md = _fetch(OPENAI_MD_URL)
        openai_table = parse_openai_markdown(openai_md)
        if not openai_table:
            raise FetchError(f"{OPENAI_MD_URL}: 'Standard pricing data' table not found")
    except FetchError as e:
        retryable_errors.append(str(e))
        openai_table = None

    try:
        google_html = _fetch(GOOGLE_HTML_URL)
    except FetchError as e:
        retryable_errors.append(str(e))
        google_html = None

    for model_key, entry in models.items():
        if model_key in SKIP_ENTRIES:
            continue
        our_input = entry.get("input_usd_per_mtok")
        our_output = entry.get("output_usd_per_mtok")
        if not isinstance(our_input, (int, float)) or not isinstance(our_output, (int, float)):
            continue

        if model_key in ANTHROPIC_MODEL_NAMES:
            if anthropic_table is None:
                continue  # already counted as a retryable_error above
            display_name = ANTHROPIC_MODEL_NAMES[model_key]
            fetched = anthropic_table.get(display_name)
            if fetched is None:
                unmapped.append(f"{model_key}: '{display_name}' not found on Anthropic's page")
                continue
            verified += 1
            if (float(our_input), float(our_output)) != fetched:
                mismatches.append((model_key, float(our_input), float(our_output), *fetched))
        elif model_key in GOOGLE_MODEL_SLUGS:
            if google_html is None:
                continue
            fetched = parse_google_html(google_html, GOOGLE_MODEL_SLUGS[model_key])
            if fetched is None:
                unmapped.append(
                    f"{model_key}: slug '{GOOGLE_MODEL_SLUGS[model_key]}' not found/parseable on Google's page"
                )
                continue
            verified += 1
            if (float(our_input), float(our_output)) != fetched:
                mismatches.append((model_key, float(our_input), float(our_output), *fetched))
        elif openai_table is not None and model_key in openai_table:
            fetched = openai_table[model_key]
            verified += 1
            if (float(our_input), float(our_output)) != fetched:
                mismatches.append((model_key, float(our_input), float(our_output), *fetched))
        elif model_key.startswith("gpt-"):
            if openai_table is None:
                continue
            unmapped.append(f"{model_key}: not found on OpenAI's page")
        # Any other model_key has no vendor mapping at all (yet) -- not an
        # error, just outside this script's current coverage; extend
        # ANTHROPIC_MODEL_NAMES/GOOGLE_MODEL_SLUGS when a new model is added.

    if not retryable_errors and not unmapped and not mismatches:
        print(f"OK: {verified} price entries verified against their vendor's own current page.")
        return 0

    if retryable_errors:
        print(
            "COULD NOT VERIFY (fetch/parse failure -- distinct from a mismatch, see module docstring):",
            file=sys.stderr,
        )
        for msg in retryable_errors:
            print(f"  - {msg}", file=sys.stderr)
    if unmapped:
        if retryable_errors:
            print(file=sys.stderr)
        print(
            "COULD NOT VERIFY (entry not found on vendor's page -- needs manual review):",
            file=sys.stderr,
        )
        for msg in unmapped:
            print(f"  - {msg}", file=sys.stderr)
    if mismatches:
        if retryable_errors or unmapped:
            print(file=sys.stderr)
        print(
            "PRICE MISMATCH (our table disagrees with the vendor's own current page):",
            file=sys.stderr,
        )
        for model_key, our_in, our_out, vendor_in, vendor_out in mismatches:
            print(
                f"  - {model_key}: ours=${our_in}/${our_out} per MTok, "
                f"vendor=${vendor_in}/${vendor_out} per MTok",
                file=sys.stderr,
            )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
