"""scripts/check_price_table_vs_vendor.py — fetches each vendor's own published pricing page and
compares it against EVERY entry in this repo's price table
(src/adk_tracegauge/data/gemini_prices.json), on every rate the table claims: input, output, the
cached-read rate (via the table's single ``cache_multipliers.read``), long-context tiers, and
promo windows. It compares against the one source that matters -- the vendor's current published
rate -- not against a sibling repo's table (which stays green while both drift together, the
failure documented in docs/audit/PHASE8_PLAN.md FF2.2).

REFUSES, does not skip. Every entry ends in exactly one status, and only VERIFIED and an explicit,
reasoned SKIPPED (retired / synthetic) are non-failures:

    VERIFIED    every rate the entry claims matches the vendor page
    SKIPPED     retired (``"retired": true``) or the synthetic local-model entry -- stated in the row
    MISMATCH    the page was parsed and a rate disagrees (input, output, cached, tier or promo)
    UNVERIFIED  the entry could not be checked: not found on the vendor page, no vendor mapping,
                or the vendor publishes no cached rate to check the multiplier against

History (why this was rewritten, 2026-09-20): the previous version verified input and output for 18
of 22 entries. It silently skipped both long-context Gemini tiers, never looked at cached rates
(the table's 0.1x cache multiplier was only a dated manual note), and never checked promo windows.
It also went red on 2026-08-31 with one real mismatch (gpt-5.6-sol) and stayed red for three weekly
runs with nobody acting -- a correct check that reported instead of blocking. The release workflow
now runs this script and refuses to publish while it fails.

Two failure modes are never conflated: a FETCH/PARSE failure (vendor page moved or its structure
changed -- "we do not know if our price is right", always fails) and a MISMATCH (page parsed, rate
differs).

Vendor pages (verified live 2026-09-20):
- Anthropic: markdown export ``.../about-claude/pricing.md``; columns located by header text.
- OpenAI: markdown export ``developers.openai.com/api/docs/pricing.md``; the "Standard pricing
  data" table only (the page repeats every model under Batch/Flex/Fast; reading past the Standard
  table silently checks the wrong tier -- a real bug this script's first version had).
- Google: server-rendered HTML, ``<h2 id="MODEL">`` then a ``pricing-table``; parsed with
  ``html.parser`` (not regex) so ``&lt;=`` and nested markup survive.

0.10.0 verifies more rate classes, each per entry against the raw page:
- the entry's OWN cached-input rate (``cached_input_usd_per_mtok``), not a global multiplier;
- vendor shutdown dates (OpenAI, Anthropic, Gemini deprecation pages), matched by exact model
  name only: an alias-vs-snapshot match is ambiguous, so it is not guessed;
- Gemini audio input / audio cached rates;
- the Google Search grounding row (unit, $/1k, free allowance).

NOT modelled by the table, therefore not verified (stated, not hidden; see README "Known
limitations"): explicit-cache STORAGE fees (Gemini: $1.00-$4.50 per 1M tokens per hour -- not
derivable from per-call usage), cache-WRITE surcharges (Anthropic 1.25x/2x, OpenAI cache writes),
OpenAI/Anthropic long-context tiers, Batch/Flex/Priority tiers.
Maps grounding and Vertex-backend pricing are not covered either.

Zero-cost: plain HTTP GET via stdlib only.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
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
OPENAI_DEPRECATIONS_URL = "https://developers.openai.com/api/docs/deprecations.md"
ANTHROPIC_DEPRECATIONS_URL = (
    "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
)
GOOGLE_DEPRECATIONS_URL = "https://ai.google.dev/gemini-api/docs/deprecations"

#: OpenAI o-series keys: they do not start with "gpt-", so a prefix test alone leaves them
#: unmapped (UNVERIFIED), which is how a new family silently stays unchecked.
OPENAI_O_SERIES = frozenset({"o1", "o3", "o3-mini", "o4-mini"})

#: Entries that cannot be verified against a live page, each with the reason printed in its row.
#: ``retired: true`` entries in the JSON are skipped the same way (reason read from the entry).
SKIP_ENTRIES: dict[str, str] = {
    "gemini-2.0-flash": "retired 2026-06-01, no longer on the vendor page",
    "__local_zero_cost__": "synthetic entry (local models), no vendor price",
}

#: Google `<h2 id>` slugs, one per Gemini model. Explicit on purpose: adding a model to the table
#: without adding it here makes this script report it UNVERIFIED (fail), never silently skip it.
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

#: Table keys that price the ``prompts > 200k tokens`` tier of a base model on Google's page.
GOOGLE_LONG_CONTEXT_KEYS: dict[str, str] = {
    "gemini-2.5-pro-long-context": "gemini-2.5-pro",
    "gemini-3.1-pro-preview-long-context": "gemini-3.1-pro-preview",
}

ANTHROPIC_MODEL_NAMES: dict[str, str] = {
    "claude-opus-5": "Claude Opus 5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-opus-4-8": "Claude Opus 4.8",
}

#: Vendor cached-rate ratio may differ from the table multiplier by this much (absolute) before it
#: is a mismatch: vendors round cached prices to the cent-ish ($0.075 on $0.75 is exactly 0.1).
CACHE_RATIO_TOLERANCE = 0.005
_RATE_EPS = 1e-9


class FetchError(Exception):
    """A vendor page could not be fetched or parsed as expected."""


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
    image_video_split: bool = False  # the input row prices image or video apart from text
    audio_input: float | None = None  # a SEPARATE audio input rate, only when the page lists one
    audio_cached: float | None = None
    grounding: tuple[str, float, str] | None = None  # (unit, usd per 1k, free-allowance count)


@dataclass
class AuditRow:
    key: str
    status: str  # VERIFIED | SKIPPED | MISMATCH | UNVERIFIED
    ours: str
    vendor: str
    detail: list[str] = field(default_factory=list)


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
    image_video_split: bool = False
    audio: float | None = None


_MODALITY_GROUP_RE = re.compile(r"\$[\d.]+\s*\(([^)]*)\)")
_AUDIO_RE = re.compile(r"\$([\d.]+)\s*\(audio\)")
"""A price group labelled exactly ``(audio)``. ``(text / image / video / audio)`` is one rate for
every modality and deliberately does NOT match: there is no separate audio rate to price."""
_GROUNDING_RE = re.compile(
    r"\$([\d.]+)\s*(?:/|per)\s*1,000\s*(grounded prompts?|search (?:queries|requests)|requests)"
)
_ALLOWANCE_RE = re.compile(r"([\d,]+)\s*(?:RPD|free search requests per month)")


def _image_video_split(text: str) -> bool:
    """True when a labelled price group OTHER than the text group names image or video, i.e. the page
    prices image/video input apart from text (``$0.30 (text) $0.60 (image)``). The table (and the
    adapter) price image and video input at the text rate, which is right only while this is False.
    ``$0.30 (text / image / video) $1.00 (audio)`` is False: audio differs, and audio is flagged."""
    groups = [g.lower() for g in _MODALITY_GROUP_RE.findall(text)]
    return any(("image" in g or "video" in g) for g in groups if "text" not in g)


def _parse_cell(text: str) -> _Cell | None:
    split = _image_video_split(text)
    cell = _parse_cell_prices(text)
    audio = _AUDIO_RE.search(text)
    return (
        None
        if cell is None
        else replace(
            cell,
            image_video_split=split,
            audio=float(audio.group(1)) if audio else None,
        )
    )


def _parse_cell_prices(text: str) -> _Cell | None:
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
    grounding = None
    for label, text in parser.rows.items():
        if label.startswith("Grounding with Google Search"):
            g = _GROUNDING_RE.search(text)
            if g:
                unit = "per_grounded_prompt" if "prompt" in g.group(2) else "per_search_query"
                a = _ALLOWANCE_RE.search(text)
                grounding = (unit, float(g.group(1)), a.group(1) if a else "")
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
        image_video_split=i.image_video_split,
        audio_input=i.audio,
        audio_cached=c.audio if c else None,
        grounding=grounding,
    )


def _fmt(rate: Rate | None) -> str:
    if rate is None:
        return "-"
    cached = "n/a" if rate.cached is None else f"{rate.cached:g}"
    return f"in ${rate.input:g} / out ${rate.output:g} / cached ${cached}"


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _RATE_EPS


def _compare(
    key: str, entry: dict[str, object], vendor: Rate, cache_mult: float, tier_note: str = ""
) -> AuditRow:
    ours = Rate(float(entry["input_usd_per_mtok"]), float(entry["output_usd_per_mtok"]))  # type: ignore[arg-type]
    ours_cached_raw = entry.get("cached_input_usd_per_mtok")
    ours_cached = float(ours_cached_raw) if ours_cached_raw is not None else ours.input * cache_mult  # type: ignore[arg-type]
    row = AuditRow(key, "VERIFIED", _fmt(Rate(ours.input, ours.output, ours_cached)), _fmt(vendor))
    if not _same(ours.input, vendor.input):
        row.detail.append(f"input: ours ${ours.input:g} vs vendor ${vendor.input:g}")
    if not _same(ours.output, vendor.output):
        row.detail.append(f"output: ours ${ours.output:g} vs vendor ${vendor.output:g}")
    if row.detail:
        row.status = "MISMATCH"
    if ours_cached_raw is not None and vendor.cached is not None:
        # 0.10.0: the entry's OWN cached rate must equal the vendor's published one exactly.
        if not _same(ours_cached, vendor.cached):
            row.detail.append(f"cached: ours ${ours_cached:g} vs vendor ${vendor.cached:g}")
            row.status = "MISMATCH"
    elif vendor.cached is None:
        row.detail.append(
            "cached rate: vendor publishes none, so the table's cached multiplier "
            "cannot be verified for this entry"
        )
        if row.status == "VERIFIED":
            row.status = "UNVERIFIED"
    elif vendor.input > 0:
        ratio = vendor.cached / vendor.input
        if abs(ratio - cache_mult) > CACHE_RATIO_TOLERANCE:
            row.detail.append(
                f"cached: vendor ${vendor.cached:g} = {ratio:.3f}x its input, table assumes "
                f"{cache_mult:g}x"
            )
            row.status = "MISMATCH"
    if tier_note and row.status == "VERIFIED":
        row.detail.append(tier_note)
    return row


def audit(
    prices: dict[str, object],
    anthropic: dict[str, Rate] | None,
    openai: dict[str, Rate] | None,
    google_html: str | None,
) -> list[AuditRow]:
    """One AuditRow per table entry. ``None`` for a vendor means its page could not be fetched."""
    models: dict[str, dict[str, object]] = prices["models"]  # type: ignore[assignment]
    cache_mult = float(prices["cache_multipliers"]["read"])  # type: ignore[index]
    rows: list[AuditRow] = []
    for key, entry in models.items():
        if key in SKIP_ENTRIES or entry.get("retired"):
            reason = SKIP_ENTRIES.get(key) or f"retired {entry.get('retired_on', '')}".strip()
            rows.append(AuditRow(key, "SKIPPED", "-", "-", [reason]))
            continue
        if not isinstance(entry.get("input_usd_per_mtok"), (int, float)):
            rows.append(AuditRow(key, "UNVERIFIED", "-", "-", ["entry has no input rate"]))
            continue

        if key in ANTHROPIC_MODEL_NAMES:
            if anthropic is None:
                rows.append(AuditRow(key, "UNVERIFIED", "-", "-", ["Anthropic page not fetched"]))
                continue
            found = anthropic.get(ANTHROPIC_MODEL_NAMES[key])
            if found is None:
                rows.append(
                    AuditRow(
                        key,
                        "UNVERIFIED",
                        "-",
                        "-",
                        [f"'{ANTHROPIC_MODEL_NAMES[key]}' not found on Anthropic's page"],
                    )
                )
                continue
            rows.append(_compare(key, entry, found, cache_mult))
        elif key in GOOGLE_MODEL_SLUGS or key in GOOGLE_LONG_CONTEXT_KEYS:
            if google_html is None:
                rows.append(AuditRow(key, "UNVERIFIED", "-", "-", ["Google page not fetched"]))
                continue
            slug = GOOGLE_MODEL_SLUGS.get(key) or GOOGLE_LONG_CONTEXT_KEYS[key]
            model = parse_google_html(google_html, slug)
            if model is None:
                rows.append(
                    AuditRow(
                        key,
                        "UNVERIFIED",
                        "-",
                        "-",
                        [f"slug '{slug}' not found/parseable on Google's page"],
                    )
                )
                continue
            if model.image_video_split:
                rows.append(
                    AuditRow(
                        key,
                        "MISMATCH",
                        "-",
                        "-",
                        [
                            "Google prices image or video input apart from text; the table and the "
                            "adapter price image/video at the text rate, so that is silently wrong -- "
                            "flag those tokens as unpriced (see input_modality_verification)"
                        ],
                    )
                )
                continue
            if key in GOOGLE_LONG_CONTEXT_KEYS:
                if model.long_context is None:
                    rows.append(
                        AuditRow(
                            key,
                            "UNVERIFIED",
                            "-",
                            "-",
                            [f"Google lists no '> 200k tokens' tier for '{slug}'"],
                        )
                    )
                    continue
                long_row = _compare(key, entry, model.long_context, cache_mult)
                _audio_checks(long_row, entry, model)
                _grounding_checks(long_row, entry, model)
                rows.append(long_row)
            else:
                rows.append(_gemini_row(key, entry, model, cache_mult))
        elif key.startswith("gpt-") or key in OPENAI_O_SERIES:
            if openai is None:
                rows.append(AuditRow(key, "UNVERIFIED", "-", "-", ["OpenAI page not fetched"]))
                continue
            found = openai.get(key)
            if found is None:
                rows.append(AuditRow(key, "UNVERIFIED", "-", "-", ["not found on OpenAI's page"]))
                continue
            rows.append(_compare(key, entry, found, cache_mult))
        else:
            rows.append(
                AuditRow(
                    key,
                    "UNVERIFIED",
                    "-",
                    "-",
                    [
                        "no vendor mapping -- add one to this script or mark the entry "
                        "retired; an unmapped entry is refused, not skipped"
                    ],
                )
            )
    return rows


def _audio_checks(row: AuditRow, entry: dict[str, object], model: GoogleModel) -> None:
    """Audio input / audio cached rates (0.10.0)."""
    for field_name, vendor_value, label in (
        ("audio_input_usd_per_mtok", model.audio_input, "audio input"),
        ("audio_cached_input_usd_per_mtok", model.audio_cached, "audio cached input"),
    ):
        ours = entry.get(field_name)
        if vendor_value is None and ours is None:
            continue
        if vendor_value is None:
            row.detail.append(f"{label}: entry carries ${ours} but the page lists no separate rate")
            row.status = "MISMATCH"
        elif ours is None:
            row.detail.append(
                f"{label}: page publishes ${vendor_value:g}/Mtok, the entry carries none (audio "
                "would stay flagged for a model with a published rate)"
            )
            row.status = "MISMATCH"
        elif not _same(float(ours), vendor_value):  # type: ignore[arg-type]
            row.detail.append(f"{label}: ours ${ours} vs vendor ${vendor_value:g}")
            row.status = "MISMATCH"


def _grounding_checks(row: AuditRow, entry: dict[str, object], model: GoogleModel) -> None:
    """The Google Search grounding row: unit, rate and free allowance (0.10.0)."""
    grounding = entry.get("grounding")
    ours_g = grounding.get("google_search") if isinstance(grounding, dict) else None
    if model.grounding is None and ours_g is None:
        return
    if model.grounding is None:
        row.detail.append("entry carries a grounding rate but the page has no parseable row")
        row.status = "MISMATCH"
        return
    unit, per_1k, allowance = model.grounding
    if not isinstance(ours_g, dict):
        row.detail.append(
            f"grounding: page lists ${per_1k:g}/1k ({unit}); the entry carries no grounding block"
        )
        row.status = "MISMATCH"
        return
    if ours_g.get("unit") != unit or not _same(float(ours_g.get("usd_per_1k", -1)), per_1k):
        row.detail.append(
            f"grounding: ours {ours_g.get('unit')} ${ours_g.get('usd_per_1k')}/1k vs vendor "
            f"{unit} ${per_1k:g}/1k"
        )
        row.status = "MISMATCH"
    if allowance and allowance not in str(ours_g.get("free_allowance", "")):
        row.detail.append(
            f"grounding free allowance: page says {allowance}, ours "
            f"'{ours_g.get('free_allowance')}'"
        )
        row.status = "MISMATCH"


def _gemini_row(
    key: str, entry: dict[str, object], model: GoogleModel, cache_mult: float
) -> AuditRow:
    row = _compare(key, entry, model.standard, cache_mult)
    _audio_checks(row, entry, model)
    _grounding_checks(row, entry, model)
    promo_until = entry.get("promo_until")
    std = entry.get("standard_rate")
    if promo_until or std:
        if model.promo_until is None or model.after_promo is None:
            row.detail.append("entry carries a promo window but Google's page shows none")
            row.status = "MISMATCH"
        else:
            if str(promo_until) != model.promo_until.isoformat():
                row.detail.append(
                    f"promo_until: ours {promo_until} vs vendor {model.promo_until.isoformat()}"
                )
                row.status = "MISMATCH"
            std_dict = std if isinstance(std, dict) else {}
            if not (
                _same(float(std_dict.get("input_usd_per_mtok", -1)), model.after_promo.input)
                and _same(float(std_dict.get("output_usd_per_mtok", -1)), model.after_promo.output)
            ):
                row.detail.append(
                    f"standard_rate: ours {std_dict} vs vendor after promo "
                    f"in ${model.after_promo.input:g} / out ${model.after_promo.output:g}"
                )
                row.status = "MISMATCH"
            ours_std_cached = std_dict.get("cached_input_usd_per_mtok")
            if (
                ours_std_cached is not None
                and model.after_promo.cached is not None
                and not _same(float(ours_std_cached), model.after_promo.cached)
            ):
                row.detail.append(
                    f"standard_rate cached: ours ${ours_std_cached} vs vendor after promo "
                    f"${model.after_promo.cached:g}"
                )
                row.status = "MISMATCH"
    elif model.promo_until is not None:
        row.detail.append(
            f"Google lists a promo through {model.promo_until.isoformat()} the entry does not carry"
        )
        row.status = "MISMATCH"
    if model.storage_usd_per_mtok_hour is not None and row.status == "VERIFIED":
        row.detail.append(
            f"explicit-cache storage ${model.storage_usd_per_mtok_hour:g}/Mtok/hour is NOT priced"
        )
    return row


class _HtmlRows(HTMLParser):
    """Every table row of a page as a list of cell texts (used for the Gemini deprecations page)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cells: list[str] = []
        self._buf: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "th"):
            self._in_cell, self._buf = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            self._cells.append(re.sub(r"\s+", " ", "".join(self._buf)).strip())
            self._in_cell = False
        elif tag == "tr":
            if self._cells:
                self.rows.append(self._cells)
            self._cells = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._buf.append(data)


def _any_date(text: str) -> date | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_openai_deprecations(md: str) -> dict[str, date]:
    """{exact model name: shutdown date} from every table whose header starts with 'Shutdown date'
    (the first column). Every backticked name in the model cell counts, aliases and snapshots alike.
    Empty dict (a parse failure) if no such table exists."""
    found: dict[str, date] = {}
    in_table = False
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            in_table = False
            continue
        # a cell may hold several names separated by an ESCAPED pipe (`a` \\| `b`)
        cells = _table_cells(line.replace("\\|", ","))
        if cells[0].lower().startswith("shutdown date"):
            in_table = True
            continue
        if not in_table or len(cells) < 2:
            continue
        when = _any_date(cells[0])
        if when is None:
            continue
        for name in re.findall(r"`([^`]+)`", cells[1]):
            found.setdefault(name, when)
    return found


def parse_anthropic_deprecations(md: str) -> dict[str, date]:
    """{exact API model name: retirement date} for models whose status is not 'Active', from the
    'Model status' table, plus the dated rows of the deprecation-history tables."""
    found: dict[str, date] = {}
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _table_cells(line)
        if len(cells) >= 2 and (when := _any_date(cells[0])) is not None:
            for name in re.findall(r"`([^`]+)`", cells[1]):
                found.setdefault(name, when)
    return found


def parse_google_deprecations(html: str) -> dict[str, date | None]:
    """{exact model name: shutdown date, or None for 'No shutdown date announced'}, read from the
    'Shutdown date' column of every deprecations table (never the release-date column)."""
    parser = _HtmlRows()
    parser.feed(html)
    found: dict[str, date | None] = {}
    col: int | None = None
    for cells in parser.rows:
        if cells and cells[0] == "Model":
            col = next((i for i, c in enumerate(cells) if c.lower().startswith("shutdown")), None)
            continue
        if col is None or len(cells) <= col or not re.fullmatch(r"gemini-[\w.\-]+", cells[0]):
            continue
        found.setdefault(cells[0], _any_date(cells[col]))
    return found


def audit_deprecations(
    prices: dict[str, object],
    openai: dict[str, date | None] | None,
    anthropic: dict[str, date | None] | None,
    google: dict[str, date | None] | None,
    today: date | None = None,
) -> list[AuditRow]:
    """One row per entry whose vendor page lists a shutdown date for its EXACT key, or whose entry
    records a ``deprecation`` the page no longer lists. A vendor-listed date must be carried by the
    entry (MISMATCH otherwise), and a passed shutdown on a non-retired entry is a MISMATCH."""
    today = today or date.today()
    models: dict[str, dict[str, object]] = prices["models"]  # type: ignore[assignment]
    rows: list[AuditRow] = []
    for key, entry in models.items():
        if key in SKIP_ENTRIES or entry.get("retired"):
            continue
        if key in ANTHROPIC_MODEL_NAMES:
            listing, vendor_name = anthropic, "Anthropic"
        elif key.startswith("gemini-"):
            listing, vendor_name = google, "Google"
        elif key.startswith("gpt-") or key in OPENAI_O_SERIES:
            listing, vendor_name = openai, "OpenAI"
        else:
            continue
        if listing is None:
            rows.append(
                AuditRow(
                    f"{key} (deprecation)",
                    "UNVERIFIED",
                    "-",
                    "-",
                    [f"{vendor_name} page not fetched"],
                )
            )
            continue
        listed = listing.get(key)
        dep = entry.get("deprecation")
        ours = dep.get("shutdown_on") if isinstance(dep, dict) else None
        if listed is None and ours is None:
            continue  # not listed, or listed with no shutdown announced, and we record none
        row = AuditRow(
            f"{key} (deprecation)",
            "VERIFIED",
            str(ours),
            listed.isoformat() if listed else "-",
        )
        if listed is None:
            row.status = "MISMATCH"
            row.detail.append(
                f"entry records shutdown {ours} but the {vendor_name} page lists none for '{key}'"
            )
        elif ours != listed.isoformat():
            row.status = "MISMATCH"
            row.detail.append(f"shutdown_on: ours {ours} vs vendor {listed.isoformat()}")
        if listed is not None and listed < today:
            row.status = "MISMATCH"
            row.detail.append(
                f"vendor shutdown {listed.isoformat()} has passed and the entry is not marked "
                "retired -- mark it retired (retired/retired_on/retired_source)"
            )
        if row.status == "VERIFIED":
            row.detail.append(f"shutdown {ours}, listed by {vendor_name}")
        rows.append(row)
    return rows


def _print_table(rows: list[AuditRow]) -> None:
    print(f"{'entry':<38} {'status':<11} detail")
    print("-" * 110)
    for r in rows:
        first = r.detail[0] if r.detail else ""
        print(f"{r.key:<38} {r.status:<11} {first}")
        for extra in r.detail[1:]:
            print(f"{'':<50} {extra}")
        if r.status in ("MISMATCH",) or (r.status == "VERIFIED" and r.vendor != "-"):
            print(f"{'':<50} ours:   {r.ours}")
            print(f"{'':<50} vendor: {r.vendor}")


def main() -> int:
    prices = load_gemini_prices()
    errors: list[str] = []

    def fetch_parse(url: str, parse, what: str):  # type: ignore[no-untyped-def]
        try:
            body = _fetch(url)
            parsed = parse(body)
            if not parsed:
                raise FetchError(f"{url}: {what} table not found")
            return parsed
        except FetchError as e:
            errors.append(str(e))
            return None

    anthropic = fetch_parse(ANTHROPIC_MD_URL, parse_anthropic_markdown, "'## Model pricing'")
    openai = fetch_parse(OPENAI_MD_URL, parse_openai_markdown, "'Standard pricing data'")
    try:
        google_html: str | None = _fetch(GOOGLE_HTML_URL)
    except FetchError as e:
        errors.append(str(e))
        google_html = None

    rows = audit(prices, anthropic, openai, google_html)

    def deprecations(url: str, parse, what: str):  # type: ignore[no-untyped-def]
        try:
            body = _fetch(url)
            parsed = parse(body)
            if not parsed:
                raise FetchError(f"{url}: {what} not found")
            return parsed
        except FetchError as e:
            errors.append(str(e))
            return None

    rows += audit_deprecations(
        prices,
        deprecations(OPENAI_DEPRECATIONS_URL, parse_openai_deprecations, "'Shutdown date' tables"),
        deprecations(
            ANTHROPIC_DEPRECATIONS_URL, parse_anthropic_deprecations, "deprecation history"
        ),
        deprecations(GOOGLE_DEPRECATIONS_URL, parse_google_deprecations, "model rows"),
    )
    _print_table(rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    failing = [r for r in rows if r.status in ("MISMATCH", "UNVERIFIED")]
    if not errors and not failing:
        print(
            f"\nOK: {len(rows)} entries audited ({summary}); every claimed rate matches the vendor."
        )
        return 0

    print(f"\nFAILED: {len(rows)} entries audited ({summary}).", file=sys.stderr)
    for msg in errors:
        print(f"COULD NOT FETCH/PARSE: {msg}", file=sys.stderr)
    for r in failing:
        print(f"{r.status}: {r.key}: " + "; ".join(r.detail), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
