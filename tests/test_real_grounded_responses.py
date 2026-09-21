"""Real grounded gemini-2.5-flash responses (captured 2026-09-21 through ADK's built-in
``google_search`` and ``GoogleSearchAgentTool``, Gemini API, free tier) replayed through the real plugin.

Data: ``docs/design/data/real_grounding_2026-09-21.jsonl``, one verbatim ``model_dump`` per model call.
Every grounded call carried ``tool_use_prompt_token_count``, which used to make the whole invocation
unknown. Now the rest is priced, the tool-use tokens and the grounding fee are flagged. Figures are
hand-computed from gemini-2.5-flash rates ($0.30/M input, $2.50/M output including thinking tokens) in
the comment next to each.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._report import render_text, to_json_dict, total_is_complete
from adk_tracegauge._store import UsageStore
from adk_tracegauge.snapshot import build_snapshot

DATA = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "design"
    / "data"
    / "real_grounding_2026-09-21.jsonl"
)


def _calls(scenario: str) -> list[dict]:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["kind"] == "model_call" and r["scenario"] == scenario]


async def _replay(scenario: str) -> UsageStore:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    for row in _calls(scenario):
        ctx = SimpleNamespace(
            invocation_id=row["invocation_id"],
            session=SimpleNamespace(id="s-" + row["invocation_id"]),
            agent_name=row["agent"],
        )
        gm = row["grounding_metadata"]
        response = LlmResponse(
            model_version=row["model_version"],
            partial=row["partial"],
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata.model_validate(
                row["usage_metadata"]
            ),
            grounding_metadata=(
                genai_types.GroundingMetadata.model_validate(gm) if gm is not None else None
            ),
        )
        await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    return store


def _components(record) -> dict[str, dict]:
    return {c["component"]: c for c in record.unpriced_components}


def test_the_data_file_holds_the_twelve_captured_calls():
    assert (
        len(
            [
                r
                for r in map(json.loads, DATA.read_text(encoding="utf-8").splitlines())
                if r["kind"] == "model_call"
            ]
        )
        == 12
    )


@pytest.mark.asyncio
async def test_single_grounded_call_prices_the_rest_and_flags_tool_use_tokens_and_the_fee():
    # prompt 44, candidates 39, thoughts 432, tool_use 119, two web_search_queries.
    # 44 x $0.30/M = $0.0000132 ; (39 + 432) x $2.50/M = $0.0011775 -> $0.0011907.
    snap = build_snapshot(await _replay("A_2_5_single"))

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0011907)
    parts = _components(record)
    assert set(parts) == {"tool_use_prompt_tokens", "grounding_fee"}
    assert parts["tool_use_prompt_tokens"]["tokens"] == 119
    assert parts["grounding_fee"]["source"] == "google_search"
    assert parts["grounding_fee"]["tokens"] == 2  # queries
    assert not total_is_complete(snap)
    text = render_text(snap, "x")
    assert "119 tool-use prompt token(s) not priced" in text
    assert "Google Search grounding on 1 call(s) (2 search queries)" in text
    assert "INCOMPLETE" in text
    assert "(1 priced, 0 unknown)" in text  # priced with flags, no longer unknown


@pytest.mark.asyncio
async def test_agent_loop_flags_only_the_two_grounded_sub_agent_invocations():
    # root, sub, root, sub, root: 5 model calls, exactly the 2 sub-agent calls grounded.
    # root: 116 x .30/M + (20+46) x 2.50/M = $0.0001998 ; 280 x .30/M + (23+40) x 2.50/M = $0.0002415 ;
    #       357 x .30/M + (33+18) x 2.50/M = $0.0002346 -> $0.0006759, no components.
    # sub 1: 78 x .30/M + (143+548) x 2.50/M = $0.0000234 + $0.0017275 = $0.0017509 (tool_use 132, 1 query)
    # sub 2: 81 x .30/M + (56+267) x 2.50/M = $0.0000243 + $0.0008075 = $0.0008318 (tool_use 148, 1 query)
    snap = build_snapshot(await _replay("C_2_5_agent_loop"))

    by_agent = {}
    for r in snap.records:
        by_agent.setdefault(next(iter(r.cost_by_agent)), []).append(r)
    (root_record,) = by_agent["root"]
    assert root_record.cost_usd == pytest.approx(0.0006759)
    assert root_record.unpriced_components == []
    subs = sorted(by_agent["google_search_agent"], key=lambda r: r.cost_usd, reverse=True)
    assert [round(r.cost_usd, 7) for r in subs] == [0.0017509, 0.0008318]
    assert [_components(r)["tool_use_prompt_tokens"]["tokens"] for r in subs] == [132, 148]
    assert all(_components(r)["grounding_fee"]["tokens"] == 1 for r in subs)
    assert sum(len(r.unpriced_components) > 0 for r in snap.records) == 2
    assert to_json_dict(snap, "x")["n_incomplete"] == 2


@pytest.mark.asyncio
async def test_streamed_grounded_call_is_one_call_not_two():
    # The partial chunk already carries the full metadata and usage; the final chunk repeats both.
    # prompt 44, candidates 38, thoughts 429, tool_use 115: 44 x .30/M + (38+429) x 2.50/M = $0.0011807.
    snap = build_snapshot(await _replay("F_2_5_streaming"))

    (record,) = snap.records
    assert record.call_count == 1
    assert record.cost_usd == pytest.approx(0.0011807)
    parts = _components(record)
    assert parts["tool_use_prompt_tokens"]["tokens"] == 115
    assert parts["grounding_fee"]["tokens"] == 2


@pytest.mark.asyncio
async def test_zero_result_prompt_still_carries_grounding_and_is_flagged():
    # Queries present, no grounding_chunks, 130 tool-use tokens, an empty answer.
    # prompt 59, candidates 34, thoughts 37: 59 x .30/M + (34+37) x 2.50/M = $0.0001952.
    snap = build_snapshot(await _replay("D_2_5_no_results_likely"))

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0001952)
    assert _components(record)["grounding_fee"]["source"] == "google_search"
    assert _components(record)["tool_use_prompt_tokens"]["tokens"] == 130


@pytest.mark.asyncio
async def test_a_call_that_had_the_search_tool_but_did_not_search_is_complete():
    # "17 x 23": no grounding_metadata, no tool-use tokens. 38 x .30/M + (3+33) x 2.50/M = $0.0001014.
    snap = build_snapshot(await _replay("E_2_5_search_tool_enabled_but_not_needed"))

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0001014)
    assert record.unpriced_components == []
    assert total_is_complete(snap)


@pytest.mark.asyncio
async def test_two_searches_in_one_prompt_is_one_grounded_call_with_two_queries():
    # prompt 49, candidates 57, thoughts 134, tool_use 131, 2 queries, 5 chunks.
    # 49 x .30/M + (57+134) x 2.50/M = $0.0000147 + $0.0004775 = $0.0004922.
    snap = build_snapshot(await _replay("G_2_5_two_searches_one_prompt"))

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0004922)
    assert record.call_count == 1
    assert _components(record)["grounding_fee"]["tokens"] == 2
