"""Google Search grounding fee, priced on the Gemini API path only (0.10.0).

Design: docs/design/grounding-fees.md. Expected figures are hand-computed from the price table's
published $35 / 1,000 grounded prompts (ai.google.dev/gemini-api/docs/pricing, raw page fetched
2026-10-07, sha256 prefix a54db5a4e1224296) and pre-registered in
docs/design/pricing-gaps-0-10-0-preregistration.md before the implementation existed.
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

from adk_tracegauge._adapter import (
    GROUNDING_FREE_ALLOWANCE_ENV_VAR,
    UPPER_BOUND_LABEL,
    build_session_digest,
)
from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._report import render_text, to_json_dict, total_is_complete
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator
from adk_tracegauge.snapshot import build_snapshot, read_snapshot, write_snapshot

_FEE = 0.035  # $35 / 1,000 grounded prompts
_REPLAY = (
    Path(__file__).resolve().parent.parent / "docs/design/data/real_grounding_2026-09-21.jsonl"
)


def _search(n: int = 2) -> genai_types.GroundingMetadata:
    return genai_types.GroundingMetadata(web_search_queries=[f"q{i}" for i in range(n)])


def _response(
    model: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    tool_use: int = 0,
    grounding: genai_types.GroundingMetadata | None = None,
    partial: bool = False,
    traffic_type: genai_types.TrafficType | None = None,
) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        tool_use_prompt_token_count=tool_use or None,
        total_token_count=prompt + output + tool_use,
        traffic_type=traffic_type,
    )
    return LlmResponse(
        model_version=model, usage_metadata=usage, grounding_metadata=grounding, partial=partial
    )


async def _capture(
    store: UsageStore,
    mocker,
    responses: list[LlmResponse],
    invocation_id: str = "e-inv-1",
    vertexai: object = False,
    agent_name: str = "root_agent",
) -> None:
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id = invocation_id
    ctx.session.id = "s-" + invocation_id
    ctx.agent_name = agent_name
    ctx._invocation_context.agent.canonical_model.api_client.vertexai = vertexai
    for r in responses:
        await plugin.after_model_callback(callback_context=ctx, llm_response=r)


@pytest.fixture(autouse=True)
def _no_free_allowance(monkeypatch):
    monkeypatch.delenv(GROUNDING_FREE_ALLOWANCE_ENV_VAR, raising=False)


# ---- the priced path -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_api_google_search_prompt_is_priced_once_per_prompt_not_per_query(mocker):
    # PRE-REGISTERED (suite G1): tokens 1,000 x $0.30/M + 200 x $2.50/M = $0.000800, plus ONE
    # $0.035 fee however many queries ran (3 here) = $0.035800.
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_search(3))])

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0358)
    assert record.grounding_fee_usd == pytest.approx(_FEE)
    assert record.is_upper_bound is True
    assert record.unpriced_components == []
    assert total_is_complete(snap)  # nothing was left out ...
    body = to_json_dict(snap, "x")
    assert body["total_is_complete"] is True
    assert body["total_is_upper_bound"] is True  # ... but it is not an exact figure
    assert body["grounding_fee_usd"] == pytest.approx(_FEE)
    assert body["total_excluding_grounding_usd"] == pytest.approx(0.0008)
    assert body["invocations"][0]["is_upper_bound"] is True
    text = render_text(snap, "x")
    assert "upper bound (grounding priced at paid rate; free allowance not observable)" in text
    assert UPPER_BOUND_LABEL in text
    assert "~" in text  # the priced-fee row marker, distinct from the unpriced `*`
    assert "at least" not in text


@pytest.mark.asyncio
async def test_tool_use_tokens_are_priced_at_the_input_rate_on_the_priced_path_only(mocker):
    # 1,000 + 120 tool-use = 1,120 x $0.30/M = 0.000336; 200 x $2.50/M = 0.0005; + 0.035.
    store = UsageStore()
    await _capture(store, mocker, [_response(tool_use=120, grounding=_search(1))])

    (record,) = build_snapshot(store).records

    assert record.cost_usd == pytest.approx(0.000336 + 0.0005 + _FEE)
    assert record.unpriced_components == []
    assert any("tool-use prompt token" in a for a in record.assumptions)
    assert any("paid rate" in a for a in record.assumptions)


@pytest.mark.asyncio
async def test_a_streamed_grounded_call_is_one_fee_not_zero_and_not_two(mocker):
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [
            _response(partial=True, output=50, grounding=_search(1)),
            _response(partial=False, output=200, grounding=_search(1)),
        ],
    )

    (record,) = build_snapshot(store).records

    assert record.grounding_fee_usd == pytest.approx(_FEE)


@pytest.mark.asyncio
async def test_two_grounded_calls_are_two_fees(mocker):
    store = UsageStore()
    await _capture(
        store, mocker, [_response(grounding=_search(1)), _response(grounding=_search(2))]
    )

    (record,) = build_snapshot(store).records

    assert record.grounding_fee_usd == pytest.approx(2 * _FEE)


@pytest.mark.asyncio
async def test_the_fee_is_attributed_to_the_grounded_calls_agent_and_agents_sum_to_the_total(
    mocker,
):
    store = UsageStore()
    await _capture(store, mocker, [_response()], invocation_id="e-p", agent_name="root")
    await _capture(
        store, mocker, [_response(grounding=_search(1))], invocation_id="e-c", agent_name="searcher"
    )
    store.record_parent("e-c", "e-p")

    snap = build_snapshot(store)

    by_id = {r.invocation_id: r for r in snap.records}
    assert by_id["e-p"].grounding_fee_usd == 0.0 and by_id["e-p"].is_upper_bound is False
    child = by_id["e-c"]
    assert child.cost_by_agent["searcher"] == pytest.approx(0.0008 + _FEE)
    assert sum(child.cost_by_agent.values()) == pytest.approx(child.cost_usd)


# ---- "flag stays" rows of the design's section 4 ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        {"vertexai": True},
        {"vertexai": "unreadable"},  # ADK internals drifted: cannot tell, so do not price
        {"vertexai": False, "traffic_type": genai_types.TrafficType.ON_DEMAND},  # Vertex-only field
        {"model": "gemini-3.5-flash"},  # no verified per-prompt row (3.x bills per query)
        {"model": "gpt-5.1"},  # not a Gemini entry at all
    ],
)
async def test_unverified_backends_and_models_keep_the_grounding_flag(mocker, case):
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [
            _response(
                model=case.get("model", "gemini-2.5-flash"),
                grounding=_search(2),
                traffic_type=case.get("traffic_type"),
            )
        ],
        vertexai=case.get("vertexai", False),
    )

    (record,) = build_snapshot(store).records

    assert [c["component"] for c in record.unpriced_components] == ["grounding_fee"]
    assert record.grounding_fee_usd == 0.0
    assert record.is_upper_bound is False
    assert not total_is_complete(build_snapshot(store))


@pytest.mark.asyncio
async def test_a_maps_source_is_flagged_while_the_search_fee_in_the_same_call_is_priced(mocker):
    maps = genai_types.GroundingMetadata(
        web_search_queries=["q"], google_maps_widget_context_token="tok"
    )
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=maps)])

    (record,) = build_snapshot(store).records

    assert record.grounding_fee_usd == pytest.approx(_FEE)
    assert [(c["component"], c["source"]) for c in record.unpriced_components] == [
        ("grounding_fee", "google_maps")
    ]


@pytest.mark.asyncio
async def test_tool_use_tokens_stay_flagged_when_the_grounding_fee_is_not_priced(mocker):
    store = UsageStore()
    await _capture(store, mocker, [_response(tool_use=120, grounding=_search(1))], vertexai=True)

    (record,) = build_snapshot(store).records

    assert {c["component"] for c in record.unpriced_components} == {
        "grounding_fee",
        "tool_use_prompt_tokens",
    }


# ---- the explicit free-allowance assertion ---------------------------------------------------


@pytest.mark.asyncio
async def test_free_allowance_env_var_prices_the_fee_at_zero_and_prints_the_assumption(
    mocker, monkeypatch
):
    monkeypatch.setenv(GROUNDING_FREE_ALLOWANCE_ENV_VAR, "1")
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_search(1))])

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0008)
    assert record.grounding_fee_usd == 0.0
    assert record.is_upper_bound is False
    assert to_json_dict(snap, "x")["total_is_upper_bound"] is False
    text = render_text(snap, "x")
    assert GROUNDING_FREE_ALLOWANCE_ENV_VAR in text
    assert "upper bound" not in text


# ---- adapter, evaluator, snapshot file -------------------------------------------------------


def test_adapter_returns_fees_and_assumptions_without_touching_the_token_digest():
    call = CapturedCall(
        model_version="gemini-2.5-flash",
        prompt_token_count=1000,
        candidates_token_count=200,
        cached_content_token_count=0,
        total_token_count=1200,
        grounding_sources=("google_search",),
        grounding_queries=1,
        backend="gemini_api",
        agent_name="a",
    )

    adapted = build_session_digest("inv", [call])

    assert [(f.turn_index, f.agent_name, f.usd) for f in adapted.fees] == [(0, "a", _FEE)]
    assert adapted.is_upper_bound
    assert adapted.unpriced_components == ()


@pytest.mark.asyncio
async def test_the_eval_metric_scores_a_priced_with_assumption_invocation(mocker):
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_search(1))], invocation_id="inv-1")
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
    assert pir.score == pytest.approx(0.0358)
    assert pir.eval_status == EvalStatus.PASSED
    rationale = pir.rubric_scores[0].rationale
    assert "ASSUMPTION:" in rationale and "paid rate" in rationale
    assert UPPER_BOUND_LABEL in rationale
    assert "grounding_fee=$0.035000" in rationale


@pytest.mark.asyncio
async def test_a_v4_snapshot_file_reads_back_without_a_priced_fee(mocker, tmp_path: Path):
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_search(1))])
    path = tmp_path / "s.json"
    write_snapshot(store, path)
    assert read_snapshot(path).records[0].grounding_fee_usd == pytest.approx(_FEE)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 4
    for r in raw["records"]:
        for k in ("assumptions", "grounding_fee_usd", "is_upper_bound"):
            del r[k]
    path.write_text(json.dumps(raw), encoding="utf-8")

    old = read_snapshot(path).records[0]
    assert (old.assumptions, old.grounding_fee_usd, old.is_upper_bound) == ([], 0.0, False)


# ---- real-capture replay ---------------------------------------------------------------------


def _replay_rows() -> list[dict]:
    rows = [json.loads(line) for line in _REPLAY.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["kind"] == "model_call"]


def _is_search_grounded(row: dict) -> bool:
    gm = row.get("grounding_metadata") or {}
    return bool(gm.get("web_search_queries") or gm.get("grounding_chunks"))


@pytest.mark.asyncio
async def test_replaying_the_real_2026_09_21_captures_prices_one_fee_per_grounded_2_5_call(mocker):
    # Real google-adk 2.9.2 responses on the Gemini API (vertexai_backend False on every row).
    rows = [r for r in _replay_rows() if r["model_version"].startswith("gemini-2.5")]
    assert rows and all(r["vertexai_backend"] is False for r in rows)
    expected_grounded = sum(1 for r in rows if _is_search_grounded(r))
    assert expected_grounded >= 5  # the capture really contains grounded calls

    store = UsageStore()
    for r in rows:
        response = LlmResponse.model_validate(
            {
                "model_version": r["model_version"],
                "usage_metadata": r["usage_metadata"],
                "grounding_metadata": r["grounding_metadata"],
                "partial": bool(r["partial"]),
            }
        )
        await _capture(
            store,
            mocker,
            [response],
            invocation_id=r["invocation_id"],
            vertexai=r["vertexai_backend"],
            agent_name=r["agent"],
        )

    snap = build_snapshot(store)

    # a streamed call (partial chunk + final chunk, same metadata) counts once, so compare against
    # the distinct (invocation, call group) count rather than the raw row count
    priced_fees = sum(r.grounding_fee_usd for r in snap.records)
    streamed_duplicates = sum(1 for r in rows if r["partial"] is True and _is_search_grounded(r))
    assert priced_fees == pytest.approx(_FEE * (expected_grounded - streamed_duplicates))
    assert all(r.unpriced_components == [] for r in snap.records if r.grounding_fee_usd)
    assert not snap.skipped
