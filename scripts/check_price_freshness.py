"""scripts/check_price_freshness.py — CI freshness gate for the Gemini price table.

Fails (exit 1) when any model entry in src/adk_tracegauge/data/gemini_prices.json
has a ``fetched_on`` date older than adk_tracegauge._pricing.STALE_THRESHOLD_DAYS,
measured against the date this script actually runs.

Pure date arithmetic against the bundled JSON file -- no network calls, no paid
API calls, zero cost. Staleness is deliberately checked against "today" as of
the CI runner's own clock (see _pricing.STALE_THRESHOLD_DAYS docstring for why
that is the correct reference point, not a date baked into the library).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._pricing import STALE_THRESHOLD_DAYS, load_gemini_prices  # noqa: E402


def main() -> int:
    prices = load_gemini_prices()
    models: dict[str, dict[str, object]] = prices["models"]
    today = date.today()

    stale: list[tuple[str, str, int]] = []
    for model_key, entry in models.items():
        fetched_on = str(entry.get("fetched_on") or "")
        try:
            fetched = date.fromisoformat(fetched_on)
        except ValueError:
            # An unparseable/missing date is itself a staleness signal --
            # fail closed rather than skip the entry silently.
            stale.append((model_key, fetched_on or "<missing>", -1))
            continue
        age_days = (today - fetched).days
        if age_days > STALE_THRESHOLD_DAYS:
            stale.append((model_key, fetched_on, age_days))

    if not stale:
        print(
            f"OK: all {len(models)} price entries fetched within "
            f"{STALE_THRESHOLD_DAYS} days of {today.isoformat()}."
        )
        return 0

    print(
        f"STALE PRICE ENTRIES as of {today.isoformat()} (threshold {STALE_THRESHOLD_DAYS} days):",
        file=sys.stderr,
    )
    for model_key, fetched_on, age_days in sorted(stale):
        age_desc = "unparseable/missing date" if age_days < 0 else f"{age_days} days old"
        print(f"  - {model_key}: fetched_on={fetched_on} ({age_desc})", file=sys.stderr)
    print(
        "\nRe-verify each flagged model against "
        "https://ai.google.dev/gemini-api/docs/pricing (and the model's own "
        "source_url, if different) and update fetched_on + source_url in "
        "src/adk_tracegauge/data/gemini_prices.json.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
