"""Provenance invariants of the bundled price table: a ``fetched_on`` must be a date on which the
rate could actually have been read from the source.

Why this exists: ``gemini-2.0-flash`` was retired 2026-06-01 yet carries ``fetched_on`` 2026-08-14,
which looked like a live fetch that could not have happened. Investigation showed it *could*
(Google's pricing page kept a "deprecated and shut down" section listing the rate until 2026-08-26,
per Wayback captures), so the date is genuine -- but nothing in the repo could tell a true late
date from a false one. The rule below makes the distinction checkable: a retired entry may carry a
``fetched_on`` after its ``retired_on`` only if it also records ``vendor_page_last_listed``, the
last date the vendor page still showed the rate, and ``fetched_on`` is not later than that.
(The bare rule "fetched_on <= retired_on" is too strict: it would reject this true record.)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from adk_tracegauge._pricing import load_gemini_prices

_MODELS = load_gemini_prices()["models"]


def _d(value: str) -> date:
    return date.fromisoformat(value)


def provenance_problem(key: str, entry: dict[str, Any], today: date | None = None) -> str | None:
    """None if the entry's ``fetched_on`` is consistent with its source; else what is wrong."""
    fetched = _d(entry["fetched_on"])
    if fetched > (today or date.today()):
        return f"{key}: fetched_on {fetched} is in the future"
    if not entry.get("retired") or fetched <= _d(entry["retired_on"]):
        return None
    listed = entry.get("vendor_page_last_listed")
    if not listed:
        return (
            f"{key}: fetched_on {fetched} is after retired_on {entry['retired_on']}; record "
            "vendor_page_last_listed (the last date the vendor page still showed the rate) or "
            "fix fetched_on -- a rate cannot be fetched from a page that no longer lists it"
        )
    if fetched > _d(listed):
        return f"{key}: fetched_on {fetched} is after vendor_page_last_listed {listed}"
    return None


@pytest.mark.parametrize("key", sorted(_MODELS))
def test_every_bundled_entry_has_consistent_provenance(key: str):
    assert provenance_problem(key, _MODELS[key]) is None


def test_gemini_2_0_flash_keeps_its_archive_evidence():
    entry = _MODELS["gemini-2.0-flash"]
    assert entry["vendor_page_last_listed"] == "2026-08-26"
    assert entry["archive_url"].startswith("https://web.archive.org/web/20260826")


_RETIRED = {"fetched_on": "2026-08-14", "retired": True, "retired_on": "2026-06-01"}


def test_rule_rejects_a_late_fetch_with_no_listing_evidence():
    assert "vendor_page_last_listed" in provenance_problem("m", _RETIRED)


def test_rule_rejects_a_fetch_after_the_vendor_page_stopped_listing():
    entry = {**_RETIRED, "vendor_page_last_listed": "2026-08-10"}
    assert "is after vendor_page_last_listed" in provenance_problem("m", entry)


def test_rule_accepts_a_late_fetch_the_page_still_showed():
    entry = {**_RETIRED, "vendor_page_last_listed": "2026-08-26"}
    assert provenance_problem("m", entry) is None


def test_rule_accepts_a_fetch_before_retirement_and_rejects_a_future_date():
    assert provenance_problem("m", {**_RETIRED, "fetched_on": "2026-05-01"}) is None
    assert "future" in provenance_problem("m", {"fetched_on": "2999-01-01"})
