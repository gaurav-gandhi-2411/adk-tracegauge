"""A route announced for a future day: ``routed_to`` + ``routed_from`` (gemini-2.5-pro, gemini-2.5-flash).

Google's deprecations page (read 2026-11-02 and 2026-11-03): "effective December 1, 2026, API requests to
gemini-2.5-pro will automatically route to gemini-3.8-flash" (gemini-2.5-flash -> gemini-3.5-flash-lite), billed
at the successor's rates. Decision (GG, 2026-11-03): model them like the gemini-3.5-flash/-3.7-flash routed aliases
from that date, and keep their own verified rates until then. Every date below is passed in or monkeypatched,
never read from the clock.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types
from scripts.check_price_table_vs_vendor import (
    AuditRow,
    _check_pending_route,
    audit,
    audit_deprecations,
    parse_google_alias_routes,
)

from adk_tracegauge import _pricing
from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._pricing import (
    effective_prices,
    is_routed,
    load_gemini_prices,
    resolve_model,
    resolve_model_for_call,
)
from adk_tracegauge._store import UsageStore
from adk_tracegauge.snapshot import build_snapshot

_ROUTES = {"gemini-2.5-pro": "gemini-3.8-flash", "gemini-2.5-flash": "gemini-3.5-flash-lite"}
_LAST_OWN_DAY = date(2026, 11, 30)
_ROUTED_DAY = date(2026, 12, 1)


@pytest.fixture
def frozen(monkeypatch):
    def _freeze(day: date) -> None:
        monkeypatch.setattr(_pricing, "_today", lambda: day)

    return _freeze


# --- the predicate ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "today", "expected"),
    [
        ({}, _ROUTED_DAY, False),
        ({"routed_from": "2026-12-01"}, _ROUTED_DAY, False),  # a date alone routes nowhere
        ({"routed_to": "x"}, date(2020, 1, 1), True),  # no date: routed already (gemini-3.5-flash)
        ({"routed_to": "x", "routed_from": "2026-12-01"}, _LAST_OWN_DAY, False),
        # the day itself counts:
        ({"routed_to": "x", "routed_from": "2026-12-01"}, _ROUTED_DAY, True),
        ({"routed_to": "x", "routed_from": "2026-12-01"}, date(2027, 1, 1), True),
        ({"routed_to": "x", "routed_from": "not-a-date"}, date(2027, 1, 1), False),
    ],
)
def test_is_routed(entry, today, expected):
    assert is_routed(entry, today) is expected


# --- the table ----------------------------------------------------------------------------------


def test_the_two_entries_route_from_2026_12_01_and_keep_their_own_rates_until_then():
    models = load_gemini_prices()["models"]
    for key, target in _ROUTES.items():
        entry = models[key]
        assert entry["routed_to"] == target and entry["routed_from"] == "2026-12-01", key
        assert entry["input_usd_per_mtok"] > 0 and entry["output_usd_per_mtok"] > 0, key
        assert "routed_to" not in models[target], target  # one hop, never a chase


def test_every_routed_from_in_the_table_parses_and_its_entry_has_rates_to_use_until_then():
    # is_routed treats an unparseable routed_from as "not routed"; this keeps that branch unreachable.
    for key, entry in load_gemini_prices()["models"].items():
        if "routed_from" not in entry:
            continue
        date.fromisoformat(entry["routed_from"])
        assert "routed_to" in entry, key
        assert isinstance(entry.get("input_usd_per_mtok"), (int, float)), key


# --- pricing on both sides of the date ----------------------------------------------------------


@pytest.mark.parametrize("key", list(_ROUTES))
def test_before_the_date_the_entry_prices_itself(frozen, key):
    frozen(_LAST_OWN_DAY)
    own = load_gemini_prices()["models"][key]
    resolved = resolve_model(key)
    assert resolved is not None and resolved.model_key == key
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (
        own["input_usd_per_mtok"],
        own["output_usd_per_mtok"],
    )
    assert effective_prices()["models"][key]["input_usd_per_mtok"] == own["input_usd_per_mtok"]


@pytest.mark.parametrize(("key", "target"), list(_ROUTES.items()))
@pytest.mark.parametrize("day", [_ROUTED_DAY, date(2026, 12, 2)])
def test_from_the_date_the_entry_resolves_to_the_target_and_its_rates(frozen, key, target, day):
    frozen(day)
    resolved, served = resolve_model(key), resolve_model(target)
    assert resolved is not None and served is not None
    assert resolved.model_key == target
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (
        served.input_usd_per_mtok,
        served.output_usd_per_mtok,
    )
    assert effective_prices()["models"][key]["routed_to"] == target  # passed through untouched


def test_gemini_2_5_pros_long_context_tier_applies_only_before_the_route(frozen):
    frozen(_LAST_OWN_DAY)
    before = resolve_model_for_call("gemini-2.5-pro", prompt_token_count=300_000)
    assert before is not None and before.model_key == "gemini-2.5-pro-long-context"
    frozen(_ROUTED_DAY)
    after = resolve_model_for_call("gemini-2.5-pro", prompt_token_count=300_000)
    assert after is not None and after.model_key == "gemini-3.8-flash"


def _response(model_version: str, prompt: int, output: int) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        total_token_count=prompt + output,
        prompt_tokens_details=[genai_types.ModalityTokenCount(modality="TEXT", token_count=prompt)],
    )
    return LlmResponse(model_version=model_version, usage_metadata=usage)


async def _cost(mocker, response: LlmResponse) -> float:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id, ctx.session.id, ctx.agent_name = "e-inv-1", "s-1", "root_agent"
    await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    snap = build_snapshot(store)
    assert snap.skipped == []
    (record,) = snap.records
    return record.cost_usd


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "target"), list(_ROUTES.items()))
async def test_a_call_reporting_the_old_model_is_priced_at_the_rate_in_force_that_day(
    mocker, frozen, key, target
):
    prompt, output = 1_000, 100
    frozen(_LAST_OWN_DAY)
    own = resolve_model(key)
    assert own is not None
    before = await _cost(mocker, _response(key, prompt, output))
    assert before == pytest.approx(
        (prompt * own.input_usd_per_mtok + output * own.output_usd_per_mtok) / 1e6
    )
    frozen(_ROUTED_DAY)
    served = resolve_model(target)
    assert served is not None
    after = await _cost(mocker, _response(key, prompt, output))
    assert after == pytest.approx(
        (prompt * served.input_usd_per_mtok + output * served.output_usd_per_mtok) / 1e6
    )
    # (gemini-3.5-flash-lite's text rates equal gemini-2.5-flash's, so for that pair the two costs are
    # equal; gemini-2.5-pro's differ. The assertions above pin each day's rate either way.)


# --- the weekly vendor check ----------------------------------------------------------------------

_DEPRECATIONS_HTML = """<table>
<tr><th>Model</th><th>Release date</th><th>Shutdown date</th><th>Recommended replacement</th></tr>
<tr><td>gemini-2.5-pro</td><td>June 17, 2025</td><td>December 1, 2026</td><td>gemini-3.8-flash
(gemini-2.5-pro is no longer available in Google AI Studio; effective December 1, 2026, API requests to
gemini-2.5-pro will automatically route to gemini-3.8-flash)</td></tr>
<tr><td>gemini-2.5-flash</td><td>June 17, 2025</td><td>December 1, 2026</td><td>gemini-3.5-flash-lite
(gemini-2.5-flash is no longer available in Google AI Studio; effective December 1, 2026, API requests to
gemini-2.5-flash will automatically route to gemini-3.5-flash-lite)</td></tr>
<tr><td>gemini-3.7-flash</td><td>August 13, 2026</td><td>N/A</td><td>gemini-3.8-flash (gemini-3.7-flash is no
longer available in Google AI Studio; the endpoint is not shutting down and automatically routes API requests
to gemini-3.8-flash)</td></tr>
</table>"""


def test_the_announced_route_form_is_parsed_beside_the_current_one():
    # Rows copied from the live page's cell text on 2026-11-03.
    assert parse_google_alias_routes(_DEPRECATIONS_HTML) == {
        "gemini-2.5-pro": "gemini-3.8-flash",
        "gemini-2.5-flash": "gemini-3.5-flash-lite",
        "gemini-3.7-flash": "gemini-3.8-flash",
    }


def test_a_route_announced_for_another_endpoint_is_not_credited_to_this_row():
    html = _DEPRECATIONS_HTML.replace(
        "API requests to\ngemini-2.5-pro will", "API requests to\ngemini-9-other will"
    )
    assert "gemini-2.5-pro" not in parse_google_alias_routes(html)


def test_from_the_date_the_vendor_check_verifies_the_route_not_the_old_rates():
    models = load_gemini_prices()["models"]
    prices = {
        "models": {k: models[k] for k in (*_ROUTES, *_ROUTES.values())},
        "cache_multipliers": {"read": 0.1},
    }
    rows = audit(prices, None, None, None, today=date(2026, 12, 2), google_routes=dict(_ROUTES))
    by_key = {r.key: r for r in rows}
    for key in _ROUTES:
        assert by_key[key].status == "VERIFIED", by_key[key].detail
        assert "routed alias" in by_key[key].detail[0]


@pytest.mark.parametrize(
    ("routes", "status", "words"),
    [
        (dict(_ROUTES), "VERIFIED", "then routes to"),
        ({}, "UNVERIFIED", "no longer announces"),
        ({"gemini-2.5-pro": "gemini-9-other"}, "MISMATCH", "Google now says 'gemini-9-other'"),
        (None, "UNVERIFIED", "not fetched"),
    ],
)
def test_before_the_date_the_announced_route_is_checked_on_top_of_the_rates(routes, status, words):
    entry = load_gemini_prices()["models"]["gemini-2.5-pro"]
    row = AuditRow("gemini-2.5-pro", "VERIFIED", "1.25", "1.25", ["rates match"])
    _check_pending_route(row, entry, routes)
    assert row.status == status
    assert words in row.detail[-1]


def test_a_routed_from_that_disagrees_with_the_vendors_shutdown_date_is_a_mismatch():
    entry = copy.deepcopy(load_gemini_prices()["models"]["gemini-2.5-pro"])
    entry["routed_from"] = "2026-12-15"
    rows = audit_deprecations(
        {"models": {"gemini-2.5-pro": entry}},
        {},
        {},
        {"gemini-2.5-pro": date(2026, 12, 1)},
        today=_LAST_OWN_DAY,
    )
    assert [r.status for r in rows] == ["MISMATCH"]
    assert "routed_from: ours 2026-12-15 vs vendor 2026-12-01" in rows[0].detail
