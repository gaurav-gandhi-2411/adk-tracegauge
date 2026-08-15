"""scripts/check_price_freshness.py — CI freshness gate for adk-tracegauge's price table.

Two INDEPENDENT checks, either of which fails the run (exit 1):

1. Staleness: any model entry in src/adk_tracegauge/data/gemini_prices.json
   whose ``fetched_on`` date is older than
   adk_tracegauge._pricing.STALE_THRESHOLD_DAYS, measured against the date
   this script actually runs.
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


def _check_staleness(
    models: dict[str, dict[str, object]], today: date
) -> list[tuple[str, str, int, str]]:
    stale: list[tuple[str, str, int, str]] = []
    for model_key, entry in models.items():
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
        if age_days > STALE_THRESHOLD_DAYS:
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


def main() -> int:
    prices = load_gemini_prices()
    models: dict[str, dict[str, object]] = prices["models"]
    today = date.today()

    stale = _check_staleness(models, today)
    expiring_soon, already_expired = _check_promo_expiry(models, today)

    if not stale and not expiring_soon and not already_expired:
        print(
            f"OK: all {len(models)} price entries fetched within "
            f"{STALE_THRESHOLD_DAYS} days of {today.isoformat()}, and no "
            f"promotional entry expires within {PROMO_EXPIRY_WARNING_DAYS} days."
        )
        return 0

    if stale:
        print(
            f"STALE PRICE ENTRIES as of {today.isoformat()} "
            f"(threshold {STALE_THRESHOLD_DAYS} days):",
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
