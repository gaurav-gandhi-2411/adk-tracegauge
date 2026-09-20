"""The bundled price table must have been re-verified against the vendors recently.

The weekly vendor check went red on 2026-08-24 (a real gpt-5.6-sol price cut) and stayed red for
four scheduled runs without anyone acting: a signal that reported instead of blocking. This test
is the blocking half for the *date*: it fails the suite -- and therefore every PR and the release
workflow -- when any price entry's ``fetched_on`` is more than RELEASE_MAX_AGE_DAYS old. A date
cannot detect a price change (only the vendor audit can); it forces the audit to have been re-run
and a passing result recorded. To clear a red run: run
``python scripts/check_price_table_vs_vendor.py`` and, only if it exits 0, update ``fetched_on``.
See scripts/check_price_freshness.py for how the 30-day limit was chosen from measured cadence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from scripts.check_price_freshness import RELEASE_MAX_AGE_DAYS, _check_staleness

from adk_tracegauge._pricing import load_gemini_prices

_TODAY = date(2026, 9, 20)


def _entries(**fetched_on: str) -> dict[str, dict[str, object]]:
    return {
        k: {"fetched_on": v, "source_url": f"https://vendor.invalid/{k}"}
        for k, v in fetched_on.items()
    }


def test_bundled_price_table_was_verified_within_the_release_max_age():
    today = datetime.now(UTC).date()

    stale = _check_staleness(load_gemini_prices()["models"], today, RELEASE_MAX_AGE_DAYS)

    assert not stale, (
        f"price entries not re-verified within {RELEASE_MAX_AGE_DAYS} days as of {today}: "
        + ", ".join(f"{k} (fetched_on={f}, {age}d old)" for k, f, age, _ in sorted(stale))
        + ". Run scripts/check_price_table_vs_vendor.py; if it exits 0, update fetched_on."
    )


def test_release_max_age_is_stricter_than_the_runtime_warning_threshold():
    from adk_tracegauge._pricing import STALE_THRESHOLD_DAYS

    assert RELEASE_MAX_AGE_DAYS < STALE_THRESHOLD_DAYS


def test_an_entry_exactly_at_the_limit_passes_and_one_day_over_fails():
    at_limit = _entries(a="2026-08-21")  # 30 days before _TODAY
    over = _entries(b="2026-08-20")  # 31 days before _TODAY

    assert _check_staleness(at_limit, _TODAY, 30) == []
    assert [row[0] for row in _check_staleness(over, _TODAY, 30)] == ["b"]


def test_a_stale_entry_is_reported_with_its_age_and_source():
    ((key, fetched_on, age, source),) = _check_staleness(_entries(old="2026-01-01"), _TODAY, 30)

    assert (key, fetched_on, age) == ("old", "2026-01-01", 262)
    assert source == "https://vendor.invalid/old"


def test_a_missing_or_malformed_fetch_date_fails_closed():
    models = {"no_date": {}, "bad_date": {"fetched_on": "last tuesday"}}

    stale = {row[0]: row for row in _check_staleness(models, _TODAY, 30)}

    assert set(stale) == {"no_date", "bad_date"}
    assert all(row[2] == -1 for row in stale.values())


def test_retired_and_synthetic_entries_are_exempt():
    models = {
        "gemini-2.0-flash": {"fetched_on": "2020-01-01", "retired": True},
        "__local_zero_cost__": {"fetched_on": "2020-01-01"},
    }

    assert _check_staleness(models, _TODAY, 30) == []


def test_the_default_threshold_is_unchanged_for_existing_callers():
    from adk_tracegauge._pricing import STALE_THRESHOLD_DAYS

    aged = _entries(x="2026-08-01")  # 50 days: over 30, under the 90-day runtime default

    assert _check_staleness(aged, _TODAY) == []  # no max_age_days argument -> 90-day default
    assert _check_staleness(aged, _TODAY, RELEASE_MAX_AGE_DAYS) != []
    assert STALE_THRESHOLD_DAYS == 90
