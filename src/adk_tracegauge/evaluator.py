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

- **Phase 3 B3: this is now also a real runtime warning, not just
  documentation.** `evaluate_invocations()` detects whether it is being
  driven by `AgentEvaluator.evaluate()` specifically, and if so emits a
  `warnings.warn` naming this exact behavior and the installed `google-adk`
  version -- see `_warn_if_running_under_agent_evaluator` and
  `_install_agent_evaluator_marker`. Detection is a `contextvars.ContextVar`
  set for the duration of a real `AgentEvaluator.evaluate()` call (via a
  defensive, best-effort monkeypatch installed as an `adk_tracegauge`
  import side effect), not a call-stack check -- a call-stack walk was
  tried first and empirically failed, because `LocalEvalService.evaluate()`
  forks each eval case's evaluation into its own `asyncio.Task`
  (`asyncio.as_completed`), which discards the physical call stack back to
  whichever caller awaited it into existence, identically for both
  `AgentEvaluator.evaluate()` and `adk eval`. A `ContextVar` set *before*
  that fork survives it (Task creation copies the current context, PEP
  567); `adk eval`/`LocalEvalService` never set it. This closes the gap
  between "documented in README/tests" and "a user actually sees this
  before hitting an unexplained AssertionError."

- Requires TraceGaugeUsagePlugin to be wired into the same App this
  evaluator runs against (see README, "bare-agent limitation"). Without it,
  every invocation reports "no usage captured", not a cost of zero.

- An invocation whose model isn't in adk-tracegauge's price table reports
  score=None with the specific unresolved model name in the rationale --
  never a fabricated number from a fallback rate.

- **Phase 2 W3: multi-provider pricing, local models, and LiteLlm-prefixed
  identifiers.** This evaluator (via ``_adapter.build_session_digest`` ->
  ``_pricing.resolve_model_for_call``) now also prices Claude and current-
  generation GPT models reached through ADK's LiteLlm integration
  (``model_version`` strings like ``"anthropic/claude-opus-5"`` or
  ``"openai/gpt-5.1"``), and recognizes local/self-hosted models (Ollama,
  vLLM -- ``"ollama_chat/..."``, ``"ollama/..."``, ``"vllm/..."``) as an
  explicit, named zero-cost case: ``cost_usd=0.000000``, which trivially
  passes any positive threshold, with a per-call rationale line stating
  "local model, zero marginal cost" -- not a silent default. See
  ``_pricing.py``'s module docstring for the prefix-stripping rules
  (bedrock/vertex_ai/azure routes are deliberately NOT auto-resolved,
  since Claude/GPT pricing on those platforms can differ from first-party
  rates) and the ``ADK_TRACEGAUGE_PRICE_TABLE`` env-var extension
  mechanism for registering a custom price.
"""

from __future__ import annotations

import contextvars
import functools
import warnings
from typing import Any, ClassVar

import google.adk as _google_adk
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
from tes.cost import SessionCost

from ._adapter import build_session_digest, price_digest, unknown_model_message
from ._pricing import LOCAL_MODEL_KEY, STALE_THRESHOLD_DAYS, load_gemini_prices, resolve_model
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
    """Evaluator-local alias for ``_adapter.price_digest`` -- kept as its own
    name (rather than calling ``_adapter.price_digest`` inline at each call
    site below) purely so existing callers of this exact symbol
    (``from adk_tracegauge.evaluator import _price_digest``, used by
    ``tests/test_pricing_call_site.py``) keep working unchanged.

    `prices` remains required with no default here too -- see
    ``_adapter.price_digest``'s docstring for the full rationale (the
    single sanctioned call site for tracegauge's ``compute_session_cost``,
    now living in ``_adapter.py`` so ``snapshot.py`` (Phase 2 W4) can share
    it instead of duplicating the same wrapper).
    """
    return price_digest(digest, prices=prices)


def _promo_unknown_rate_warning(session_cost: SessionCost) -> str | None:
    """Returns a warning line if any priced turn's promotional rate is
    within its pre-expiry warning window (or already past expiry) with no
    published post-promo standard rate, else None. Same pattern as
    _stale_price_warning: also emits a Python warning (Phase 3 B2 2.3) so
    this is visible in logs, not only to a reader of this one rationale.
    """
    flagged = sorted(
        {
            tc.model_key
            for tc in session_cost.turn_costs
            if (resolved := resolve_model(tc.model_key)) is not None
            and resolved.standard_rate_warning_due
        }
    )
    if not flagged:
        return None

    message = (
        f"PROMOTIONAL RATE EXPIRING WITHOUT A KNOWN STANDARD RATE: {', '.join(flagged)} "
        "-- the post-promotional rate for this model has not been published/confirmed "
        "anywhere in the price table, so the cost reported here may silently continue "
        "at a stale promotional rate past the promo's expiry. Re-verify against the "
        "vendor's own pricing page and add a standard_rate to the entry in "
        "src/adk_tracegauge/data/gemini_prices.json -- see README 'Updating the price "
        "table'."
    )
    warnings.warn(message, stacklevel=3)
    return f"WARNING: {message}"


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
        line = (
            f"  call[{turn_cost.turn_index}] model={turn_cost.model_key} "
            f"fresh_tokens={turn_cost.fresh_tokens} fresh=${turn_cost.fresh_cost:.6f} "
            f"cache_read=${turn_cost.cache_read_cost:.6f} "
            f"output=${turn_cost.output_cost:.6f} total=${turn_cost.total_usd:.6f}"
        )
        if turn_cost.model_key == LOCAL_MODEL_KEY:
            # Explicit, named, auditable per Phase 2 W3's requirement --
            # never silently a $0.00 line indistinguishable from a genuinely
            # free-tier priced call. Phase 3 B1: wording now names the
            # explicit assertion this zero-cost result required, since it's
            # no longer an implicit/default outcome -- see module docstring.
            line += " (local model, zero marginal cost, asserted via ADK_TRACEGAUGE_ASSUME_LOCAL)"
        elif (
            resolved_for_turn := resolve_model(turn_cost.model_key)
        ) is not None and resolved_for_turn.promo_until:
            # Phase 3 B2 2.2: the promo's active/expired status and expiry
            # date must be explicit in the rationale text, not just baked
            # silently into the dollar figure above.
            if resolved_for_turn.promo_active:
                line += f" (promotional rate, expires {resolved_for_turn.promo_until})"
            else:
                line += (
                    f" (promotional period ended {resolved_for_turn.promo_until}; "
                    "standard rate applied automatically)"
                )
        breakdown_lines.append(line)
    if session_cost.approximate:
        breakdown_lines.append(
            f"WARNING: approximate -- {'; '.join(session_cost.approximate_reasons)}"
        )
    if (stale_warning := _stale_price_warning(session_cost)) is not None:
        breakdown_lines.append(stale_warning)
    if (promo_warning := _promo_unknown_rate_warning(session_cost)) is not None:
        breakdown_lines.append(promo_warning)

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


_RUNNING_UNDER_AGENT_EVALUATOR: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "adk_tracegauge_running_under_agent_evaluator", default=False
)
"""Set (via ``_install_agent_evaluator_marker``'s monkeypatch) for the
duration of a real ``AgentEvaluator.evaluate()`` call. See
``_warn_if_running_under_agent_evaluator`` for why this exists and why it is
a ``ContextVar``, not a call-stack check."""

_AGENT_EVALUATOR_MARKER_INSTALLED = False
"""Idempotency guard for ``_install_agent_evaluator_marker`` -- importing
``adk_tracegauge`` more than once in a process (re-import after
``importlib.reload``, multiple test modules, ...) must not double-wrap
``AgentEvaluator.evaluate``."""


def _installed_adk_version() -> str:
    return getattr(_google_adk, "__version__", "unknown")


def _install_agent_evaluator_marker() -> None:
    """Best-effort: wraps ``AgentEvaluator.evaluate`` to set
    ``_RUNNING_UNDER_AGENT_EVALUATOR`` for the duration of the call, so
    ``evaluate_invocations()`` can reliably detect that *this specific*
    harness is driving the current evaluation.

    **Why not a call-stack check (tried first, and why it fails):** a plain
    ``inspect.stack()`` walk for a frame inside
    ``google.adk.evaluation.agent_evaluator`` was the first approach tried
    here -- and empirically failed (confirmed live against the real,
    installed, unpatched ``google-adk==2.6.3`` in
    ``tests/test_agent_evaluator_integration.py`` during development, not a
    hypothetical concern). Root cause, source-confirmed:
    ``LocalEvalService.evaluate()`` -- which both ``AgentEvaluator.evaluate()``
    and ``adk eval`` call identically -- wraps every eval case's evaluation
    in its own ``asyncio.Task`` via ``asyncio.as_completed(evaluation_tasks)``
    (``local_eval_service.py::evaluate``). A Task's physical call stack does
    NOT include the frames of whatever awaited it into existence -- so by
    the time execution reaches ``evaluate_invocations()``, every frame from
    ``agent_evaluator.py`` (or ``cli_tools_click.py``) is already gone,
    *identically* on both the AgentEvaluator and the adk-eval path. A
    call-stack check can't distinguish the two callers because, at the
    point this code actually runs, neither caller's frames are observable
    at all.

    ``contextvars.ContextVar`` survives exactly the boundary that broke the
    stack walk: ``asyncio.Task`` creation (via ``ensure_future`` inside
    ``asyncio.as_completed``) copies the *current* context at task-creation
    time (``contextvars.copy_context()``, per PEP 567) -- so a value set
    here, before ``AgentEvaluator.evaluate()`` has done any Task-forking
    internally, propagates down into every Task it later spawns, including
    the one that eventually calls ``evaluate_invocations()``. ``adk eval``
    (which never runs through this wrapper) never sets it.

    Defensive by construction, same philosophy as ``_compat.py``: wrapped
    in ``try/except`` so an ADK release that renames/removes
    ``AgentEvaluator.evaluate`` degrades this to "no warning capability" on
    import, never a crash -- the whole point of this mechanism is to make a
    real bug more visible, not to introduce a new failure mode of its own.
    Idempotent via ``_AGENT_EVALUATOR_MARKER_INSTALLED``.

    **Known, mechanism-explained gap: the very first ``AgentEvaluator.
    evaluate()`` call in a process, if THAT SAME call is what triggers
    ``adk_tracegauge`` to be imported for the first time.** This is the
    common quickstart shape: the user's *agent module* (not their test
    file) does ``import adk_tracegauge`` (see README quickstart), and
    ``AgentEvaluator._get_agent_for_eval`` only imports the agent module
    *from inside* the already-in-progress, still-unwrapped ``evaluate()``
    call -- so this function runs (and installs the wrap) too late to
    affect the call already on the stack; reassigning
    ``AgentEvaluator.evaluate`` does not retroactively change a call
    already dispatched to the original function. Confirmed empirically
    during development (not a hypothetical caveat): a single-file pytest
    run where this is the first and only ``AgentEvaluator.evaluate()`` call
    in the process misses the warning; a second such call in the same
    process (or the same call after ``adk_tracegauge`` was already
    imported some other way -- e.g. a `conftest.py` importing it, or an
    earlier test in the same session) gets it correctly, because the wrap
    is already installed by then. Recommended workaround, documented in
    README: import ``adk_tracegauge`` explicitly at the top of your eval
    driver script or `conftest.py`, ahead of any ``AgentEvaluator.
    evaluate()`` call, rather than relying solely on the agent module's own
    import to install the wrap in time. Not fixed by also doing a stack
    walk at import time and setting the ContextVar directly (tried and
    rejected): unlike the wrap's `set`/`reset` pair, a value set at import
    time has no natural point to reset it, so it would leak `True` into
    every later, unrelated evaluation in the same process (a false
    positive on the package's own primary, unaffected `adk eval`/
    `LocalEvalService` path) -- worse than the gap it would close, and a
    hazard this package's own "never fabricate, fail closed" philosophy
    rules out.
    """
    global _AGENT_EVALUATOR_MARKER_INSTALLED
    if _AGENT_EVALUATOR_MARKER_INSTALLED:
        return

    try:
        from google.adk.evaluation.agent_evaluator import AgentEvaluator

        original = AgentEvaluator.__dict__["evaluate"].__func__

        @functools.wraps(original)
        async def _marked_evaluate(*args: Any, **kwargs: Any) -> Any:
            token = _RUNNING_UNDER_AGENT_EVALUATOR.set(True)
            try:
                return await original(*args, **kwargs)
            finally:
                _RUNNING_UNDER_AGENT_EVALUATOR.reset(token)

        # Deliberate monkeypatch, not a typo -- see this function's docstring
        # for why (ContextVar propagation requires the wrap to be installed
        # before the call, and there is no ADK-supported extension point for
        # this). mypy correctly flags reassigning a class's own method as
        # generally suspicious; suppressed here, narrowly, with the reason
        # stated rather than silenced blind.
        AgentEvaluator.evaluate = staticmethod(_marked_evaluate)  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001 -- advisory only, see docstring; never block import.
        return

    _AGENT_EVALUATOR_MARKER_INSTALLED = True


def _warn_if_running_under_agent_evaluator() -> None:
    """Runtime guard for the known ADK-side directionality bug (Phase 3 B3, 3.2).

    ``AgentEvaluator.evaluate()`` (google-adk's pytest-style harness) and
    ``adk eval``/``LocalEvalService`` both end up calling this evaluator's
    own ``evaluate_invocations()`` -- there is no ADK-side flag or context
    object handed to a registered ``Evaluator`` that distinguishes which
    caller is driving it, by design (see ``_install_agent_evaluator_marker``
    for how this package supplies one anyway).

    When ``_RUNNING_UNDER_AGENT_EVALUATOR`` reads ``True``, warns explicitly
    -- naming the exact ADK behavior
    (``agent_evaluator.py::_process_metrics_and_get_failures`` recomputes
    PASSED/FAILED from raw scores via ``mean(scores) >= threshold``,
    hardcoded higher-is-better, ignoring this evaluator's own correct
    ``eval_status``) and the installed ``google-adk`` version -- so a caller
    sees this explained *before* hitting an unexplained ``AssertionError``
    (or, worse, a silently inverted pass/fail with no signal at all). See
    the module docstring, README "Known limitations", and
    ``tests/test_agent_evaluator_integration.py``.

    Best-effort by construction: if ``_install_agent_evaluator_marker``
    could not wrap ``AgentEvaluator.evaluate`` (an ADK release renamed or
    removed it), this never fires -- a caller then only sees the
    documentation, same as before this mechanism existed, never a crash.
    Deliberately does NOT use ``warnings``' default once-per-location dedup
    as a reason to skip the check on every call: the whole point is to fire
    every time this evaluator is actually driven through the affected
    harness, and reading a ``ContextVar`` is effectively free.
    """
    if not _RUNNING_UNDER_AGENT_EVALUATOR.get():
        return
    installed = _installed_adk_version()
    warnings.warn(
        "adk_tracegauge: this evaluation is running under "
        f"AgentEvaluator.evaluate() (installed google-adk=={installed}). "
        "Its pytest-style harness "
        "(agent_evaluator.py::_process_metrics_and_get_failures) recomputes "
        "PASSED/FAILED itself from raw per-invocation scores via "
        "`mean(scores) >= threshold` -- hardcoded higher-is-better, ignoring "
        "this evaluator's own correct eval_status entirely -- which is "
        "directionally backward for this lower-is-better cost metric at ANY "
        "real threshold. Trust `adk eval`/LocalEvalService, or this "
        "evaluator's own eval_status (call evaluate_invocations() directly), "
        "for real pass/fail -- never AgentEvaluator.evaluate()'s own "
        "assert/no-assert outcome for this metric. See README "
        "'Known limitations'.",
        stacklevel=3,
    )


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

        _warn_if_running_under_agent_evaluator()

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
