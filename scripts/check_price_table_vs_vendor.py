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
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
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


@dataclass(frozen=True)
class Rate:
    """USD per million tokens as the vendor publishes them (``cached`` None if not published)."""

    input: float
    output: float
    cached: float | None = None


@dataclass(frozen=True)
class GoogleModel:
    standard: Rate
    long_context: Rate | None = None  # the ``prompts > 200k tokens`` tier, if the page has one
    promo_until: date | None = None  # standard rate is a promo through this date...
    after_promo: Rate | None = None  # ...and this rate applies afterwards
    storage_usd_per_mtok_hour: float | None = None  # explicit-cache storage; parsed, never priced


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


def _money(cell: str) -> float | None:
    m = re.search(r"\$\s*([\d,]*\.?\d+)", cell)
    return float(m.group(1).replace(",", "")) if m else None


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_col(header: list[str], *needles: str) -> int | None:
    for i, cell in enumerate(header):
        low = cell.lower()
        if all(n in low for n in needles):
            return i
    return None


def parse_anthropic_markdown(md: str) -> dict[str, Rate]:
    """{display_name: Rate} from the '## Model pricing' table; columns are located by header text
    ("Base input tokens", "Cache hits and refreshes", "Output tokens"), never by position.
    Empty dict (never raises) if the table is not found."""
    idx = md.find("## Model pricing")
    if idx == -1:
        return {}
    result: dict[str, Rate] = {}
    cols: tuple[int, int | None, int] | None = None
    for line in md[idx:].splitlines():
        if line.startswith("#") and "Model pricing" not in line:
            break  # next section -- never read past our table
        if not line.strip().startswith("|"):
            continue
        cells = _table_cells(line)
        if cols is None:
            i_in = _find_col(cells, "base input")
            i_out = _find_col(cells, "output")
            if i_in is not None and i_out is not None:
                cols = (i_in, _find_col(cells, "cache", "hit"), i_out)
            continue
        if set(cells[0]) <= {"-", " ", ":"} or len(cells) <= max(c for c in cols if c is not None):
            continue
        name = re.sub(r"\s*\(\[.*", "", cells[0]).strip()
        rate_in, rate_out = _money(cells[cols[0]]), _money(cells[cols[2]])
        if rate_in is None or rate_out is None:
            continue
        cached = _money(cells[cols[1]]) if cols[1] is not None else None
        result[name] = Rate(rate_in, rate_out, cached)
    return result


def parse_openai_markdown(md: str) -> dict[str, Rate]:
    """{model_key: Rate} from the 'Standard pricing data' table's SHORT-context columns only.
    Empty dict (never raises) if the table is not found."""
    idx = md.find("Standard pricing data")
    if idx == -1:
        return {}
    result: dict[str, Rate] = {}
    cols: tuple[int, int | None, int] | None = None
    for line in md[idx:].splitlines():
        if line.startswith("#") and "Standard pricing data" not in line:
            break  # Batch/Flex/Fast tables reuse the same model names -- never read them
        if not line.strip().startswith("|"):
            continue
        cells = _table_cells(line)
        if cols is None:
            i_in = _find_col(cells, "short context input")
            i_out = _find_col(cells, "short context output")
            if i_in is not None and i_out is not None:
                cols = (i_in, _find_col(cells, "short context cached"), i_out)
            continue
        if set(cells[0]) <= {"-", " ", ":"} or len(cells) <= max(c for c in cols if c is not None):
            continue
        rate_in, rate_out = _money(cells[cols[0]]), _money(cells[cols[2]])
        if rate_in is None or rate_out is None:
            continue
        cached = _money(cells[cols[1]]) if cols[1] is not None else None
        result[cells[0]] = Rate(rate_in, rate_out, cached)
    return result


class _GoogleTable(HTMLParser):
    """Collects {row label: paid-tier cell text} from the first ``pricing-table`` after a marker."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: dict[str, str] = {}
        self._in_table = False
        self._done = False
        self._cells: list[str] = []
        self._buf: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "table" and ("class", "pricing-table") in attrs:
            self._in_table = True
        elif self._in_table and tag in ("td", "th"):
            self._in_cell, self._buf = True, []
        elif self._in_table and tag == "br" and self._in_cell:
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._done or not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cells.append(re.sub(r"\s+", " ", "".join(self._buf)).strip())
            self._in_cell = False
        elif tag == "tr":
            if len(self._cells) >= 3 and self._cells[0]:
                self.rows.setdefault(self._cells[0], self._cells[-1])
            self._cells = []
        elif tag == "table":
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_cell and not self._done:
            self._buf.append(data)


_PROMO_RE = re.compile(
    r"\$([\d.]+) through ([A-Z][a-z]+ \d{1,2}, \d{4})\.?\s*\$([\d.]+) starting", re.S
)
_STORAGE_RE = re.compile(r"\$([\d.]+)\s*/\s*1,000,000 tokens per hour")


def _parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%B %d, %Y").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class _Cell:
    standard: float
    long_context: float | None = None
    after_promo: float | None = None
    promo_until: date | None = None
    storage: float | None = None


def _parse_cell(text: str) -> _Cell | None:
    storage_m = _STORAGE_RE.search(text)
    storage = float(storage_m.group(1)) if storage_m else None
    text = _STORAGE_RE.sub(" ", text)
    promo = _PROMO_RE.search(text)
    if promo:
        return _Cell(
            float(promo.group(1)),
            after_promo=float(promo.group(3)),
            promo_until=_parse_date(promo.group(2)),
            storage=storage,
        )
    prices = [float(p) for p in re.findall(r"\$([\d.]+)", text)]
    if not prices:
        return None
    # "$1.25, prompts <= 200k tokens $2.50, prompts > 200k tokens" -> standard then long tier.
    if re.search(r"prompts\s*>\s*200k", text) and len(prices) >= 2:
        return _Cell(prices[0], long_context=prices[1], storage=storage)
    return _Cell(prices[0], storage=storage)  # first price is text/image/video; audio is ignored


def parse_google_html(html: str, slug: str) -> GoogleModel | None:
    """Parses the Standard-tier pricing table after ``<h2 id="{slug}">``. None (a parse failure,
    not a value) if the slug or the Input/Output rows are missing."""
    idx = html.find(f'id="{slug}"')
    if idx == -1:
        return None
    parser = _GoogleTable()
    parser.feed(html[idx : idx + 12000])
    cells: dict[str, _Cell] = {}
    for label, text in parser.rows.items():
        for want in ("Input price", "Output price", "Context caching price"):
            if label.startswith(want):
                cell = _parse_cell(text)
                if cell is not None:
                    cells[want] = cell
    if "Input price" not in cells or "Output price" not in cells:
        return None
    i, o, c = cells["Input price"], cells["Output price"], cells.get("Context caching price")
    return GoogleModel(
        standard=Rate(i.standard, o.standard, c.standard if c else None),
        long_context=(
            Rate(i.long_context, o.long_context, c.long_context if c else None)
            if i.long_context is not None and o.long_context is not None
            else None
        ),
        promo_until=i.promo_until,
        after_promo=(
            Rate(i.after_promo, o.after_promo, c.after_promo if c else None)
            if i.after_promo is not None and o.after_promo is not None
            else None
        ),
        storage_usd_per_mtok_hour=c.storage if c else None,
    )


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
            if (float(our_input), float(our_output)) != (fetched.input, fetched.output):
                mismatches.append(
                    (model_key, float(our_input), float(our_output), fetched.input, fetched.output)
                )
        elif model_key in GOOGLE_MODEL_SLUGS:
            if google_html is None:
                continue
            google = parse_google_html(google_html, GOOGLE_MODEL_SLUGS[model_key])
            if google is None:
                unmapped.append(
                    f"{model_key}: slug '{GOOGLE_MODEL_SLUGS[model_key]}' not found/parseable on Google's page"
                )
                continue
            verified += 1
            fetched = google.standard
            if (float(our_input), float(our_output)) != (fetched.input, fetched.output):
                mismatches.append(
                    (model_key, float(our_input), float(our_output), fetched.input, fetched.output)
                )
        elif openai_table is not None and model_key in openai_table:
            fetched = openai_table[model_key]
            verified += 1
            if (float(our_input), float(our_output)) != (fetched.input, fetched.output):
                mismatches.append(
                    (model_key, float(our_input), float(our_output), fetched.input, fetched.output)
                )
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
