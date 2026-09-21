"""Vendor-billed components the token arithmetic cannot price (grounding fees, audio input,
non-text output) are FLAGGED and the total is marked incomplete -- never silently omitted and never
priced at the text rate.

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

_M = genai_types.MediaModality


def _details(**tokens: int) -> list[genai_types.ModalityTokenCount]:
    return [
        genai_types.ModalityTokenCount(modality=_M[name.upper()], token_count=n)
        for name, n in tokens.items()
    ]


def _response(
    model: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    cached: int = 0,
    prompt_details: list[genai_types.ModalityTokenCount] | None = None,
    cache_details: list[genai_types.ModalityTokenCount] | None = None,
    output_details: list[genai_types.ModalityTokenCount] | None = None,
    grounding: genai_types.GroundingMetadata | None = None,
    partial: bool = False,
) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=cached,
        total_token_count=prompt + output,
        prompt_tokens_details=prompt_details,
        cache_tokens_details=cache_details,
        candidates_tokens_details=output_details,
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
        {"component": "grounding_fee", "source": "google_search", "invocations": 1, "tokens": 2}
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


# ---- audio input -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_input_is_left_out_and_flagged_never_priced_at_the_text_rate(mocker):
    # 1,000 prompt tokens of which 400 audio. Text-rate pricing of all 1,000 would be $0.000800
    # (the silent-wrong figure). Priced part: 600 x $0.30/M = $0.000180 + 200 x $2.50/M =
    # $0.000500 -> $0.000680; the 400 audio tokens are reported as unpriced.
    store = UsageStore()
    await _capture(store, mocker, [_response(prompt_details=_details(text=600, audio=400))])

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.00068)
    assert record.tokens_input == 600
    (component,) = record.unpriced_components
    assert component["component"] == "audio_input_tokens"
    assert component["tokens"] == 400
    assert "400 audio input token(s) not priced" in component["detail"]
    assert not total_is_complete(snap)
    assert "audio input token(s) not priced" in render_text(snap, "x")


@pytest.mark.asyncio
async def test_cached_audio_tokens_are_removed_from_the_cache_read_too(mocker):
    # 1,000 prompt: 400 audio, 600 text; 500 cached, 200 of them audio. Priced: 600 text-side
    # input, of which 500 - 200 = 300 cached ($0.03/M) and 300 fresh ($0.30/M):
    # 300 x 0.30/M = $0.000090; 300 x 0.03/M = $0.000009; 200 out x 2.50/M = $0.000500
    # -> $0.000599.
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [
            _response(
                cached=500,
                prompt_details=_details(text=600, audio=400),
                cache_details=_details(text=300, audio=200),
            )
        ],
    )

    (record,) = build_snapshot(store).records

    assert record.cost_usd == pytest.approx(0.000599)
    assert record.tokens_cache_read == 300


# ---- other non-text modalities -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_video_document_input_is_priced_at_the_text_rate_and_not_flagged(mocker):
    # The vendor publishes ONE input rate for text/image/video on every Gemini model in the table
    # (ai.google.dev/gemini-api/docs/pricing, read 2026-09-21), so 1,000 mixed tokens at $0.30/M
    # + 200 out at $2.50/M = $0.000800 is the correct, complete figure.
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [_response(prompt_details=_details(text=100, image=300, video=400, document=200))],
    )

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0008)
    assert record.unpriced_components == []
    assert total_is_complete(snap)


@pytest.mark.asyncio
@pytest.mark.parametrize("modality", ["image", "audio", "video"])
async def test_non_text_output_is_left_out_and_flagged(mocker, modality: str):
    # 1,000 output tokens of which 800 are `modality`. The model id may prefix-match a text entry
    # (gemini-2.5-flash-image -> gemini-2.5-flash), whose $2.50/M would misprice image output
    # ($30/M+). Priced part: 1,000 in x $0.30/M = $0.000300 + 200 text out x $2.50/M = $0.000500
    # -> $0.000800; the 800 non-text output tokens are unpriced.
    store = UsageStore()
    await _capture(
        store,
        mocker,
        [
            _response(
                model="gemini-2.5-flash-image",
                prompt=1000,
                output=1000,
                output_details=_details(text=200, **{modality: 800}),
            )
        ],
    )

    snap = build_snapshot(store)

    (record,) = snap.records
    assert record.cost_usd == pytest.approx(0.0008)
    (component,) = record.unpriced_components
    assert component["component"] == "non_text_output_tokens"
    assert component["tokens"] == 800
    assert f"800 {modality} output token(s) not priced" in component["detail"]
    assert not total_is_complete(snap)


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
    [
        {"grounding": _search(1)},
        {"prompt_details": _details(text=600, audio=400)},
        {"output_details": _details(text=100, image=100), "output": 200},
    ],
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


# ---- the flag names WHICH grounding source was used -----------------------------------------


def _gm(**kw) -> genai_types.GroundingMetadata:
    return genai_types.GroundingMetadata(**kw)


_SOURCES = {
    "google_search:queries": (
        _gm(web_search_queries=["a"]),
        "google_search",
        "Google Search grounding",
    ),
    "google_search:image_queries": (
        _gm(image_search_queries=["a"]),
        "google_search",
        "Google Search grounding",
    ),
    "google_search:entry_point": (
        _gm(search_entry_point=genai_types.SearchEntryPoint(rendered_content="<x/>")),
        "google_search",
        "Google Search grounding",
    ),
    "vertex_ai_search:queries": (
        _gm(retrieval_queries=["a"]),
        "vertex_ai_search",
        "Vertex AI Search grounding",
    ),
    "vertex_ai_search:chunk": (
        _gm(
            grounding_chunks=[
                genai_types.GroundingChunk(
                    retrieved_context=genai_types.GroundingChunkRetrievedContext(uri="u")
                )
            ]
        ),
        "vertex_ai_search",
        "Vertex AI Search grounding",
    ),
    "google_maps:token": (
        _gm(google_maps_widget_context_token="t"),
        "google_maps",
        "Google Maps grounding",
    ),
    "google_maps:chunk": (
        _gm(
            grounding_chunks=[
                genai_types.GroundingChunk(maps=genai_types.GroundingChunkMaps(uri="u"))
            ]
        ),
        "google_maps",
        "Google Maps grounding",
    ),
    "unknown:supports_only": (
        _gm(grounding_supports=[genai_types.GroundingSupport()]),
        "unknown",
        "unrecognised source type",
    ),
    "unknown:empty_chunk": (
        _gm(grounding_chunks=[genai_types.GroundingChunk()]),
        "unknown",
        "unrecognised source type",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_SOURCES))
async def test_the_flag_names_the_grounding_source(mocker, case: str):
    metadata, source, phrase = _SOURCES[case]
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=metadata)])

    snap = build_snapshot(store)

    (record,) = snap.records
    (component,) = record.unpriced_components
    assert component["component"] == "grounding_fee"
    assert component["source"] == source
    assert "grounding fee not included in total" in component["detail"]
    assert phrase in component["detail"]
    assert phrase in render_text(snap, "x")
    assert not total_is_complete(snap)
    assert {c["source"] for c in to_json_dict(snap, "x")["unpriced_components"]} == {source}


@pytest.mark.asyncio
async def test_only_google_search_counts_search_queries_and_others_do_not_claim_them(mocker):
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=_gm(retrieval_queries=["a", "b"]))])

    (record,) = build_snapshot(store).records

    (component,) = record.unpriced_components
    assert "search quer" not in component["detail"]
    assert component["tokens"] == 1  # one grounded call, not a query count


@pytest.mark.asyncio
async def test_one_call_with_two_sources_gets_one_flag_per_source(mocker):
    store = UsageStore()
    both = _gm(web_search_queries=["a", "b"], retrieval_queries=["c"])
    await _capture(store, mocker, [_response(grounding=both)])

    snap = build_snapshot(store)

    (record,) = snap.records
    assert [c["source"] for c in record.unpriced_components] == [
        "google_search",
        "vertex_ai_search",
    ]
    assert [
        (c["source"], c["invocations"]) for c in to_json_dict(snap, "x")["unpriced_components"]
    ] == [
        ("google_search", 1),
        ("vertex_ai_search", 1),
    ]


@pytest.mark.asyncio
async def test_the_shape_of_a_real_google_search_response_is_named_google_search(mocker):
    # Trimmed from a real gemini-2.5-flash response captured through ADK's built-in google_search
    # tool on 2026-09-21 (two queries, web chunks, supports, a search entry point).
    real = genai_types.GroundingMetadata.model_validate(
        {
            "grounding_chunks": [
                {"web": {"title": "python.org", "uri": "https://example.invalid/redirect/1"}},
                {"web": {"title": "python.org", "uri": "https://example.invalid/redirect/2"}},
            ],
            "grounding_supports": [
                {"grounding_chunk_indices": [0, 1], "segment": {"start_index": 95, "end_index": 97}}
            ],
            "search_entry_point": {"rendered_content": "<style></style>"},
            "web_search_queries": [
                "latest stable python version",
                "latest stable Node.js LTS version",
            ],
        }
    )
    store = UsageStore()
    await _capture(store, mocker, [_response(grounding=real)])

    (record,) = build_snapshot(store).records

    (component,) = record.unpriced_components
    assert component["source"] == "google_search"
    assert component["tokens"] == 2
    assert "Google Search grounding on 1 call(s) (2 search queries)" in component["detail"]
