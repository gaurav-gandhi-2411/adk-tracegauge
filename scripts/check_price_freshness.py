"""scripts/check_price_freshness.py — CI freshness gate for adk-tracegauge's price table.

Two INDEPENDENT checks, either of which fails the run (exit 1):

1. Staleness: any NON-RETIRED model entry in
   src/adk_tracegauge/data/gemini_prices.json whose ``fetched_on`` date is
   older than adk_tracegauge._pricing.STALE_THRESHOLD_DAYS, measured
   against the date this script actually runs. An entry with ``"retired":
   true`` is exempt (AN3 fix): the vendor no longer changes pricing for a
   model that can't be resolved by anyone, so staleness doesn't apply --
   ported from tracegauge's sibling script, which already had this
   exemption when this repo's copy did not. That gap is exactly how
   ``gemini-2.0-flash`` sat with a normal-looking ``fetched_on`` for weeks
   after being fully removed from Google's own model catalog -- this check
   would have stayed green the whole time, since date arithmetic alone
   cannot tell "recently re-verified" from "recently re-verified against a
   vendor page that no longer lists the model at all."
2. Promo expiry (Phase 3 B2 2.4): any entry with a ``promo_until`` date that
   is within adk_tracegauge._pricing.PROMO_EXPIRY_WARNING_DAYS of "today", OR
   already past. Reported as two DISTINCT conditions ("expiring soon" vs.
   "already expired, standard_rate should now be effective") -- an entry can
   fail this check without ever being stale (fetched_on can be recent even
   while promo_until is imminent), so this is not folded into check 1.

Pure date arithmetic against the bundled JSON file -- no network calls, no paid
API calls, zero cost. Both checks are deliberately evaluated against "today" as
of the CI runner's own clock (see _pricing.STALE_THRESHOLD_DAYS's docstring for
why that is the correct reference point, not a date baked into the library).

Covers every entry regardless of vendor (Gemini, Claude, GPT, and the
synthetic local-model entry -- Phase 2 W3 broadened the table beyond Gemini);
the per-model guidance below points at each entry's own recorded
``source_url`` rather than a single hardcoded vendor page, since that page
now differs per model.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._pricing import (  # noqa: E402
    PROMO_EXPIRY_WARNING_DAYS,
    STALE_THRESHOLD_DAYS,
    load_gemini_prices,
)

#: Maximum age, in days, of any price entry's ``fetched_on`` for the RELEASE gate and the test
#: suite (the weekly workflow keeps the looser runtime STALE_THRESHOLD_DAYS=90 default).
#: Chosen from measured vendor cadence, 2026-09-20: 20 verifiable entries were audited against the
#: three vendors' live pages; between the 2026-08-14 fetch and today (37 days) exactly one entry
#: changed (gpt-5.6-sol, $5/$30 -> $4/$20, first flagged 2026-08-24, i.e. within 10 days). That is
#: ~1.4e-3 changes per entry-day, so a table left unverified for 30 days is expected to contain
#: ~0.8 wrong entries (P(at least one) ~ 55%); at 90 days, ~2.4 (91%). One observed event is a thin
#: basis for a rate, so this is a ceiling on how long the table may go without a passing vendor
#: audit, not a claim that 30 days is safe -- the vendor audit (release-blocking) is what detects
#: drift; this date only forces the audit to have been re-run. 30 = monthly, and tolerates three
#: missed weekly runs (the actual failure lasted four).
RELEASE_MAX_AGE_DAYS = 30


def _check_staleness(
    models: dict[str, dict[str, object]],
    today: date,
    max_age_days: int = STALE_THRESHOLD_DAYS,
) -> list[tuple[str, str, int, str]]:
    stale: list[tuple[str, str, int, str]] = []
    for model_key, entry in models.items():
        if model_key.startswith("__"):
            # Synthetic entries (e.g. the zero-cost local-model entry) have no vendor page, so
            # a fetch date on them cannot go stale in any meaningful sense.
            continue
        if entry.get("retired"):
            # Retired entries are exempt by design: the vendor no longer
            # changes pricing for a model that can't be resolved/priced by
            # anyone, so "is this stale" doesn't apply -- the entry is kept
            # only to price historical sessions recorded before retirement,
            # at the last-published rate. Ported from tracegauge's sibling
            # check_price_freshness.py, which already had this exemption;
            # this repo's copy did not until this fix (AN3) -- the gap that
            # let gemini-2.0-flash sit with a normal-looking fetched_on
            # despite being fully removed from Google's own model catalog.
            continue
        fetched_on = str(entry.get("fetched_on") or "")
        source_url = str(entry.get("source_url") or "<no source_url recorded>")
        try:
            fetched = date.fromisoformat(fetched_on)
        except ValueError:
            # An unparseable/missing date is itself a staleness signal --
            # fail closed rather than skip the entry silently.
            stale.append((model_key, fetched_on or "<missing>", -1, source_url))
            continue
        age_days = (today - fetched).days
        if age_days > max_age_days:
            stale.append((model_key, fetched_on, age_days, source_url))
    return stale


def _check_promo_expiry(
    models: dict[str, dict[str, object]], today: date
) -> tuple[list[tuple[str, str, int | None, str]], list[tuple[str, str, int | None, str]]]:
    """Returns (expiring_soon, already_expired) -- two distinct lists, per
    Phase 3 B2 2.4's requirement to report both conditions distinctly. The
    days-left element is None only for an unparseable promo_until (distinct
    from a real 0-or-negative day count, which is a legitimate value, not a
    sentinel)."""
    expiring_soon: list[tuple[str, str, int | None, str]] = []
    already_expired: list[tuple[str, str, int | None, str]] = []
    for model_key, entry in models.items():
        promo_until = entry.get("promo_until")
        if not promo_until:
            continue
        source_url = str(entry.get("source_url") or "<no source_url recorded>")
        try:
            promo_until_date = date.fromisoformat(str(promo_until))
        except ValueError:
            # An unparseable promo_until is itself worth flagging -- treat
            # it as already expired (fail closed) rather than skip it.
            already_expired.append((model_key, str(promo_until), None, source_url))
            continue
        days_left = (promo_until_date - today).days
        if days_left < 0:
            already_expired.append((model_key, str(promo_until), days_left, source_url))
        elif days_left <= PROMO_EXPIRY_WARNING_DAYS:
            expiring_soon.append((model_key, str(promo_until), days_left, source_url))
    return expiring_soon, already_expired


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Price table freshness gate.")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=STALE_THRESHOLD_DAYS,
        help=(
            f"fail any entry fetched more than this many days ago (default "
            f"{STALE_THRESHOLD_DAYS}; the release workflow passes {RELEASE_MAX_AGE_DAYS})"
        ),
    )
    max_age_days = parser.parse_args(argv).max_age_days
    prices = load_gemini_prices()
    models: dict[str, dict[str, object]] = prices["models"]
    today = date.today()

    stale = _check_staleness(models, today, max_age_days)
    expiring_soon, already_expired = _check_promo_expiry(models, today)

    if not stale and not expiring_soon and not already_expired:
        checked = sum(1 for entry in models.values() if not entry.get("retired"))
        print(
            f"OK: all {checked} non-retired price entries fetched within "
            f"{max_age_days} days of {today.isoformat()}, and no "
            f"promotional entry expires within {PROMO_EXPIRY_WARNING_DAYS} days."
        )
        return 0

    if stale:
        print(
            f"STALE PRICE ENTRIES as of {today.isoformat()} (threshold {max_age_days} days):",
            file=sys.stderr,
        )
        for model_key, fetched_on, age_days, source_url in sorted(stale):
            age_desc = "unparseable/missing date" if age_days < 0 else f"{age_days} days old"
            print(
                f"  - {model_key}: fetched_on={fetched_on} ({age_desc}) -- re-verify "
                f"against {source_url}",
                file=sys.stderr,
            )
        print(
            "\nUpdate fetched_on + source_url (and the price itself, if it "
            "changed) for each flagged entry in "
            "src/adk_tracegauge/data/gemini_prices.json.",
            file=sys.stderr,
        )

    if expiring_soon:
        if stale:
            print(file=sys.stderr)
        print(
            f"PROMOTIONAL ENTRIES EXPIRING SOON as of {today.isoformat()} "
            f"(within {PROMO_EXPIRY_WARNING_DAYS} days):",
            file=sys.stderr,
        )
        for model_key, promo_until, days_left, source_url in sorted(expiring_soon):
            print(
                f"  - {model_key}: promo_until={promo_until} ({days_left} day(s) "
                f"left) -- confirm the entry's standard_rate against {source_url} "
                "before it takes effect.",
                file=sys.stderr,
            )

    if already_expired:
        if stale or expiring_soon:
            print(file=sys.stderr)
        print(
            f"PROMOTIONAL ENTRIES ALREADY EXPIRED as of {today.isoformat()}:",
            file=sys.stderr,
        )
        for model_key, promo_until, days_left, source_url in sorted(
            already_expired, key=lambda row: row[0]
        ):
            detail = "unparseable date" if days_left is None else f"{-days_left} day(s) ago"
            print(
                f"  - {model_key}: promo_until={promo_until} (expired {detail}) -- "
                "standard_rate should now be in effect; verify it's actually being "
                f"applied and re-confirm the number against {source_url}.",
                file=sys.stderr,
            )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
