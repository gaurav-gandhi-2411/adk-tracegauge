"""A model whose announced ``retired_on`` date has arrived is retired without a table edit.

Why: OpenAI's own deprecations page lists gpt-4.1-nano, o1, o3-mini and o4-mini as shut down on
2026-10-23. From that day its pricing page drops them, so the weekly vendor check would report
every one UNVERIFIED (and the deprecation audit a MISMATCH) and go red BY DESIGN until someone hand-
marked them ``"retired": true``. They now carry ``retired_on`` + ``vendor_page_last_listed`` today,
and ``is_retired`` makes the date itself the switch: the check stays green, the freshness gate stops
counting their ``fetched_on``, and the report says the figure is the last rate the vendor published.

Every date below is passed in (or monkeypatched), never read from the clock.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from scripts.check_price_freshness import _check_staleness
from scripts.check_price_table_vs_vendor import Rate, audit, audit_deprecations

from adk_tracegauge import _pricing
from adk_tracegauge._cli import EXIT_PASS, main
from adk_tracegauge._pricing import is_retired, load_gemini_prices
from adk_tracegauge._report import unverifiable_pricing
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import build_snapshot, write_snapshot

_SHUTDOWN = date(2026, 10, 23)
_RETIRING = ("gpt-4.1-nano", "o1", "o3-mini", "o4-mini")
_BEFORE = date(2026, 10, 22)
_AFTER = date(2026, 10, 24)


# --- the predicate ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "today", "expected"),
    [
        ({}, date(2030, 1, 1), False),
        ({"retired": True}, date(2020, 1, 1), True),  # the manual flag still works, no date needed
        ({"retired_on": "2026-10-23"}, _BEFORE, False),
        ({"retired_on": "2026-10-23"}, _SHUTDOWN, True),  # the day itself counts
        ({"retired_on": "2026-10-23"}, _AFTER, True),
        ({"retired_on": "not-a-date"}, _AFTER, False),  # unparseable stays under full verification
        ({"retired_on": ""}, _AFTER, False),
    ],
)
def test_is_retired(entry, today, expected):
    assert is_retired(entry, today) is expected


def test_the_four_openai_models_are_retired_by_date_and_not_by_flag():
    models = load_gemini_prices()["models"]
    for key in _RETIRING:
        entry = models[key]
        assert "retired" not in entry, f"{key}: a hand flag would retire it today, while it is live"
        assert entry["retired_on"] == entry["deprecation"]["shutdown_on"] == "2026-10-23"
        # provenance: when the vendor page last showed the rate we carry
        assert date.fromisoformat(entry["vendor_page_last_listed"]) <= _SHUTDOWN
        assert is_retired(entry, _BEFORE) is False
        assert is_retired(entry, _SHUTDOWN) is True


# --- the weekly vendor check ----------------------------------------------------------------------


def _prices(**extra):
    models = {
        "o1": {
            "input_usd_per_mtok": 15.0,
            "output_usd_per_mtok": 60.0,
            "cached_input_usd_per_mtok": 7.5,
            "deprecation": {"shutdown_on": "2026-10-23"},
            "retired_on": "2026-10-23",
            "vendor_page_last_listed": "2026-10-07",
        }
    }
    models["o1"].update(extra)
    return {"cache_multipliers": {"read": 0.1}, "models": models}


_O1_ON_PAGE = {"o1": Rate(15.0, 60.0, 7.5)}


def _rate_row(prices, openai, today):
    return {r.key: r for r in audit(prices, None, openai, None, today=today)}["o1"]


def test_before_the_shutdown_a_missing_row_is_still_a_failure():
    # Not yet retired: the page dropping a live model early is exactly what the check must catch.
    assert _rate_row(_prices(), {}, _BEFORE).status == "UNVERIFIED"


def test_after_the_shutdown_a_row_the_page_dropped_is_skipped_not_a_failure():
    row = _rate_row(_prices(), {}, _AFTER)

    assert row.status == "SKIPPED"
    assert "retired 2026-10-23" in row.detail[0]
    assert "last listed 2026-10-07" in row.detail[0]
    assert "priced at last published rate" in row.detail[0]


def test_without_retired_on_the_same_state_fails_which_is_the_defect_this_fixes():
    prices = _prices()
    del prices["models"]["o1"]["retired_on"]

    assert _rate_row(prices, {}, _AFTER).status == "UNVERIFIED"


def test_a_postponed_shutdown_is_not_hidden_while_the_page_still_lists_the_model():
    assert _rate_row(_prices(), _O1_ON_PAGE, _AFTER).status == "VERIFIED"  # verified as usual
    repriced = {"o1": Rate(16.0, 60.0, 7.5)}
    assert _rate_row(_prices(), repriced, _AFTER).status == "MISMATCH"


def _dep_row(prices, listing, today):
    rows = {r.key: r for r in audit_deprecations(prices, listing, {}, {}, today=today)}
    return rows.get("o1 (deprecation)")


def test_deprecation_audit_accepts_the_vendor_dropping_a_retired_model():
    row = _dep_row(_prices(), {}, _AFTER)

    assert row is not None and row.status == "VERIFIED"
    assert "no longer lists" in row.detail[0]


def test_deprecation_audit_still_fails_when_the_vendor_moved_the_date():
    assert _dep_row(_prices(), {"o1": date(2026, 12, 15)}, _AFTER).status == "MISMATCH"


def test_deprecation_audit_accepts_a_passed_date_the_page_still_shows_once_retired_on_is_set():
    # Vendors keep shut-down models on the deprecations page; with retired_on set that is not drift.
    row = _dep_row(_prices(), {"o1": _SHUTDOWN}, _AFTER)

    assert row.status == "VERIFIED"


def test_deprecation_audit_before_the_date_keeps_the_old_strictness():
    assert _dep_row(_prices(), {}, _BEFORE).status == "MISMATCH"  # page lists none, entry says one


def test_the_bundled_table_goes_green_for_all_four_models_the_day_after_the_shutdown():
    prices = load_gemini_prices()
    models = prices["models"]
    still_listed = {
        key: Rate(
            float(e["input_usd_per_mtok"]),
            float(e["output_usd_per_mtok"]),
            float(e["cached_input_usd_per_mtok"]),
        )
        for key, e in models.items()
        if (key.startswith("gpt-") or key in ("o1", "o3", "o3-mini", "o4-mini"))
        and key not in _RETIRING
    }
    rows = {r.key: r for r in audit(prices, None, still_listed, None, today=_AFTER)}
    dep_rows = {
        r.key: r for r in audit_deprecations(prices, {}, {}, {}, today=_AFTER) if "(" in r.key
    }

    for key in _RETIRING:
        assert rows[key].status == "SKIPPED", (key, rows[key].detail)
        assert dep_rows[f"{key} (deprecation)"].status == "VERIFIED", key
    # the models that did NOT retire are untouched by the new date logic
    assert rows["o3"].status == "VERIFIED"


# --- the freshness gate ----------------------------------------------------------------------------


def test_freshness_stops_counting_a_retired_models_fetched_on():
    models = {"o1": {"fetched_on": "2026-08-01", "retired_on": "2026-10-23"}}

    # Before its shutdown date a 61-day-old fetch is stale like any live model's ...
    assert [row[0] for row in _check_staleness(models, date(2026, 10, 1), 30)] == ["o1"]
    # ... from the shutdown date it cannot be re-fetched, so it is exempt (and stays so).
    assert _check_staleness(models, _SHUTDOWN, 30) == []
    assert _check_staleness(models, date(2026, 12, 1), 30) == []
    # An entry with no retired_on at all is held to the rule forever.
    live = {"o1": {"fetched_on": "2026-08-01"}}
    assert [row[0] for row in _check_staleness(live, date(2026, 12, 1), 30)] == ["o1"]


# --- the report ---------------------------------------------------------------------------------------


def _store_of(model: str) -> UsageStore:
    store = UsageStore()
    store.record(
        "e-inv-0",
        CapturedCall(
            model_version=model,
            prompt_token_count=1000,
            candidates_token_count=200,
            cached_content_token_count=0,
            total_token_count=1200,
        ),
    )
    return store


def _snapshot_of(model: str, tmp_path: Path) -> Path:
    path = tmp_path / "snap.json"
    write_snapshot(_store_of(model), path)
    return path


def test_report_marks_a_retired_model_as_priced_at_its_last_published_rate():
    snap = build_snapshot(_store_of("gpt-4.1-nano"))

    before = unverifiable_pricing(snap, today=_BEFORE)
    after = unverifiable_pricing(snap, today=_AFTER)

    assert before == []  # live until its day: the weekly check still verifies the rate
    assert [row["model"] for row in after] == ["gpt-4.1-nano"]
    assert after[0]["reason"].startswith("retired 2026-10-23; priced at last published rate")
    assert "(the vendor page last listed it 2026-10-07)" in after[0]["reason"]


@pytest.mark.parametrize(("today", "marked"), [(_BEFORE, False), (_AFTER, True)])
def test_cli_report_text_switches_on_the_date(tmp_path, capsys, monkeypatch, today, marked):
    monkeypatch.setattr(_pricing, "_today", lambda: today)
    snap = _snapshot_of("o4-mini", tmp_path)

    assert main(["report", str(snap)]) == EXIT_PASS

    out = capsys.readouterr().out
    assert ("o4-mini: retired 2026-10-23; priced at last published rate" in out) is marked
    # 1,000 in x $1.10/M + 200 out x $4.40/M = $0.001100 + $0.000880 = $0.001980, either way
    assert "$0.001980" in out
