"""The release gate's promotion rule (GG decision 2026-11-28).

A promotion ending within PROMO_EXPIRY_WARNING_DAYS refuses UNLESS the entry carries a
vendor-verified, fresh post-promotion rate effective the day after the promotion ends
(scripts/check_price_freshness.py ``_verified_successor``). Everything else is unchanged: an
already-expired promotion still refuses, and staleness is a separate check. Every date is passed in;
nothing reads the clock.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest
from scripts.check_price_freshness import (
    RELEASE_MAX_AGE_DAYS,
    _check_promo_expiry,
    _check_staleness,
    _waived_promos,
    main,
)

from adk_tracegauge import _pricing
from adk_tracegauge._pricing import PROMO_EXPIRY_WARNING_DAYS, load_gemini_prices

PROMO_UNTIL = date(2026, 12, 31)
TODAY = date(2026, 12, 20)  # 11 days left: inside the window
GEMINI_PROMOS = ["gemini-3.6-flash", "gemini-3.8-flash"]


def _entry(**successor: object) -> dict[str, object]:
    std: dict[str, object] = {
        "input_usd_per_mtok": 1.5,
        "output_usd_per_mtok": 7.5,
        "effective_from": "2027-01-01",
        "verified_on": "2026-12-14",
    }
    std.update(successor)
    return {
        "input_usd_per_mtok": 0.75,
        "output_usd_per_mtok": 3.75,
        "source_url": "https://example.invalid/pricing",
        "fetched_on": "2026-12-14",
        "promo_until": PROMO_UNTIL.isoformat(),
        "standard_rate": {k: v for k, v in std.items() if v is not None},
    }


def _soon(entry: dict[str, object], today: date = TODAY) -> list[str]:
    soon, expired = _check_promo_expiry({"m": entry}, today, RELEASE_MAX_AGE_DAYS)
    assert expired == []
    return [k for k, *_ in soon]


def test_a_verified_fresh_successor_rate_passes():
    assert _soon(_entry()) == []
    waived = _waived_promos({"m": _entry()}, TODAY, RELEASE_MAX_AGE_DAYS)
    assert [(k, d) for k, d, _ in waived] == [("m", 11)]


def test_a_promo_without_any_successor_rate_still_refuses():
    entry = _entry()
    del entry["standard_rate"]
    assert _soon(entry) == ["m"]


@pytest.mark.parametrize(
    "missing", ["effective_from", "verified_on", "input_usd_per_mtok", "output_usd_per_mtok"]
)
def test_a_successor_rate_missing_a_required_field_still_refuses(missing: str):
    assert _soon(_entry(**{missing: None})) == ["m"]


def test_a_stale_successor_rate_refuses():
    stale = TODAY - timedelta(days=RELEASE_MAX_AGE_DAYS + 1)
    assert _soon(_entry(verified_on=stale.isoformat())) == ["m"]
    edge = TODAY - timedelta(days=RELEASE_MAX_AGE_DAYS)  # exactly the limit is still fresh
    assert _soon(_entry(verified_on=edge.isoformat())) == []


@pytest.mark.parametrize("effective_from", ["2026-12-31", "2027-01-02", "2027-02-01", "not-a-date"])
def test_a_successor_not_effective_the_day_after_the_promotion_refuses(effective_from: str):
    assert _soon(_entry(effective_from=effective_from)) == ["m"]


def test_a_verified_on_in_the_future_or_unparseable_refuses():
    assert _soon(_entry(verified_on="2026-12-21")) == ["m"]
    assert _soon(_entry(verified_on="yesterday")) == ["m"]


def test_a_non_numeric_successor_rate_refuses():
    assert _soon(_entry(input_usd_per_mtok="1.5")) == ["m"]
    assert _soon(_entry(output_usd_per_mtok=True)) == ["m"]


def test_an_already_expired_promotion_still_refuses_with_a_verified_successor():
    soon, expired = _check_promo_expiry(
        {"m": _entry(verified_on="2026-12-31")}, date(2027, 1, 1), RELEASE_MAX_AGE_DAYS
    )
    assert soon == [] and [k for k, *_ in expired] == ["m"]


def test_outside_the_window_nothing_changes():
    early = PROMO_UNTIL - timedelta(days=PROMO_EXPIRY_WARNING_DAYS + 1)
    entry = _entry()
    del entry["standard_rate"]
    assert _soon(entry, early) == []


# --- the bundled table, frozen clock ------------------------------------------------------------


def _bundled() -> dict[str, dict[str, object]]:
    return load_gemini_prices()["models"]


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def test_bundled_gemini_promotions_carry_a_verified_successor():
    models = _bundled()
    for key in GEMINI_PROMOS:
        std = models[key]["standard_rate"]
        assert std["effective_from"] == "2027-01-01"
        assert date.fromisoformat(std["verified_on"]) <= date.fromisoformat(
            models[key]["fetched_on"]
        )


@pytest.mark.parametrize("today", _days(date(2026, 12, 17), date(2026, 12, 31)))
def test_release_gate_passes_12_17_to_12_31_with_the_weekly_refresh(today: date):
    """The weekly refresh (Mondays) moves fetched_on and standard_rate.verified_on together on a
    VERIFIED row. Model the last refresh on or before `today`, from the bundled table's own dates."""
    models = copy.deepcopy(_bundled())
    last_monday = today - timedelta(days=today.weekday())
    for entry in models.values():
        entry["fetched_on"] = max(str(entry["fetched_on"]), last_monday.isoformat())
        std = entry.get("standard_rate")
        if isinstance(std, dict) and "verified_on" in std:
            std["verified_on"] = max(str(std["verified_on"]), last_monday.isoformat())
    soon, expired = _check_promo_expiry(models, today, RELEASE_MAX_AGE_DAYS)
    assert soon == [] and expired == []
    waived = [k for k, *_ in _waived_promos(models, today, RELEASE_MAX_AGE_DAYS)]
    assert waived == GEMINI_PROMOS
    assert _check_staleness(models, today, RELEASE_MAX_AGE_DAYS) == []


def test_without_a_refresh_the_bundled_successor_goes_stale_after_30_days():
    """No refresh at all: verified_on (and fetched_on) age out on the same day, and the old
    refusal returns. The rule does not let an unrefreshed table through."""
    models = _bundled()
    verified = max(
        date.fromisoformat(models[k]["standard_rate"]["verified_on"]) for k in GEMINI_PROMOS
    )
    last_ok = verified + timedelta(days=RELEASE_MAX_AGE_DAYS)
    first_bad = last_ok + timedelta(days=1)
    if first_bad > PROMO_UNTIL:
        pytest.skip("the bundled verified_on is recent enough to last the whole window")
    assert _check_promo_expiry(models, last_ok, RELEASE_MAX_AGE_DAYS)[0] == []
    soon = [k for k, *_ in _check_promo_expiry(models, first_bad, RELEASE_MAX_AGE_DAYS)[0]]
    assert soon == GEMINI_PROMOS


def test_main_prints_the_waiver_and_passes(monkeypatch: pytest.MonkeyPatch, capsys):
    import scripts.check_price_freshness as gate

    class _Frozen(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return date(2026, 12, 17)

    models = copy.deepcopy(_bundled())
    for entry in models.values():
        entry["fetched_on"] = "2026-12-14"
        if "verified_on" in entry.get("standard_rate", {}):
            entry["standard_rate"]["verified_on"] = "2026-12-14"
    monkeypatch.setattr(gate, "date", _Frozen)
    monkeypatch.setattr(gate, "load_gemini_prices", lambda: {"models": models})
    monkeypatch.setattr(_pricing, "date", _Frozen)  # is_retired reads the same clock
    assert main(["--max-age-days", str(RELEASE_MAX_AGE_DAYS)]) == 0
    out = capsys.readouterr().out
    assert "gemini-3.6-flash carries a vendor-verified successor rate in $1.5 / out $7.5" in out
    assert "effective 2027-01-01, verified 2026-12-14" in out
