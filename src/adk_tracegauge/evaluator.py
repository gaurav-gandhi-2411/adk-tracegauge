"""adk_tracegauge/evaluator.py — Per-invocation cost-in-USD evaluator for ADK.

Registers under the metric name "adk_tracegauge_cost_usd" via
google.adk.evaluation.metric_evaluator_registry.DEFAULT_METRIC_EVALUATOR_REGISTRY
(an @experimental API -- see README for the pinning/breakage story).

Phase 2 W2 redesign, because it's load-bearing and not obvious from the code:

- **This is now a real threshold-based evaluator, not a permanent gauge.**
  Phase 1 found this metric's `eval_status` was *always* `NOT_EVALUATED`
  (never PASSED/FAILED), and that `AgentEvaluator.evaluate()`'s failure
  classifier treats `NOT_EVALUATED` identically to `FAILED` -- so
  registering this metric with `AgentEvaluator.evaluate()` raised
  `AssertionError` unconditionally, and `adk eval` recorded `score: null`.
  As of Phase 2 W2, a priceable invocation always resolves to a real
  PASSED/FAILED verdict: `score` (raw cost in USD, unchanged sign/semantic)
  is compared against a required max-USD-per-invocation threshold --
  `CostThresholdCriterion.threshold` (preferred) or the deprecated
  `EvalMetric.threshold` scalar. `score <= threshold -> PASSED`, the
  opposite comparison direction from ADK's own built-in `score >= threshold
  -> PASSED` convention (built for higher-is-better metrics) -- deliberately,
  since cost is lower-is-better and ADK has no inverted-metric/polarity
  concept anywhere in google.adk.evaluation (confirmed by source read).
  This evaluator computes PASSED/FAILED itself; it never relies on ADK's
  built-in `>=` comparison to do it correctly for a lower-is-better metric.

- **No silent always-PASS default.** Constructing this evaluator without a
  threshold (neither `criterion` nor the deprecated `threshold` field set)
  raises `ValueError` with an actionable message. A permissive default that
  always passes would be exactly the kind of gate that looks green while
  checking nothing -- unacceptable for a package now positioned as "the
  cost regression gate for ADK evals" (see PLAN.md).

- **An unpriceable invocation (no usage captured, unresolved model, a
  streaming anomaly, or an unpriced token category) still reports
  `NOT_EVALUATED`, not PASSED/FAILED.** This is a distinct, legitimate case
  from the old bug: it means "we could not verify this invocation's cost at
  all," not "cost was fine." Fail-closed, same philosophy as the rest of
  this package (never fabricate a number for what can't be priced).

- **A known ADK limitation this package cannot fully fix, found while
  proving this redesign end to end:** `AgentEvaluator.evaluate()`'s
  pytest-style helper (`agent_evaluator.py::_process_metrics_and_get_failures`,
  google-adk 2.6.3) does not read this evaluator's own `eval_status` at all
  -- it independently recomputes PASSED/FAILED from raw per-invocation
  `score` values and the *deprecated* `EvalMetric.threshold` scalar via
  `mean(scores) >= threshold`, hardcoded higher-is-better, for every
  registered metric uniformly, ALWAYS populated from the same source
  (`get_eval_metrics_from_config`, used by both `AgentEvaluator.evaluate()`
  and `adk eval`) regardless of whether the caller used a plain-float or a
  criterion-object entry in `test_config.json`. Neither of GG's two open
  upstream PRs (google/adk-python#6682, #6710) touches this function. This
  means `AgentEvaluator.evaluate()`'s own assert/no-assert exit code for
  this metric is directionally unreliable for *any* real threshold value --
  not something a sentinel value from this package can silently correct,
  since a permissive sentinel (e.g. pinning the legacy field to `0.0`,
  always `<=` any real cost) would make that harness's own gate
  permanently PASS regardless of real cost, which is worse than the
  original bug: a "regression gate" that can never fire is exactly the
  anti-pattern this whole redesign exists to avoid. This evaluator still
  fills the legacy field with the *real* resolved threshold when the
  caller leaves it unset (see `CostEfficiencyEvaluator.__init__`) purely to
  avoid a `TypeError` comparing `float >= None` -- not to make that
  harness's gate correct. `LocalEvalService`/`adk eval` are unaffected --
  they read this evaluator's real `eval_status` directly (confirmed by
  source read). Real threshold gating for the `AgentEvaluator.evaluate()`
  pytest harness must be read from this evaluator's own per-invocation
  `eval_status` (via `adk eval`/`LocalEvalService`, or by calling
  `evaluate_invocations()` directly), never from that harness's own
  pass/fail exit behavior.

- Requires TraceGaugeUsagePlugin to be wired into the same App this
  evaluator runs against (see README, "bare-agent limitation"). Without it,
  every invocation reports "no usage captured", not a cost of zero.

- An invocation whose model isn't in the Gemini price table reports
  score=None with the specific unresolved model name in the rationale --
  never a fabricated number from a fallback rate.
"""

from __future__ import annotations

import warnings
from typing import Any, ClassVar

from google.adk.evaluation.eval_case import ConversationScenario, Invocation
from google.adk.evaluation.eval_metrics import (
    BaseCriterion,
    EvalMetric,
    Interval,
    MetricInfo,
    MetricValueInfo,
)
from google.adk.evaluation.eval_rubrics import RubricScore
from google.adk.evaluation.evaluator import (
    EvalStatus,
    EvaluationResult,
    Evaluator,
    PerInvocationResult,
)
from pydantic import ValidationError
from tes._digest import SessionDigest
from tes.cost import SessionCost, compute_session_cost

from ._adapter import build_session_digest, unknown_model_message
from ._pricing import STALE_THRESHOLD_DAYS, load_gemini_prices, resolve_model
from ._store import DEFAULT_USAGE_STORE, UsageStore

METRIC_NAME = "adk_tracegauge_cost_usd"


class CostThresholdCriterion(BaseCriterion):
    """Criterion for CostEfficiencyEvaluator: max USD allowed per invocation.

    Reuses `BaseCriterion.threshold` rather than inventing a parallel field
    -- it carries the same real meaning ADK's own criterion classes give it
    ("the threshold to be used by the metric"), just interpreted here with
    the opposite comparison direction: an invocation's real dollar cost is
    PASSED when `cost <= threshold`, FAILED when `cost > threshold`. See
    the module docstring for why this differs from ADK's built-in
    `score >= threshold` convention.
    """


_METRIC_INFO = MetricInfo(
    metric_name=METRIC_NAME,
    description=(
        "Dollar cost of one invocation, computed from real token usage "
        "captured by TraceGaugeUsagePlugin during inference. Raw USD, not a "
        "0-1 quality score. Real PASSED/FAILED against a required "
        "max-USD-per-invocation threshold (CostThresholdCriterion); an "
        "invocation whose cost could not be verified (no usage captured, "
        "unresolved model, ...) reports NOT_EVALUATED, never a fabricated "
        "pass/fail. Requires TraceGaugeUsagePlugin in your App's plugins "
        "list."
    ),
    metric_value_info=MetricValueInfo(
        interval=Interval(min_value=0.0, max_value=1_000_000.0, open_at_max=True)
    ),
)


def _no_usage_result(invocation: Invocation, expected: Invocation | None) -> PerInvocationResult:
    message = (
        "no usage captured for this invocation -- either TraceGaugeUsagePlugin "
        "is not wired into this App's plugins list, or eval ran against a bare "
        "root_agent (LocalEvalService's bare-agent path never fires plugins). "
        "See README: 'The only path that reliably works'."
    )
    # This rationale can still be lost downstream: LocalEvalService blanks
    # every per-invocation result for this metric across the whole eval
    # case (not just this one) whenever the case-level overall_eval_status
    # is NOT_EVALUATED -- see _aggregate_eval_status's docstring. That only
    # happens when literally nothing in the case could be priced, but
    # warnings.warn is still the one channel guaranteed to reach a caller
    # in that scenario.
    warnings.warn(f"adk_tracegauge: {message}", stacklevel=2)
    # NOT_EVALUATED here means "cost could not be verified" -- a distinct,
    # legitimate case from Phase 1's bug (every invocation was permanently
    # NOT_EVALUATED regardless of whether it could be priced). See module
    # docstring.
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
        rubric_scores=[RubricScore(rubric_id="cost_breakdown", score=None, rationale=message)],
    )


def _unresolved_model_result(
    invocation: Invocation, expected: Invocation | None, model_version: str
) -> PerInvocationResult:
    message = unknown_model_message(model_version)
    warnings.warn(f"adk_tracegauge: {message}", stacklevel=2)
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
        rubric_scores=[RubricScore(rubric_id="cost_breakdown", score=None, rationale=message)],
    )


def _streaming_anomaly_result(
    invocation: Invocation, expected: Invocation | None, reason: str
) -> PerInvocationResult:
    message = f"cost not computed: {reason}"
    warnings.warn(f"adk_tracegauge: {message}", stacklevel=2)
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
        rubric_scores=[RubricScore(rubric_id="cost_breakdown", score=None, rationale=message)],
    )


def _unpriced_component_result(
    invocation: Invocation, expected: Invocation | None, reason: str
) -> PerInvocationResult:
    message = f"cost not computed: {reason}"
    warnings.warn(f"adk_tracegauge: {message}", stacklevel=2)
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
        rubric_scores=[RubricScore(rubric_id="cost_breakdown", score=None, rationale=message)],
    )


def _price_digest(digest: SessionDigest, *, prices: dict[str, Any]) -> SessionCost:
    """The only call site for tracegauge's compute_session_cost in this package.

    `prices` is required with no default -- deliberately, not by convention.
    tracegauge's own compute_session_cost(digest, prices=None, ...) silently
    falls back to its bundled Claude price table when prices is omitted, and
    that fallback bug actually happened here during development: omitting
    `prices=` priced a $2.80 gemini-2.5-flash call at $18.00 (Claude Sonnet's
    rate), no error, just a buried `approximate` flag. Our own adapter's
    pre-check (build_session_digest) only guards against *unresolvable*
    models -- it does nothing to stop the wrong price *table* being passed
    for an otherwise-valid model, which is exactly what happened. Routing
    every call through this one function, with `prices` required, converts
    "forgot the argument" from a silent wrong number into a TypeError. A
    regression test (test_pricing_call_site.py) asserts this is the only
    place compute_session_cost is called in src/, so a future call site
    added elsewhere can't reintroduce the same bug by skipping this wrapper.
    """
    return compute_session_cost(digest, prices=prices)


def _stale_price_warning(session_cost: SessionCost) -> str | None:
    """Returns a warning line if any priced turn's table entry is stale, else None.

    Also emits a Python warning (deduplicated by the stdlib's default
    once-per-location filter) so a stale table is visible in logs, not only
    to whoever happens to read this invocation's rationale text.
    """
    stale_keys = sorted(
        {
            tc.model_key
            for tc in session_cost.turn_costs
            if (resolved := resolve_model(tc.model_key)) is not None and resolved.is_stale
        }
    )
    if not stale_keys:
        return None

    message = (
        f"PRICE TABLE STALE: {', '.join(stale_keys)} priced from an entry "
        f"fetched more than {STALE_THRESHOLD_DAYS} days ago. Verify against "
        "https://ai.google.dev/gemini-api/docs/pricing before trusting this "
        "number -- see README 'Updating the price table'."
    )
    warnings.warn(message, stacklevel=3)
    return f"WARNING: {message}"


def _priced_result(
    invocation: Invocation,
    expected: Invocation | None,
    digest: SessionDigest,
    *,
    threshold_usd: float,
) -> PerInvocationResult:
    session_cost = _price_digest(digest, prices=load_gemini_prices())

    # The actual PASSED/FAILED verdict -- computed here, directly, never via
    # ADK's built-in score>=threshold helper (wrong direction for a
    # lower-is-better metric; see module docstring).
    eval_status = (
        EvalStatus.PASSED if session_cost.total_usd <= threshold_usd else EvalStatus.FAILED
    )

    # price_as_of travels with every reported number specifically so a
    # reader of just the rationale text (still worth carrying even now that
    # LocalEvalService reads eval_status directly -- see module docstring)
    # can tell how current the dollar figure is without cross-referencing
    # the price table file separately. One session can span multiple
    # models/tiers with different fetched_on dates (e.g. a long-context
    # call re-resolved to a different table entry); report every distinct
    # date actually used, sorted, not just one arbitrarily-picked model's
    # date.
    price_as_of_dates = sorted(
        {
            resolved.fetched_on
            for tc in session_cost.turn_costs
            if (resolved := resolve_model(tc.model_key)) is not None and resolved.fetched_on
        }
    )
    price_as_of = ",".join(price_as_of_dates) if price_as_of_dates else "unknown"

    breakdown_lines = [
        f"cost_usd={session_cost.total_usd:.6f}",
        f"threshold_usd={threshold_usd:.6f}",
        f"eval_status={eval_status.name}",
        f"ai_calls={session_cost.ai_turn_count}",
        f"price_as_of={price_as_of}",
    ]
    if eval_status == EvalStatus.FAILED:
        # A readable, actionable line naming the actual cost vs. the actual
        # threshold -- not just "FAILED" -- so a reader doesn't have to
        # parse cost_usd/threshold_usd separately to know why this failed.
        breakdown_lines.append(
            f"FAILED: cost ${session_cost.total_usd:.6f} exceeds the configured "
            f"threshold ${threshold_usd:.6f} (over by "
            f"${session_cost.total_usd - threshold_usd:.6f})"
        )
    for turn_cost in session_cost.turn_costs:
        breakdown_lines.append(
            f"  call[{turn_cost.turn_index}] model={turn_cost.model_key} "
            f"fresh_tokens={turn_cost.fresh_tokens} fresh=${turn_cost.fresh_cost:.6f} "
            f"cache_read=${turn_cost.cache_read_cost:.6f} "
            f"output=${turn_cost.output_cost:.6f} total=${turn_cost.total_usd:.6f}"
        )
    if session_cost.approximate:
        breakdown_lines.append(
            f"WARNING: approximate -- {'; '.join(session_cost.approximate_reasons)}"
        )
    if (stale_warning := _stale_price_warning(session_cost)) is not None:
        breakdown_lines.append(stale_warning)

    # warnings.warn remains a second channel alongside the (now real,
    # LocalEvalService-visible) rubric_scores/eval_status -- useful for
    # anyone driving evaluate_invocations() directly or watching logs
    # rather than reading structured per-invocation results.
    warnings.warn(
        "adk_tracegauge cost breakdown for invocation "
        f"{invocation.invocation_id}:\n" + "\n".join(breakdown_lines),
        stacklevel=2,
    )

    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=session_cost.total_usd,
        eval_status=eval_status,
        rubric_scores=[
            RubricScore(
                rubric_id="cost_breakdown",
                score=session_cost.total_usd,
                rationale="\n".join(breakdown_lines),
            )
        ],
    )


def _resolve_threshold_usd(eval_metric: EvalMetric) -> float:
    """Resolves the max-USD-per-invocation threshold this evaluator gates on.

    Prefers `eval_metric.criterion` (a CostThresholdCriterion) over the
    deprecated `eval_metric.threshold` scalar, matching ADK's own migration
    guidance (`EvalMetric.threshold`'s own docstring: "This field will be
    deprecated soon. Please use `criterion` instead"). Raises ValueError if
    neither is set -- see module docstring for why this never falls back to
    a permissive always-PASSED default.
    """
    if eval_metric.criterion is not None:
        # Mirrors TrajectoryEvaluator's own model_validate/except pattern
        # (google-adk's convention for criterion-type mismatches). Every
        # BaseCriterion subclass shares the required `threshold` field and
        # BaseCriterion itself allows extra fields (`extra="allow"`), so in
        # practice any real ADK criterion validates here regardless of its
        # concrete subclass -- this except branch is defense against a
        # future BaseCriterion subclass narrowing that contract, not
        # something reachable through today's criterion hierarchy (hence
        # not covered by a unit test; ADK's own equivalent isn't either).
        try:
            criterion = CostThresholdCriterion.model_validate(eval_metric.criterion.model_dump())
        except ValidationError as e:  # pragma: no cover
            raise ValueError(
                f"`{eval_metric.metric_name}` expects a criterion of type "
                f"`CostThresholdCriterion`, got `{type(eval_metric.criterion).__name__}`."
            ) from e
        return criterion.threshold
    if eval_metric.threshold is not None:
        return eval_metric.threshold
    raise ValueError(
        "CostEfficiencyEvaluator requires a max-USD-per-invocation threshold -- "
        "it never defaults to a permissive always-PASSED sentinel (that would "
        "be a gate that looks green while checking nothing). Pass either "
        "EvalMetric(metric_name=METRIC_NAME, criterion=CostThresholdCriterion("
        "threshold=<max_usd_per_invocation>)) (preferred) or the deprecated "
        "EvalMetric(metric_name=METRIC_NAME, threshold=<max_usd_per_invocation>)."
    )


def _aggregate_eval_status(statuses: list[EvalStatus]) -> EvalStatus:
    """Aggregates one eval case's per-invocation eval_status values into one
    overall status for this metric.

    FAILED always wins (a genuine over-threshold invocation is real
    evidence, regardless of order or what else is in the case). Otherwise
    PASSED if at least one invocation was individually confirmed under
    threshold. Deliberately *not* "PASSED only if every invocation passed":
    `LocalEvalService._evaluate_metric_for_eval_case` (google-adk 2.6.3,
    confirmed by source read) substitutes an empty PerInvocationResult for
    *every* invocation in the case, discarding all real per-invocation
    scores/eval_status for this metric, whenever this metric's
    overall_eval_status for that case is NOT_EVALUATED -- unconditionally,
    not per-invocation. A stricter "any NOT_EVALUATED invocation drags the
    whole case to NOT_EVALUATED" rule (the metric-level precedent GG's own
    upstream fix, google/adk-python#6682, established for a *different*
    aggregation -- across metrics, not across invocations within one
    metric) would, through that mechanism, blank out genuinely-priced
    PASSED/FAILED invocations in any case that also contains one
    unpriceable invocation -- destroying exactly the per-invocation
    visibility this redesign exists to surface, for a stricter case-level
    label that (per-invocation eval_status already carries the real
    NOT_EVALUATED where it belongs) buys nothing in return. Only when
    literally nothing in the case could be priced does this report
    NOT_EVALUATED (in which case there is no real per-invocation data to
    lose).
    """
    if any(status == EvalStatus.FAILED for status in statuses):
        return EvalStatus.FAILED
    if any(status == EvalStatus.PASSED for status in statuses):
        return EvalStatus.PASSED
    return EvalStatus.NOT_EVALUATED


class CostEfficiencyEvaluator(Evaluator):
    """Reports real per-invocation dollar cost with a real PASSED/FAILED verdict.

    Requires a max-USD-per-invocation threshold at construction time -- via
    `eval_metric.criterion=CostThresholdCriterion(threshold=...)` (preferred)
    or the deprecated `eval_metric.threshold=...` -- see module docstring.
    """

    criterion_type: ClassVar[type[CostThresholdCriterion]] = CostThresholdCriterion

    def __init__(self, *, eval_metric: EvalMetric, store: UsageStore | None = None) -> None:
        self._eval_metric = eval_metric
        self._store = store if store is not None else DEFAULT_USAGE_STORE
        self._threshold_usd = _resolve_threshold_usd(eval_metric)

        # Mirror the resolved threshold into the deprecated legacy field
        # when the caller left it unset (i.e. used criterion= only) --
        # purely so eval_metric.threshold is never left as None (avoiding a
        # `TypeError: '>=' not supported between float and NoneType` in any
        # downstream code, ours or third-party, that assumes every
        # registered evaluator's EvalMetric.threshold is numeric). This is
        # NOT an attempt to make AgentEvaluator.evaluate()'s own pytest-style
        # reclassification (agent_evaluator.py::_process_metrics_and_get_failures)
        # gate correctly on this metric -- see module docstring for why that
        # reclassification is directionally backward for ANY numeric
        # threshold, real or otherwise, and why a permissive sentinel
        # (e.g. 0.0, always <= any real cost) was deliberately rejected: it
        # would make that harness's own pass/fail exit code permanently
        # PASS for this metric regardless of real cost, which is worse than
        # the original bug -- a "regression gate" that can never fire is
        # exactly the anti-pattern this whole redesign exists to avoid.
        # Never override an explicit legacy threshold the caller set
        # themselves.
        if eval_metric.threshold is None:
            eval_metric.threshold = self._threshold_usd

    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: list[Invocation] | None = None,
        conversation_scenario: ConversationScenario | None = None,
    ) -> EvaluationResult:
        del conversation_scenario  # not applicable to a per-invocation cost metric.

        resolved_expected: list[Invocation | None] = (
            [None] * len(actual_invocations)
            if expected_invocations is None
            else list(expected_invocations)
        )

        per_invocation_results: list[PerInvocationResult] = []
        for actual, expected in zip(actual_invocations, resolved_expected, strict=True):
            # Includes calls captured under any invocation_id recorded (via
            # before_run_callback/after_run_callback nesting -- see _plugin.py)
            # as a descendant of this one, e.g. an AgentTool-delegated
            # sub-agent's own real model calls, which ADK's own eval-event
            # conversion never surfaces under the parent's invocation_id.
            calls = self._store.get_with_descendants(actual.invocation_id)
            if not calls:
                per_invocation_results.append(_no_usage_result(actual, expected))
                continue

            adapted = build_session_digest(actual.invocation_id, calls)
            if adapted.streaming_anomaly is not None:
                per_invocation_results.append(
                    _streaming_anomaly_result(actual, expected, adapted.streaming_anomaly)
                )
                continue
            if adapted.unpriced_component is not None:
                per_invocation_results.append(
                    _unpriced_component_result(actual, expected, adapted.unpriced_component)
                )
                continue
            if not adapted.ok:
                per_invocation_results.append(
                    _unresolved_model_result(actual, expected, adapted.unresolved_model or "")
                )
                continue

            per_invocation_results.append(
                _priced_result(actual, expected, adapted.digest, threshold_usd=self._threshold_usd)
            )

        scores: list[float] = [r.score for r in per_invocation_results if r.score is not None]
        overall_score = sum(scores) if scores else None
        overall_eval_status = _aggregate_eval_status(
            [r.eval_status for r in per_invocation_results]
        )

        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=overall_eval_status,
            per_invocation_results=per_invocation_results,
        )


__all__ = ["CostEfficiencyEvaluator", "CostThresholdCriterion", "METRIC_NAME"]
