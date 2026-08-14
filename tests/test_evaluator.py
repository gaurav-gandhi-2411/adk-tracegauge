from __future__ import annotations

import pytest
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus
from google.genai import types as genai_types

from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator


def _invocation(invocation_id: str) -> Invocation:
    return Invocation(
        invocation_id=invocation_id,
        user_content=genai_types.Content(
            parts=[genai_types.Part(text="do something")], role="user"
        ),
        final_response=genai_types.Content(parts=[genai_types.Part(text="done")], role="model"),
    )


def _evaluator(store: UsageStore) -> CostEfficiencyEvaluator:
    return CostEfficiencyEvaluator(eval_metric=EvalMetric(metric_name=METRIC_NAME), store=store)


def test_no_usage_captured_reports_none_score_not_zero():
    store = UsageStore()
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    assert len(result.per_invocation_results) == 1
    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert "no usage captured" in pir.rubric_scores[0].rationale
    assert result.overall_score is None


def test_unresolved_model_reports_none_score_with_model_name_in_rationale():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="claude-sonnet-4-6",
            prompt_token_count=100,
            candidates_token_count=50,
            cached_content_token_count=0,
            total_token_count=150,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert "claude-sonnet-4-6" in pir.rubric_scores[0].rationale


def test_priced_invocation_reports_positive_cost_as_raw_score():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    # 1M input tokens @ $0.30/Mtok + 1M output tokens @ $2.50/Mtok = $2.80
    assert pir.score == 2.80
    assert "cost_usd=2.800000" in pir.rubric_scores[0].rationale


def test_eval_status_always_not_evaluated_never_passed_or_failed():
    # The core sign-problem resolution: this metric never gates pass/fail,
    # regardless of whether a threshold was configured on eval_metric.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=100,
            candidates_token_count=50,
            cached_content_token_count=0,
            total_token_count=150,
        ),
    )
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=0.01), store=store
    )

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    assert result.overall_eval_status == EvalStatus.NOT_EVALUATED
    assert result.per_invocation_results[0].eval_status == EvalStatus.NOT_EVALUATED


def test_overall_score_sums_cost_across_invocations_not_averages():
    store = UsageStore()
    for inv_id, prompt in [("inv-1", 1_000_000), ("inv-2", 1_000_000)]:
        store.record(
            inv_id,
            CapturedCall(
                model_version="gemini-2.5-flash",
                prompt_token_count=prompt,
                candidates_token_count=0,
                cached_content_token_count=0,
                total_token_count=prompt,
            ),
        )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1"), _invocation("inv-2")])

    # 2x (1M tokens @ $0.30/Mtok) = $0.60 total, not $0.30 averaged.
    assert result.overall_score == 0.60


def test_mixed_priced_and_unpriced_invocations_sums_only_the_priced_ones():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000_000,
        ),
    )
    # inv-2 has no captured usage at all.
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1"), _invocation("inv-2")])

    assert result.overall_score == 0.30
    assert result.per_invocation_results[1].score is None


def test_stale_price_table_entry_warns_in_rationale_and_via_python_warnings(monkeypatch):
    import adk_tracegauge._pricing as pricing

    # Force every entry to read as stale without needing to fabricate a
    # multi-year-old bundled table. -1 forces staleness even for an entry
    # fetched today (age 0 days > -1) -- 0 would not, since "0 days old" is
    # not ">  0 days old".
    monkeypatch.setattr(pricing, "STALE_THRESHOLD_DAYS", -1)

    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000_000,
        ),
    )
    evaluator = _evaluator(store)

    import warnings as warnings_module

    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == 0.30, "staleness must not block the score, only warn about it"
    assert "PRICE TABLE STALE" in pir.rubric_scores[0].rationale
    assert "gemini-2.5-flash" in pir.rubric_scores[0].rationale
    assert any("PRICE TABLE STALE" in str(w.message) for w in caught), (
        "staleness must also surface as a real Python warning, not only in "
        "the rationale text -- that's the only channel visible to someone "
        "watching logs rather than reading individual eval results"
    )


def test_delegated_sub_agent_cost_aggregates_into_the_parent_invocation():
    # Mirrors AgentTool: the sub-agent's real model call lands under its own
    # invocation_id, correlated back to the parent via record_parent (as
    # TraceGaugeUsagePlugin's before_run_callback/after_run_callback do for a
    # real AgentTool call).
    store = UsageStore()
    store.record(
        "parent",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=500_000,
            candidates_token_count=500_000,
            cached_content_token_count=0,
            total_token_count=1_000_000,
        ),
    )
    store.record(
        "child",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    store.record_parent("child", "parent")
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("parent")])

    pir = result.per_invocation_results[0]
    # parent's own call ($1.40) + delegated child's call ($2.80) = $4.20.
    assert pir.score == pytest.approx(4.20)
    assert "ai_calls=2" in pir.rubric_scores[0].rationale


def test_streaming_anomaly_reports_none_score_not_a_partial_total():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=100_000,
            candidates_token_count=50_000,
            cached_content_token_count=0,
            total_token_count=150_000,
            partial=True,
        ),
    )
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=100_000,
            candidates_token_count=10_000,
            cached_content_token_count=0,
            total_token_count=110_000,  # regressed -- monotonicity violated
            partial=False,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert "decreased" in pir.rubric_scores[0].rationale
    assert result.overall_score is None
