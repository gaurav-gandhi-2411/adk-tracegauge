from __future__ import annotations

import pytest
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus
from google.genai import types as genai_types

from adk_tracegauge._pricing import ASSUME_LOCAL_ENV_VAR
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator, CostThresholdCriterion


def _invocation(invocation_id: str) -> Invocation:
    return Invocation(
        invocation_id=invocation_id,
        user_content=genai_types.Content(
            parts=[genai_types.Part(text="do something")], role="user"
        ),
        final_response=genai_types.Content(parts=[genai_types.Part(text="done")], role="model"),
    )


def _evaluator(store: UsageStore, threshold_usd: float = 1_000.0) -> CostEfficiencyEvaluator:
    # A generously high default threshold -- every real cost figure in this
    # file's fixtures is well under it -- so tests unrelated to threshold
    # gating itself (token math, rationale content, staleness, ...) keep
    # PASSING and aren't coupled to the specific dollar amounts asserted
    # elsewhere in this file.
    return CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=threshold_usd), store=store
    )


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


def test_priced_invocation_under_threshold_passes():
    # Phase 2 W2: the core fix. A priceable invocation now gets a real
    # PASSED verdict, not the permanent NOT_EVALUATED that made
    # AgentEvaluator.evaluate() raise unconditionally.
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
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=3.00), store=store
    )

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == 2.80
    assert pir.eval_status == EvalStatus.PASSED
    assert result.overall_eval_status == EvalStatus.PASSED


def test_priced_invocation_over_threshold_fails_with_readable_message():
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
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1.00), store=store
    )

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == 2.80
    assert pir.eval_status == EvalStatus.FAILED
    assert result.overall_eval_status == EvalStatus.FAILED
    # Readable and actionable: names the actual cost and the actual
    # threshold, not just the word "FAILED".
    rationale = pir.rubric_scores[0].rationale
    assert "FAILED: cost $2.800000 exceeds the configured threshold $1.000000" in rationale


def test_missing_threshold_raises_clear_actionable_error():
    with pytest.raises(ValueError, match="requires a max-USD-per-invocation threshold"):
        CostEfficiencyEvaluator(eval_metric=EvalMetric(metric_name=METRIC_NAME), store=UsageStore())


def test_criterion_threshold_is_used_when_provided():
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
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(
            metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=1.00)
        ),
        store=store,
    )

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    assert result.per_invocation_results[0].eval_status == EvalStatus.FAILED


def test_unset_legacy_threshold_is_mirrored_from_the_resolved_criterion():
    # Never left as None (would TypeError in any downstream code assuming
    # EvalMetric.threshold is numeric) and never a fake permissive sentinel
    # either -- a 0.0 sentinel would make AgentEvaluator.evaluate()'s own
    # (ADK-side, direction-backward) pytest reclassification permanently
    # PASS for this metric regardless of real cost, which is worse than the
    # bug this package fixes. See evaluator.py's module docstring.
    eval_metric = EvalMetric(
        metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=0.05)
    )
    assert eval_metric.threshold is None

    CostEfficiencyEvaluator(eval_metric=eval_metric, store=UsageStore())

    assert eval_metric.threshold == 0.05


def test_explicit_legacy_threshold_is_not_overridden():
    eval_metric = EvalMetric(metric_name=METRIC_NAME, threshold=0.05)

    CostEfficiencyEvaluator(eval_metric=eval_metric, store=UsageStore())

    assert eval_metric.threshold == 0.05


def test_overall_eval_status_failed_dominates_a_mix():
    store = UsageStore()
    # inv-1: cheap, passes. inv-2: expensive, fails.
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000,
        ),
    )
    store.record(
        "inv-2",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1.00), store=store
    )

    result = evaluator.evaluate_invocations([_invocation("inv-1"), _invocation("inv-2")])

    assert result.per_invocation_results[0].eval_status == EvalStatus.PASSED
    assert result.per_invocation_results[1].eval_status == EvalStatus.FAILED
    assert result.overall_eval_status == EvalStatus.FAILED


def test_overall_eval_status_is_passed_when_mixed_with_an_unpriceable_invocation():
    # inv-1 prices and passes; inv-2 has no captured usage (NOT_EVALUATED).
    # Deliberately overall PASSED, not NOT_EVALUATED: LocalEvalService
    # blanks EVERY per-invocation result for this metric across the whole
    # case whenever overall_eval_status is NOT_EVALUATED (google-adk 2.6.3,
    # source-confirmed) -- letting one unpriceable invocation drag the
    # whole case to NOT_EVALUATED would destroy inv-1's genuinely-priced
    # PASSED result downstream. inv-2's own per-invocation eval_status
    # still correctly shows NOT_EVALUATED -- nothing is hidden there.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1"), _invocation("inv-2")])

    assert result.per_invocation_results[0].eval_status == EvalStatus.PASSED
    assert result.per_invocation_results[1].eval_status == EvalStatus.NOT_EVALUATED
    assert result.overall_eval_status == EvalStatus.PASSED


def test_unpriceable_invocations_still_report_not_evaluated_not_passed_or_failed():
    # A legitimate, distinct case from the Phase 1 bug: "cost could not be
    # verified" is neither a pass nor a fail.
    store = UsageStore()
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    assert result.per_invocation_results[0].eval_status == EvalStatus.NOT_EVALUATED
    assert result.overall_eval_status == EvalStatus.NOT_EVALUATED


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


def test_cached_token_discount_matches_hand_calculated_value():
    # gemini-2.5-flash: input $0.30/Mtok, output $2.50/Mtok, cache-read 0.1x
    # input. prompt_token_count (1,000,000) already includes the cached
    # portion (400,000) per Gemini's own usage_metadata semantics.
    #   fresh_tokens = 1,000,000 - 400,000 = 600,000
    #   fresh_cost   = 600,000 * 0.30 / 1e6           = 0.18
    #   cache_cost   = 400,000 * (0.30 * 0.1) / 1e6   = 0.012
    #   output_cost  = 100,000 * 2.50 / 1e6           = 0.25
    #   total        = 0.18 + 0.012 + 0.25            = 0.442
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=100_000,
            cached_content_token_count=400_000,
            total_token_count=1_100_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == pytest.approx(0.442)
    assert "cache_read=$0.012000" in pir.rubric_scores[0].rationale


def test_long_context_tier_at_exactly_the_threshold_uses_base_rate():
    # gemini-2.5-pro base rate: $1.25/Mtok input. Exactly 200,000 tokens is
    # still the <=200k tier per Google's own "<=200k" / ">200k" split.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-pro",
            prompt_token_count=200_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=200_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == pytest.approx(0.25)  # 200,000 * 1.25 / 1e6
    assert "model=gemini-2.5-pro " in pir.rubric_scores[0].rationale


def test_long_context_tier_one_token_above_threshold_uses_the_higher_rate():
    # gemini-2.5-pro long-context rate: $2.50/Mtok input -- exactly double
    # the base rate, triggered the instant prompt_token_count exceeds 200k.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-pro",
            prompt_token_count=200_001,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=200_001,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == pytest.approx(200_001 * 2.50 / 1_000_000)
    assert "model=gemini-2.5-pro-long-context" in pir.rubric_scores[0].rationale


def test_unpriced_component_reports_none_score_not_a_partial_total():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000,
            candidates_token_count=50,
            cached_content_token_count=0,
            total_token_count=1_050,
            tool_use_prompt_token_count=77,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert "tool_use_prompt" in pir.rubric_scores[0].rationale
    assert "77" in pir.rubric_scores[0].rationale
    assert result.overall_score is None


def test_price_as_of_appears_in_the_rationale():
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000,
            candidates_token_count=50,
            cached_content_token_count=0,
            total_token_count=1_050,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert "price_as_of=" in pir.rubric_scores[0].rationale
    # Never the bare "unknown" fallback for a resolvable, priced model.
    assert "price_as_of=unknown" not in pir.rubric_scores[0].rationale


def test_stale_price_warning_names_only_the_stale_model_not_a_fresh_one(monkeypatch):
    import copy
    import warnings as warnings_module
    from datetime import date, timedelta

    import adk_tracegauge._pricing as pricing_module
    import adk_tracegauge.evaluator as evaluator_module

    real_prices = pricing_module.load_gemini_prices()
    patched = copy.deepcopy(real_prices)
    old_date = (date.today() - timedelta(days=pricing_module.STALE_THRESHOLD_DAYS + 30)).isoformat()
    patched["models"]["gemini-2.5-flash"]["fetched_on"] = old_date
    # gemini-2.5-flash-lite is left at its real (fresh) fetched_on.

    monkeypatch.setattr(pricing_module, "load_gemini_prices", lambda: patched)
    monkeypatch.setattr(evaluator_module, "load_gemini_prices", lambda: patched)

    store = UsageStore()
    store.record(
        "stale-inv",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000_000,
        ),
    )
    store.record(
        "fresh-inv",
        CapturedCall(
            model_version="gemini-2.5-flash-lite",
            prompt_token_count=1_000_000,
            candidates_token_count=0,
            cached_content_token_count=0,
            total_token_count=1_000_000,
        ),
    )
    evaluator = _evaluator(store)

    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        result = evaluator.evaluate_invocations(
            [_invocation("stale-inv"), _invocation("fresh-inv")]
        )

    stale_pir, fresh_pir = result.per_invocation_results
    assert "PRICE TABLE STALE" in stale_pir.rubric_scores[0].rationale
    assert "gemini-2.5-flash" in stale_pir.rubric_scores[0].rationale
    assert "PRICE TABLE STALE" not in fresh_pir.rubric_scores[0].rationale
    assert any(
        "PRICE TABLE STALE" in str(w.message) and "gemini-2.5-flash" in str(w.message)
        for w in caught
    )


# --- Phase 3 B2: promotional pricing rationale/warnings --------------------


def test_promo_active_rationale_states_the_promo_is_active_and_its_expiry_date():
    # gemini-3.6-flash is a REAL bundled entry, genuinely inside its
    # promo window as of the date this table was fetched (promo_until
    # 2026-12-31) -- no price-table patching needed for the "active" case.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-3.6-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    rationale = pir.rubric_scores[0].rationale
    # 1M input @ $0.75/Mtok + 1M output @ $3.75/Mtok = $4.50 (the
    # PROMOTIONAL rate, since today is still within the promo window).
    assert pir.score == pytest.approx(4.50)
    assert "promotional rate, expires 2026-12-31" in rationale
    assert "promotional period ended" not in rationale


def test_promo_expired_rationale_states_standard_rate_applied_automatically(monkeypatch):
    import copy
    from datetime import date, timedelta

    import adk_tracegauge._pricing as pricing_module
    import adk_tracegauge.evaluator as evaluator_module

    real_prices = pricing_module.load_gemini_prices()
    patched = copy.deepcopy(real_prices)
    expired_date = (date.today() - timedelta(days=10)).isoformat()
    patched["models"]["gemini-3.6-flash"]["promo_until"] = expired_date
    patched["models"]["gemini-3.6-flash"]["standard_rate"] = {
        "input_usd_per_mtok": 1.50,
        "output_usd_per_mtok": 7.50,
    }

    monkeypatch.setattr(pricing_module, "load_gemini_prices", lambda: patched)
    monkeypatch.setattr(evaluator_module, "load_gemini_prices", lambda: patched)

    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-3.6-flash",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    rationale = pir.rubric_scores[0].rationale
    # 1M input @ $1.50/Mtok + 1M output @ $7.50/Mtok = $9.00 (the STANDARD
    # rate, applied automatically -- no manual table edit in this test).
    assert pir.score == pytest.approx(9.00)
    expected_phrase = (
        f"promotional period ended {expired_date}; standard rate applied automatically"
    )
    assert expected_phrase in rationale
    assert "promotional rate, expires" not in rationale


def test_promo_unknown_standard_rate_warns_inside_the_pre_expiry_window(monkeypatch):
    import copy
    import warnings as warnings_module
    from datetime import date, timedelta

    import adk_tracegauge._pricing as pricing_module
    import adk_tracegauge.evaluator as evaluator_module

    real_prices = pricing_module.load_gemini_prices()
    patched = copy.deepcopy(real_prices)
    # Inside PROMO_EXPIRY_WARNING_DAYS (14) but no standard_rate published.
    near_expiry = (date.today() + timedelta(days=3)).isoformat()
    patched["models"]["gemini-3.6-flash"]["promo_until"] = near_expiry
    patched["models"]["gemini-3.6-flash"].pop("standard_rate", None)

    monkeypatch.setattr(pricing_module, "load_gemini_prices", lambda: patched)
    monkeypatch.setattr(evaluator_module, "load_gemini_prices", lambda: patched)

    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-3.6-flash",
            prompt_token_count=1_000,
            candidates_token_count=500,
            cached_content_token_count=0,
            total_token_count=1_500,
        ),
    )
    evaluator = _evaluator(store)

    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    rationale = pir.rubric_scores[0].rationale
    assert "PROMOTIONAL RATE EXPIRING WITHOUT A KNOWN STANDARD RATE" in rationale
    assert "gemini-3.6-flash" in rationale
    assert any(
        "PROMOTIONAL RATE EXPIRING WITHOUT A KNOWN STANDARD RATE" in str(w.message)
        and "gemini-3.6-flash" in str(w.message)
        for w in caught
    )


def test_local_model_prices_at_zero_and_passes_with_explicit_rationale(monkeypatch):
    # Phase 2 W3 / Phase 3 B1: an ASSERTED local/self-hosted model must
    # resolve to cost 0.0 with a real PASSED verdict (trivially under any
    # positive threshold) and an explicit, named rationale -- never a
    # silent default, never the old score=None behavior an unresolved
    # model gets. Since Phase 3 B1, this requires the explicit
    # ADK_TRACEGAUGE_ASSUME_LOCAL opt-in (see the fail-closed test below
    # for the un-asserted case).
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "1")
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="ollama_chat/qwen2.5:7b",
            prompt_token_count=1_000,
            candidates_token_count=500,
            cached_content_token_count=0,
            total_token_count=1_500,
        ),
    )
    evaluator = _evaluator(store, threshold_usd=0.01)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == 0.0
    assert pir.eval_status == EvalStatus.PASSED
    assert result.overall_eval_status == EvalStatus.PASSED
    rationale = pir.rubric_scores[0].rationale
    assert "local model, zero marginal cost" in rationale
    # Phase 3 B1: wording now names the explicit assertion this zero-cost
    # result required -- not identical to the old implicit-default wording.
    assert ASSUME_LOCAL_ENV_VAR in rationale


def test_local_model_passes_even_at_a_zero_threshold(monkeypatch):
    # cost <= threshold, and 0.0 <= 0.0 -- the strictest possible threshold
    # still passes a genuinely zero-cost local call.
    monkeypatch.setenv(ASSUME_LOCAL_ENV_VAR, "1")
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="vllm/mistral-7b-instruct",
            prompt_token_count=10_000,
            candidates_token_count=10_000,
            cached_content_token_count=0,
            total_token_count=20_000,
        ),
    )
    evaluator = _evaluator(store, threshold_usd=0.0)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == 0.0
    assert pir.eval_status == EvalStatus.PASSED


def test_local_model_without_opt_in_reports_not_evaluated_not_a_silent_zero(monkeypatch):
    # Phase 3 B1 (release-blocking): the core fix. Without the explicit
    # ADK_TRACEGAUGE_ASSUME_LOCAL opt-in, a local-prefixed model must NEVER
    # be priced at $0.00 -- Ollama Cloud shares the identical prefix.
    monkeypatch.delenv(ASSUME_LOCAL_ENV_VAR, raising=False)
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="ollama_chat/qwen2.5:7b",
            prompt_token_count=1_000,
            candidates_token_count=500,
            cached_content_token_count=0,
            total_token_count=1_500,
        ),
    )
    evaluator = _evaluator(store, threshold_usd=1_000.0)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score is None
    assert pir.eval_status == EvalStatus.NOT_EVALUATED
    rationale = pir.rubric_scores[0].rationale
    assert "ollama_chat/qwen2.5:7b" in rationale
    assert ASSUME_LOCAL_ENV_VAR in rationale
    assert "Ollama Cloud" in rationale


def test_litellm_prefixed_claude_model_prices_correctly_end_to_end():
    # anthropic/claude-opus-5: $5.00/Mtok input, $25.00/Mtok output.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="anthropic/claude-opus-5",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    # 1M input @ $5.00/Mtok + 1M output @ $25.00/Mtok = $30.00
    assert pir.score == 30.00
    assert "model=claude-opus-5" in pir.rubric_scores[0].rationale


def test_litellm_prefixed_gpt_model_prices_correctly_end_to_end():
    # openai/gpt-5.1: $1.25/Mtok input, $10.00/Mtok output.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="openai/gpt-5.1",
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )
    evaluator = _evaluator(store)

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    pir = result.per_invocation_results[0]
    assert pir.score == pytest.approx(11.25)
    assert "model=gpt-5.1" in pir.rubric_scores[0].rationale


def test_unresolvable_non_local_model_reports_actionable_rationale():
    # Not local, not a cloud-platform Claude/GPT route, not a recognized
    # vendor at all -- must still fail closed with the extension mechanism
    # named, not the stale "Gemini price table" wording.
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="mistral-large-latest",
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
    assert pir.eval_status == EvalStatus.NOT_EVALUATED
    rationale = pir.rubric_scores[0].rationale
    assert "mistral-large-latest" in rationale
    assert "Gemini price table" not in rationale
    assert "ADK_TRACEGAUGE_PRICE_TABLE" in rationale


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
