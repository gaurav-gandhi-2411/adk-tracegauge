"""scripts/refresh_price_fetched_on.py -- weekly refresh of the price table's ``fetched_on`` dates.

Why: the release gate (``check_price_freshness.py --max-age-days 30``) and the test suite fail once
any entry's ``fetched_on`` is more than 30 days old, and the only way to move that date was a hand
edit. On 2026-11-03 every entry had been fetched on 2026-10-30, so the gate (and every PR's CI)
would have gone red on 2026-11-30 with no price having changed. This script lets the weekly
``Price table freshness`` workflow do the routine part of the re-verify.

What it does, and only this:
1. Runs the full vendor audit (``check_price_table_vs_vendor.collect``: every vendor pricing and
   deprecation page, fetched live).
2. For every entry whose audit row is VERIFIED -- and whose deprecation row, if it has one, is not
   failing -- sets ``fetched_on`` to today. Nothing else in the entry changes.
3. Only when the WHOLE audit is clean (no fetch error, no MISMATCH, no UNVERIFIED), also updates
   ``vendor_pages.pages`` (SHA-256 prefix of each page as the audit read it) and
   ``vendor_pages.fetched_at_utc``. On a partial audit those stay as they were, because they
   describe pages the whole table was verified against.

Never changes a rate. An entry whose rate differs from the vendor (MISMATCH) or that could not be
checked (UNVERIFIED) keeps its old ``fetched_on`` and is listed in the PR body for a human. Not
refreshed either, by design: SKIPPED entries (synthetic, ``retired: true``, retired and gone from
the page) and any entry whose ``retired_on`` has arrived (the provenance rule in
tests/test_price_table_provenance.py ties a late ``fetched_on`` to ``vendor_page_last_listed``,
which this script does not touch; such entries are exempt from the freshness gate anyway).

The file is edited line by line, not re-serialised (``json.dumps`` would rewrite ``10.00`` as
``10.0`` and every other formatting choice), and the result is checked: parsing the new text must
give exactly the old table with only the intended fields changed, or the script refuses to write.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.check_price_table_vs_vendor import (  # noqa: E402
    ANTHROPIC_DEPRECATIONS_URL,
    ANTHROPIC_MD_URL,
    GOOGLE_DEPRECATIONS_URL,
    GOOGLE_HTML_URL,
    OPENAI_DEPRECATIONS_URL,
    OPENAI_MD_URL,
    AuditRow,
)

from adk_tracegauge._pricing import is_retired  # noqa: E402

PRICES_PATH = _ROOT / "src" / "adk_tracegauge" / "data" / "gemini_prices.json"
FAILING = frozenset({"MISMATCH", "UNVERIFIED"})
HASH_PREFIX_LEN = 16  # the length the table already records

#: The URL each ``vendor_pages.pages`` key is fetched from by the audit. Google's
#: ``ai.google.dev/gemini-api/docs/*`` redirects to ``aistudio.google.com/docs/*`` since 2026-11
#: (see check_price_table_vs_vendor's docstring); the key keeps the URL the entries cite, and the
#: hash is of the page the audit actually read. A key missing here is a refusal, not a guess.
PAGE_FETCH_URL: dict[str, str] = {
    "https://ai.google.dev/gemini-api/docs/pricing": GOOGLE_HTML_URL,
    "https://developers.openai.com/api/docs/pricing.md": OPENAI_MD_URL,
    "https://platform.claude.com/docs/en/about-claude/pricing.md": ANTHROPIC_MD_URL,
    "https://developers.openai.com/api/docs/deprecations.md": OPENAI_DEPRECATIONS_URL,
    "https://platform.claude.com/docs/en/about-claude/model-deprecations.md": (
        ANTHROPIC_DEPRECATIONS_URL
    ),
    "https://ai.google.dev/gemini-api/docs/deprecations": GOOGLE_DEPRECATIONS_URL,
}


@dataclass
class Plan:
    today: date
    refreshed: dict[str, str] = field(default_factory=dict)  # key -> previous fetched_on
    held: list[AuditRow] = field(default_factory=list)  # failing rows, for a human
    errors: list[str] = field(default_factory=list)  # fetch/parse errors
    not_refreshed: dict[str, str] = field(default_factory=dict)  # key -> reason (non-failing)
    page_hashes: dict[str, str] | None = None  # new vendor_pages.pages, or None = unchanged
    page_hash_note: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.refreshed) or self.page_hashes is not None


def page_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:HASH_PREFIX_LEN]


def make_plan(
    prices: dict[str, object],
    rows: list[AuditRow],
    errors: list[str],
    bodies: dict[str, str],
    today: date,
) -> Plan:
    models: dict[str, dict[str, object]] = prices["models"]  # type: ignore[assignment]
    plan = Plan(today=today, errors=list(errors))
    by_key = {r.key: r for r in rows}
    plan.held = [r for r in rows if r.status in FAILING]
    for key, entry in models.items():
        row = by_key.get(key)
        dep = by_key.get(f"{key} (deprecation)")
        if row is None:
            plan.not_refreshed[key] = "no audit row (fails closed)"
        elif row.status != "VERIFIED":
            plan.not_refreshed[key] = row.status
        elif dep is not None and dep.status in FAILING:
            plan.not_refreshed[key] = f"deprecation row {dep.status}"
        elif key.startswith("__") or is_retired(entry, today):
            plan.not_refreshed[key] = "synthetic or retired (exempt from the freshness gate)"
        elif entry.get("fetched_on") == today.isoformat():
            plan.not_refreshed[key] = "already fetched today"
        else:
            plan.refreshed[key] = str(entry.get("fetched_on"))

    if errors or plan.held:
        plan.page_hash_note = "audit not clean, so the page hashes are left as they were"
        return plan
    pages: dict[str, str] = prices["vendor_pages"]["pages"]  # type: ignore[index]
    new: dict[str, str] = {}
    for key in pages:
        url = PAGE_FETCH_URL.get(key)
        if url is None or url not in bodies:
            plan.page_hash_note = (
                f"no fetched body for vendor page {key!r}; hashes left as they were"
            )
            return plan
        new[key] = page_hash(bodies[url])
    if new != pages:
        plan.page_hashes = new
        plan.page_hash_note = "updated (whole audit clean)"
    else:
        plan.page_hash_note = "unchanged (every page hashes as before)"
    return plan
