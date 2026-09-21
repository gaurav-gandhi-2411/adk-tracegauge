"""A vendor-billed component the token arithmetic cannot price (the Google Search grounding fee)
is FLAGGED and the total is marked incomplete -- never silently omitted.

Every dollar figure is hand-computed from the published gemini-2.5-flash rates in a comment next
to it: $0.30/M input, $0.03/M cached input (0.1x), $2.50/M output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._report import render_text, to_json_dict, total_is_complete
from adk_tracegauge._store import UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator
from adk_tracegauge.snapshot import build_snapshot, read_snapshot, write_snapshot


def _response(
    model: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    grounding: genai_types.GroundingMetadata | None = None,
    partial: bool = False,
) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        total_token_count=prompt + output,
    )
    return LlmResponse(
        model_version=model, usage_metadata=usage, grounding_metadata=grounding, partial=partial
    )


async def _capture(
    store: UsageStore, mocker, responses: list[LlmResponse], invocation_id: str = "e-inv-1"
) -> None:
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id = invocation_id
    ctx.session.id = "s-" + invocation_id
    ctx.agent_name = "root_agent"
    for r in responses:
        await plugin.after_model_callback(callback_context=ctx, llm_response=r)


def _search(n: int = 2) -> genai_types.GroundingMetadata:
    return genai_types.GroundingMetadata(web_search_queries=[f"q{i}" for i in range(n)])


# ---- grounding: the three paths the cost-correctness suite exercises --------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-3.5-flash"])
async def test_builtin_google_search_is_flagged_on_2_5_and_3_x(mocker, model: str):
    # 1,000 in x $0.30/M + 200 out x $2.50/M = $0.000800 on 2.5; 3.5-flash is $1.50/$9.00:
    # 1,000 x 1.50/M + 200 x 9.00/M = $0.001500 + $0.001800 = $0.003300. The grounding fee is not
    # in either figure -- that is exactly what must be flagged.
    store = UsageStore()
    await _capture(store, mocker, [_response(model=model, grounding=_search(2))])

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0008 if model == "gemini-2.5-flash" else 0.0033)
    assert [c["component"] for c in record.unpriced_components] == ["grounding_fee"]
    assert "grounding fee not included in total" in record.unpriced_components[0]["detail"]
    assert record.unpriced_components[0]["tokens"] == 2  # the two search queries
    assert not total_is_complete(snap)
    assert "grounding fee not included in total" in render_text(snap, "x")
    body = to_json_dict(snap, "x")
    assert body["total_is_complete"] is False
    assert body["n_incomplete"] == 1
    assert body["invocations"][0]["is_complete"] is False
    assert body["unpriced_components"] == [
        {"component": "grounding_fee", "invocations": 1, "tokens": 2}
    ]


@pytest.mark.asyncio
async def test_google_search_agent_tool_flags_the_sub_agents_own_record(mocker):
    # GoogleSearchAgentTool runs a search sub-agent as its own invocation whose model call carries
    # the grounding metadata; the parent's call does not. The sub-agent's record is the flagged one.
    store = UsageStore()
    await _capture(store, mocker, [_response()], invocation_id="e-parent")
    await _capture(store, mocker, [_response(grounding=_search(1))], invocation_id="e-child")
    store.record_parent("e-child", "e-parent")

    snap = build_snapshot(store)

    by_id = {r.invocation_id: r for r in snap.records}
    assert by_id["e-parent"].unpriced_components == []
    assert [c["component"] for c in by_id["e-child"].unpriced_components] == ["grounding_fee"]
    assert not total_is_complete(snap)
    text = render_text(snap, "x")
    assert "INCOMPLETE" in text
    assert "e-child" in text.split("NOT included in the total")[1]


@pytest.mark.asyncio
async def test_grounding_on_an_earlier_streamed_chunk_is_still_flagged(mocker):
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [
            _response(partial=True, output=50, grounding=_search(1)),
            _response(partial=False, output=200),  # the final chunk carries no grounding
        ],
    )

    (record,) = build_snapshot(store).records

    assert [c["component"] for c in record.unpriced_components] == ["grounding_fee"]


@pytest.mark.asyncio
async def test_grounding_chunks_without_search_queries_are_still_flagged(mocker):
    store = UsageStore()
    grounded = genai_types.GroundingMetadata(
        grounding_chunks=[genai_types.GroundingChunk(web=genai_types.GroundingChunkWeb(uri="u"))]
    )
    await _capture(store, mocker, [_response(grounding=grounded)])

    (record,) = build_snapshot(store).records

    assert [c["component"] for c in record.unpriced_components] == ["grounding_fee"]


@pytest.mark.asyncio
async def test_a_plain_call_and_an_empty_grounding_object_are_not_flagged(mocker):
    store = UsageStore()
    await _capture(store, mocker, [_response()], invocation_id="e-a")
    await _capture(
        store, mocker, [_response(grounding=genai_types.GroundingMetadata())], invocation_id="e-b"
    )

    snap = build_snapshot(store)

    assert all(r.unpriced_components == [] for r in snap.records)
    assert total_is_complete(snap)
    assert to_json_dict(snap, "x")["total_is_complete"] is True
    assert "NOT included" not in render_text(snap, "x")


# ---- persistence, the eval metric, and old files ----------------------------------------------


@pytest.mark.asyncio
async def test_flags_survive_the_snapshot_file_and_a_v3_file_reads_back_complete(
    mocker, tmp_path: Path
):
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_search(1))])
    path = tmp_path / "s.json"
    write_snapshot(store, path)

    reread = read_snapshot(path)

    assert reread.schema_version == 4
    assert [c["component"] for c in reread.records[0].unpriced_components] == ["grounding_fee"]

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 3
    for r in raw["records"]:
        del r["unpriced_components"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert read_snapshot(path).records[0].unpriced_components == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"grounding": _search(1)}],
)
async def test_the_eval_metric_reports_not_evaluated_not_a_lower_bound_score(mocker, kwargs):
    store = UsageStore()
    await _capture(store, mocker, [_response(**kwargs)], invocation_id="inv-1")
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0), store=store
    )

    result = evaluator.evaluate_invocations(
        [
            Invocation(
                invocation_id="inv-1",
                user_content=genai_types.Content(parts=[genai_types.Part(text="q")], role="user"),
            )
        ]
    )

    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert pir.eval_status == EvalStatus.NOT_EVALUATED
    assert "cost not computed" in pir.rubric_scores[0].rationale
