"""tests/test_refresh_price_fetched_on.py -- the weekly fetched_on refresh never changes a rate.

What is pinned here (scripts/refresh_price_fetched_on.py):
- only VERIFIED entries get today's ``fetched_on``; a MISMATCH or UNVERIFIED entry keeps its date
  and is reported, and so is a VERIFIED entry whose deprecation row fails;
- synthetic and retired-by-date entries are never refreshed;
- the page hashes move only on a fully clean audit;
- the file is edited line by line (``10.00`` survives) and nothing but the planned fields changes;
- on the real bundled table, a clean audit moves every gated entry, so the 30-day release gate
  then runs out no earlier than 30 days after the refresh.
No network: audit rows and page bodies are constructed here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.check_price_freshness import RELEASE_MAX_AGE_DAYS, _check_staleness
from scripts.check_price_table_vs_vendor import (
    ANTHROPIC_MD_URL,
    GOOGLE_HTML_URL,
    OPENAI_MD_URL,
    AuditRow,
)
from scripts.refresh_price_fetched_on import (
    PAGE_FETCH_URL,
    PRICES_PATH,
    RefreshError,
    apply_plan,
    main,
    make_plan,
    page_hash,
    render_body,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "price_refresh_table.json"
TODAY = date(2026, 11, 3)
AT = "2026-11-03T09:30:00Z"
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
    body = render_body(plan, prices)
    assert "**MISMATCH** `gpt-5.6-sol`" in body and "ours: ours" in body
    assert "NOT auto-updated" in body and "No rate is changed by this PR" in body


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
    assert "COULD NOT FETCH/PARSE: https://x: HTTP 500" in render_body(plan, prices)


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


def test_a_clean_audit_refreshes_every_gated_entry_and_only_fetched_on_changes():
    text = _text()
    prices = json.loads(text)
    plan = make_plan(prices, _rows(), [], BODIES, TODAY)
    assert plan.refreshed == {
        "gemini-2.5-pro": "2026-10-30",
        "claude-opus-5": "2026-10-30",
        "gpt-5.6-sol": "2026-10-30",
    }
    assert "gpt-4.1-nano" in plan.not_refreshed and "__local_zero_cost__" in plan.not_refreshed
    new = apply_plan(text, plan, AT)
    # Line-level edit: the diff is exactly the fetched_on lines, the hash lines and fetched_at_utc.
    changed = [(a, b) for a, b in zip(text.splitlines(), new.splitlines(), strict=True) if a != b]
    kinds = [
        "fetched_on"
        if '"fetched_on"' in a
        else "fetched_at_utc"
        if '"fetched_at_utc"' in a
        else "page_hash"
        if a.lstrip().startswith('"https://')
        else a
        for a, _ in changed
    ]
    assert sorted(kinds) == ["fetched_at_utc"] + ["fetched_on"] * 3 + ["page_hash"] * 3
    assert '"output_usd_per_mtok": 10.00,' in new  # formatting preserved, not re-serialised
    got = json.loads(new)
    assert got["models"]["gemini-2.5-pro"]["fetched_on"] == "2026-11-03"
    assert got["models"]["gemini-2.5-pro"]["deprecation"]["checked_on"] == "2026-11-03"
    assert got["models"]["gpt-4.1-nano"]["fetched_on"] == "2026-10-30"
    assert got["vendor_pages"]["fetched_at_utc"] == AT
    assert got["vendor_pages"]["pages"]["https://ai.google.dev/gemini-api/docs/pricing"] == (
        page_hash("google page")
    )
    for key, entry in prices["models"].items():  # every rate identical
        for field, value in entry.items():
            if field != "fetched_on":
                assert got["models"][key][field] == value


def test_an_edit_that_would_touch_anything_else_is_refused():
    text = _text()
    plan = make_plan(json.loads(text), _rows(), [], BODIES, TODAY)
    plan.refreshed["claude-opus-5"] = "1999-01-01"  # the old value is not in the file
    with pytest.raises(RefreshError, match="expected exactly one"):
        apply_plan(text, plan, AT)


def test_on_the_real_table_a_clean_audit_moves_the_release_gate_30_days_out():
    text = PRICES_PATH.read_text(encoding="utf-8")
    prices = json.loads(text)
    rows = [AuditRow(k, "VERIFIED", "-", "-") for k in prices["models"]]
    bodies = {url: f"page {url}" for url in PAGE_FETCH_URL.values()}
    plan = make_plan(prices, rows, [], bodies, TODAY)
    got = json.loads(apply_plan(text, plan, AT))
    last_ok = date.fromordinal(TODAY.toordinal() + RELEASE_MAX_AGE_DAYS)
    after = date.fromordinal(last_ok.toordinal() + 1)
    assert _check_staleness(got["models"], last_ok, RELEASE_MAX_AGE_DAYS) == []
    assert _check_staleness(got["models"], after, RELEASE_MAX_AGE_DAYS) != []


def test_main_re_runs_the_audit_after_a_fetch_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    rows = [AuditRow(k, "VERIFIED", "-", "-") for k in prices["models"]]
    calls = [(rows, ["https://x: timed out after 20s"], {}), (rows, [], {})]
    with patch("scripts.refresh_price_fetched_on.collect", side_effect=calls) as fake:
        assert main(["--body-file", str(tmp_path / "body.md")]) == 0
    assert fake.call_count == 2
    assert "COULD NOT FETCH" not in (tmp_path / "body.md").read_text(encoding="utf-8")


def test_main_dry_run_writes_nothing_and_emits_outputs(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    before = PRICES_PATH.read_bytes()
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    rows = [AuditRow(k, "VERIFIED", "-", "-") for k in prices["models"]]
    with patch("scripts.refresh_price_fetched_on.collect", return_value=(rows, [], {})):
        assert main(["--body-file", str(tmp_path / "body.md")]) == 0
    assert PRICES_PATH.read_bytes() == before
    assert "changed=true" in out.read_text()
    assert "## NOT updated (needs a human)" in (tmp_path / "body.md").read_text(encoding="utf-8")
