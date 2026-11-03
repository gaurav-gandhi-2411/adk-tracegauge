"""tests/test_refresh_price_fetched_on.py -- the weekly fetched_on refresh never changes a rate.

What is pinned here (scripts/refresh_price_fetched_on.py):
- only VERIFIED entries get today's ``fetched_on``; a MISMATCH or UNVERIFIED entry keeps its date
  and is reported, and so is a VERIFIED entry whose deprecation row fails;
- synthetic and retired-by-date entries are never refreshed;
- the page hashes move only on a fully clean audit.
No network: audit rows and page bodies are constructed here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.check_price_table_vs_vendor import (
    ANTHROPIC_MD_URL,
    GOOGLE_HTML_URL,
    OPENAI_MD_URL,
    AuditRow,
)
from scripts.refresh_price_fetched_on import (
    PAGE_FETCH_URL,
    PRICES_PATH,
    make_plan,
    page_hash,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "price_refresh_table.json"
TODAY = date(2026, 11, 3)
BODIES = {GOOGLE_HTML_URL: "google page", OPENAI_MD_URL: "openai page", ANTHROPIC_MD_URL: "claude"}


def _text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _rows(**status: str) -> list[AuditRow]:
    """Audit rows for the fixture: every entry VERIFIED unless overridden; synthetic SKIPPED."""
    base = {
        "gemini-2.5-pro": "VERIFIED",
        "claude-opus-5": "VERIFIED",
        "gpt-5.6-sol": "VERIFIED",
        "gpt-4.1-nano": "VERIFIED",  # retired by date but still listed: verified, not refreshed
        "__local_zero_cost__": "SKIPPED",
        "gemini-2.5-pro (deprecation)": "VERIFIED",
    }
    base.update({k.replace("_dep", " (deprecation)"): v for k, v in status.items()})
    return [AuditRow(k, s, "ours", "vendor", [f"{k} {s}"]) for k, s in base.items()]


def test_a_clean_audit_plans_every_gated_entry_and_the_page_hashes():
    prices = json.loads(_text())
    plan = make_plan(prices, _rows(), [], BODIES, TODAY)
    assert plan.refreshed == {
        "gemini-2.5-pro": "2026-10-30",
        "claude-opus-5": "2026-10-30",
        "gpt-5.6-sol": "2026-10-30",
    }
    assert set(plan.not_refreshed) == {"gpt-4.1-nano", "__local_zero_cost__"}
    assert plan.held == [] and plan.changed
    assert plan.page_hashes == {
        "https://ai.google.dev/gemini-api/docs/pricing": page_hash("google page"),
        "https://developers.openai.com/api/docs/pricing.md": page_hash("openai page"),
        "https://platform.claude.com/docs/en/about-claude/pricing.md": page_hash("claude"),
    }


def test_a_mismatch_keeps_its_date_is_reported_and_freezes_the_page_hashes():
    prices = json.loads(_text())
    rows = _rows(**{"gpt-5.6-sol": "MISMATCH"})
    plan = make_plan(prices, rows, [], BODIES, TODAY)
    assert "gpt-5.6-sol" not in plan.refreshed
    assert [r.key for r in plan.held] == ["gpt-5.6-sol"]
    assert plan.page_hashes is None and "not clean" in plan.page_hash_note


def test_unverified_and_a_failing_deprecation_row_are_not_refreshed():
    prices = json.loads(_text())
    plan = make_plan(
        prices,
        _rows(**{"claude-opus-5": "UNVERIFIED", "gemini-2.5-pro_dep": "MISMATCH"}),
        [],
        BODIES,
        TODAY,
    )
    assert plan.refreshed == {"gpt-5.6-sol": "2026-10-30"}
    assert plan.not_refreshed["gemini-2.5-pro"] == "deprecation row MISMATCH"
    assert plan.not_refreshed["claude-opus-5"] == "UNVERIFIED"


def test_an_entry_with_no_audit_row_fails_closed():
    prices = json.loads(_text())
    rows = [r for r in _rows() if r.key != "claude-opus-5"]
    plan = make_plan(prices, rows, [], BODIES, TODAY)
    assert plan.not_refreshed["claude-opus-5"] == "no audit row (fails closed)"


def test_a_fetch_error_freezes_hashes_and_is_reported():
    prices = json.loads(_text())
    plan = make_plan(prices, _rows(), ["https://x: HTTP 500"], BODIES, TODAY)
    assert plan.page_hashes is None
    assert plan.errors == ["https://x: HTTP 500"]


def test_a_vendor_page_with_no_fetched_body_leaves_the_hashes_alone():
    prices = json.loads(_text())
    plan = make_plan(prices, _rows(), [], {GOOGLE_HTML_URL: "g"}, TODAY)
    assert plan.page_hashes is None and "no fetched body" in plan.page_hash_note


def test_nothing_to_do_when_already_refreshed_today():
    prices = json.loads(_text())
    for e in prices["models"].values():
        e["fetched_on"] = TODAY.isoformat()
    for p, url in PAGE_FETCH_URL.items():
        if p in prices["vendor_pages"]["pages"]:
            prices["vendor_pages"]["pages"][p] = page_hash(BODIES[url])
    plan = make_plan(prices, _rows(), [], BODIES, TODAY)
    assert not plan.changed


def test_every_vendor_pages_key_in_the_bundled_table_has_a_fetch_url():
    pages = json.loads(PRICES_PATH.read_text(encoding="utf-8"))["vendor_pages"]["pages"]
    assert set(pages) <= set(PAGE_FETCH_URL)
