"""The bundled gemini-3.6-flash / gemini-3.8-flash promotions, pinned to their real dates.

Google's pricing page (raw page read 2026-11-28T11:46:52Z, sha256 prefix bb70827b812f661b) states, for
both models: input "$0.75 through December 31, 2026. $1.50 starting January 1, 2027.", output "$3.75 ...
$7.50 ...", context caching "$0.075 ... $0.15 ...". The table encodes that as ``promo_until`` 2026-12-31
plus ``standard_rate``; the switch is automatic. These tests freeze the clock on each side of the date
(test_pricing.py's promo tests use dates relative to today, so they never touch the bundled entries'
real boundary), and pin what the release gate's promo-expiry check does on the days around it.
"""

from __future__ import annotations

from datetime import date

import pytest
from scripts.check_price_freshness import _check_promo_expiry

from adk_tracegauge import _pricing
from adk_tracegauge._pricing import effective_prices, load_gemini_prices, resolve_model

_PROMO_MODELS = ("gemini-3.6-flash", "gemini-3.8-flash")
_LAST_PROMO_DAY = date(2026, 12, 31)
_FIRST_STANDARD_DAY = date(2027, 1, 1)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch):
    """Freezes both clocks _pricing reads: ``date.today()`` (promo switch, staleness) and ``_today()``
    (routed_from)."""

    def _freeze(day: date) -> None:
        class _FrozenDate(date):
            @classmethod
            def today(cls) -> date:  # type: ignore[override]
                return day

        monkeypatch.setattr(_pricing, "date", _FrozenDate)
        monkeypatch.setattr(_pricing, "_today", lambda: day)

    return _freeze


def test_only_the_two_flash_models_carry_a_promotion() -> None:
    models = load_gemini_prices()["models"]
    assert sorted(k for k, e in models.items() if e.get("promo_until")) == list(_PROMO_MODELS)


@pytest.mark.parametrize("model_key", _PROMO_MODELS)
def test_promotional_rate_through_2026_12_31(frozen, model_key: str) -> None:
    frozen(_LAST_PROMO_DAY)
    resolved = resolve_model(model_key)
    assert resolved is not None
    assert resolved.promo_active is True
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (0.75, 3.75)
    assert effective_prices()["models"][model_key]["cached_input_usd_per_mtok"] == pytest.approx(
        0.075
    )


@pytest.mark.parametrize("model_key", _PROMO_MODELS)
def test_standard_rate_from_2027_01_01(frozen, model_key: str) -> None:
    frozen(_FIRST_STANDARD_DAY)
    resolved = resolve_model(model_key)
    assert resolved is not None
    assert resolved.promo_active is False
    assert resolved.standard_rate_unknown is False
    assert resolved.standard_rate_warning_due is False
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (1.50, 7.50)
    entry = effective_prices()["models"][model_key]
    assert (entry["input_usd_per_mtok"], entry["output_usd_per_mtok"]) == (1.50, 7.50)
    assert entry["cached_input_usd_per_mtok"] == pytest.approx(0.15)


def test_gemini_2_5_pro_routed_to_3_8_flash_follows_the_switch(frozen) -> None:
    """gemini-2.5-pro routes to gemini-3.8-flash from 2026-12-01, so it takes the promotion's end too."""
    frozen(_LAST_PROMO_DAY)
    before = resolve_model("gemini-2.5-pro")
    frozen(_FIRST_STANDARD_DAY)
    after = resolve_model("gemini-2.5-pro")
    assert before is not None and after is not None
    assert (before.input_usd_per_mtok, before.output_usd_per_mtok) == (0.75, 3.75)
    assert (after.input_usd_per_mtok, after.output_usd_per_mtok) == (1.50, 7.50)


@pytest.mark.parametrize(
    ("today", "expiring_soon", "already_expired"),
    [
        # Last day the release gate's promo check passes: 15 days left, outside the 14-day window.
        (date(2026, 12, 16), [], []),
        # From here the gate refuses by design (PROMO_EXPIRY_WARNING_DAYS = 14) ...
        (date(2026, 12, 17), list(_PROMO_MODELS), []),
        (_LAST_PROMO_DAY, list(_PROMO_MODELS), []),
        # ... and keeps refusing after the switch until a table edit retires the two promotions.
        (_FIRST_STANDARD_DAY, [], list(_PROMO_MODELS)),
    ],
)
def test_release_gate_promo_check_around_the_switch(
    today: date, expiring_soon: list[str], already_expired: list[str]
) -> None:
    soon, expired = _check_promo_expiry(load_gemini_prices()["models"], today)
    assert sorted(k for k, *_ in soon) == expiring_soon
    assert sorted(k for k, *_ in expired) == already_expired
