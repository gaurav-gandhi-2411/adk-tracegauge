"""adk_tracegauge/evaluator.py — Per-invocation cost-in-USD evaluator for ADK.

Registers under the metric name "adk_tracegauge_cost_usd" via
google.adk.evaluation.metric_evaluator_registry.DEFAULT_METRIC_EVALUATOR_REGISTRY
(an @experimental API -- see README for the pinning/breakage story).

Design notes, because they're load-bearing and not obvious from the code:

- score is raw cost in USD, unmodified sign. This metric never calls
  get_eval_status()/never compares against a threshold -- eval_status is
  always NOT_EVALUATED. ADK's built-in `score >= threshold -> PASSED`
  convention assumes higher-is-better; cost is lower-is-better, and ADK has
  no inverted-metric convention (confirmed: no direction/polarity concept
  anywhere in google.adk.evaluation). Reporting a sign-flipped score to make
  the built-in gate "work" would misrepresent the number. Read `score`
  directly, or write your own threshold comparison against it.

- Requires TraceGaugeUsagePlugin to be wired into the same App this
  evaluator runs against (see README, "bare-agent limitation"). Without it,
  every invocation reports "no usage captured", not a cost of zero.

- An invocation whose model isn't in the Gemini price table reports
  score=None with the specific unresolved model name in the rationale --
  never a fabricated number from a fallback rate.
"""

from __future__ import annotations

from google.adk.evaluation.eval_case import ConversationScenario, Invocation
from google.adk.evaluation.eval_metrics import EvalMetric, Interval, MetricInfo, MetricValueInfo
from google.adk.evaluation.eval_rubrics import RubricScore
from google.adk.evaluation.evaluator import EvaluationResult, Evaluator, PerInvocationResult
from tes._digest import SessionDigest
from tes.cost import compute_session_cost

from ._adapter import build_session_digest, unknown_model_message
from ._pricing import load_gemini_prices
from ._store import DEFAULT_USAGE_STORE, UsageStore

METRIC_NAME = "adk_tracegauge_cost_usd"

_METRIC_INFO = MetricInfo(
    metric_name=METRIC_NAME,
    description=(
        "Dollar cost of one invocation, computed from real token usage "
        "captured by TraceGaugeUsagePlugin during inference. Raw USD, not a "
        "0-1 quality score -- always reports NOT_EVALUATED (see module "
        "docstring for why). Requires TraceGaugeUsagePlugin in your App's "
        "plugins list."
    ),
    metric_value_info=MetricValueInfo(
        interval=Interval(min_value=0.0, max_value=1_000_000.0, open_at_max=True)
    ),
)


def _no_usage_result(invocation: Invocation, expected: Invocation | None) -> PerInvocationResult:
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        rubric_scores=[
            RubricScore(
                rubric_id="cost_breakdown",
                score=None,
                rationale=(
                    "no usage captured for this invocation -- either "
                    "TraceGaugeUsagePlugin is not wired into this App's "
                    "plugins list, or eval ran against a bare root_agent "
                    "(LocalEvalService's bare-agent path never fires "
                    "plugins). See README: 'Required: wrap your agent in "
                    "an App'."
                ),
            )
        ],
    )


def _unresolved_model_result(
    invocation: Invocation, expected: Invocation | None, model_version: str
) -> PerInvocationResult:
    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=None,
        rubric_scores=[
            RubricScore(
                rubric_id="cost_breakdown",
                score=None,
                rationale=unknown_model_message(model_version),
            )
        ],
    )


def _priced_result(
    invocation: Invocation,
    expected: Invocation | None,
    digest: SessionDigest,
) -> PerInvocationResult:
    # Explicit: without this, compute_session_cost defaults to tracegauge's
    # own bundled Claude price table (tes/data/prices.json), not ours -- our
    # adapter already resolved every turn.model to a Gemini table key, but
    # tracegauge's internal _resolve_model would fail to find that key in
    # the Claude table and silently default to a Claude rate. Confirmed by
    # this bug actually happening during test development: omitting this
    # argument priced a $2.80 gemini-2.5-flash call at $18.00 (Claude
    # Sonnet's rate) with no error, only a buried "approximate" flag.
    session_cost = compute_session_cost(digest, prices=load_gemini_prices())

    breakdown_lines = [
        f"cost_usd={session_cost.total_usd:.6f}",
        f"ai_calls={session_cost.ai_turn_count}",
    ]
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

    return PerInvocationResult(
        actual_invocation=invocation,
        expected_invocation=expected,
        score=session_cost.total_usd,
        rubric_scores=[
            RubricScore(
                rubric_id="cost_breakdown",
                score=session_cost.total_usd,
                rationale="\n".join(breakdown_lines),
            )
        ],
    )


class CostEfficiencyEvaluator(Evaluator):
    """Reports real per-invocation dollar cost, sourced from tracegauge's cost engine."""

    def __init__(self, *, eval_metric: EvalMetric, store: UsageStore | None = None) -> None:
        self._eval_metric = eval_metric
        self._store = store if store is not None else DEFAULT_USAGE_STORE

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
            calls = self._store.get(actual.invocation_id)
            if not calls:
                per_invocation_results.append(_no_usage_result(actual, expected))
                continue

            adapted = build_session_digest(actual.invocation_id, calls)
            if not adapted.ok:
                per_invocation_results.append(
                    _unresolved_model_result(actual, expected, adapted.unresolved_model or "")
                )
                continue

            per_invocation_results.append(_priced_result(actual, expected, adapted.digest))

        scores: list[float] = [r.score for r in per_invocation_results if r.score is not None]
        overall_score = sum(scores) if scores else None

        return EvaluationResult(
            overall_score=overall_score,
            per_invocation_results=per_invocation_results,
        )


__all__ = ["CostEfficiencyEvaluator", "METRIC_NAME"]
