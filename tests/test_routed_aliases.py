"""Routed aliases (gemini-3.5-flash, gemini-3.7-flash) and gemini-3.8-flash.

Evidence, 2026-10-20: Google dropped the pricing sections for gemini-3.5-flash and gemini-3.7-flash but keeps the
endpoints alive ("automatically routes API requests to" gemini-3.6-flash / gemini-3.8-flash on its deprecations
page). One real ADK call per alias on a free-tier key came back with `model_version` naming the SERVED model, so
adk-tracegauge, which prices by `model_version`, prices the served model. The usage numbers in the response tests
below are that verbatim `usage_metadata`. The table therefore marks the two aliases `routed_to` (no rates of their own)
and adds the previously missing gemini-3.8-flash.
"""

from __future__ import annotations

import copy

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._pricing import effective_prices, load_gemini_prices, resolve_model
from adk_tracegauge._store import UsageStore
from adk_tracegauge.snapshot import build_snapshot


@pytest.mark.parametrize(
    ("alias", "served"),
    [("gemini-3.5-flash", "gemini-3.6-flash"), ("gemini-3.7-flash", "gemini-3.8-flash")],
)
def test_an_alias_string_resolves_to_the_served_models_entry_and_rates(alias: str, served: str):
    resolved, target = resolve_model(alias), resolve_model(served)
    assert resolved is not None and target is not None
    assert resolved.model_key == served  # downstream sees the model that served the call
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (
        target.input_usd_per_mtok,
        target.output_usd_per_mtok,
    )


def test_the_alias_entries_carry_no_rates_of_their_own():
    models = load_gemini_prices()["models"]
    for alias, served in (
        ("gemini-3.5-flash", "gemini-3.6-flash"),
        ("gemini-3.7-flash", "gemini-3.8-flash"),
    ):
        entry = models[alias]
        assert entry["routed_to"] == served
        assert "input_usd_per_mtok" not in entry and "output_usd_per_mtok" not in entry
        assert "input_usd_per_mtok" in models[served]


def test_effective_prices_passes_an_alias_through_untouched():
    eff = effective_prices()
    assert eff["models"]["gemini-3.5-flash"]["routed_to"] == "gemini-3.6-flash"
    assert "input_usd_per_mtok" in eff["models"]["gemini-3.6-flash"]


def test_an_alias_that_routes_to_an_alias_is_a_table_error_not_a_chase():
    prices = copy.deepcopy(load_gemini_prices())
    prices["models"]["gemini-3.6-flash"] = {"routed_to": "gemini-3.5-flash"}
    with pytest.raises(ValueError, match="itself an alias"):
        resolve_model("gemini-3.5-flash", prices)


def test_gemini_3_8_flash_is_priced_and_its_variants_stay_unknown():
    resolved = resolve_model("gemini-3.8-flash")
    assert resolved is not None and resolved.model_key == "gemini-3.8-flash"
    assert (resolved.input_usd_per_mtok, resolved.output_usd_per_mtok) == (
        0.75,
        3.75,
    )  # promotional until 2026-12-31
    assert resolved.promo_until == "2026-12-31" and resolved.standard_rate_input_usd_per_mtok == 1.5
    # different models with different prices share the prefix: they must NOT be priced as the text model
    for variant in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.8-live"):
        assert resolve_model(variant) is None, variant


def _real_response(served: str, prompt: int, output: int, thoughts: int) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        thoughts_token_count=thoughts,
        total_token_count=prompt + output + thoughts,
        prompt_tokens_details=[genai_types.ModalityTokenCount(modality="TEXT", token_count=prompt)],
    )
    return LlmResponse(model_version=served, usage_metadata=usage)


async def _price(mocker, response: LlmResponse):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id, ctx.session.id, ctx.agent_name = "e-inv-1", "s-1", "root_agent"
    await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    snap = build_snapshot(store)
    return snap


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "served", "thoughts", "expected_usd"),
    [
        # Verbatim usage_metadata of the real calls on 2026-10-20 (request model -> response model_version):
        # prompt_token_count 24, candidates_token_count 1, thoughts_token_count 111 / 113. Output tokens bill thinking
        # too: (1 + 111) x $3.75/M + 24 x $0.75/M = $0.000420 + $0.000018 = $0.000438; (1 + 113) x $3.75/M + 24 x
        # $0.75/M = $0.0004275 + $0.000018 = $0.0004455.
        ("gemini-3.5-flash", "gemini-3.6-flash", 111, 0.000438),
        ("gemini-3.7-flash", "gemini-3.8-flash", 113, 0.0004455),
    ],
)
async def test_the_real_alias_responses_are_priced_under_the_served_model(
    mocker, requested: str, served: str, thoughts: int, expected_usd: float
):
    snap = await _price(mocker, _real_response(served, prompt=24, output=1, thoughts=thoughts))
    assert snap.skipped == [], (
        f"a response served by {served} (requested as {requested}) was skipped"
    )
    (record,) = snap.records
    assert record.models == [served]
    assert record.cost_usd == pytest.approx(expected_usd)
    assert record.unpriced_components == [] and record.assumptions == []


@pytest.mark.asyncio
async def test_before_this_change_a_response_served_by_3_8_flash_was_skipped_as_unknown(mocker):
    # The regression this PR fixes, stated as the control: an unknown served model is skipped, never guessed.
    snap = await _price(
        mocker, _real_response("gemini-9.9-nonexistent", prompt=24, output=1, thoughts=111)
    )
    assert snap.records == [] and len(snap.skipped) == 1
