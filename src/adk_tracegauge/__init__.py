"""adk-tracegauge — the cost regression gate for custom ADK eval harnesses.

As of Phase 2 W2, `CostEfficiencyEvaluator` reports a real per-invocation
dollar cost *and* a real PASSED/FAILED verdict against a required
max-USD-per-invocation threshold -- it no longer reports the permanent
`EvalStatus.NOT_EVALUATED` that made `AgentEvaluator.evaluate()` raise
`AssertionError` unconditionally and made `adk eval` record `score: null`
(the Phase 1 P0 finding). See `adk_tracegauge.evaluator`'s module docstring
for the full redesign, including one known ADK limitation this package
cannot fix (AgentEvaluator.evaluate()'s pytest-style helper recomputes
pass/fail from raw scores independent of eval_status -- worked around, not
solved, at construction time).

    from adk_tracegauge import TraceGaugeUsagePlugin
    from adk_tracegauge.evaluator import CostThresholdCriterion

    app = App(name="my_app", root_agent=root_agent, plugins=[TraceGaugeUsagePlugin()])
    # eval_metric=EvalMetric(metric_name=METRIC_NAME,
    #     criterion=CostThresholdCriterion(threshold=0.05))  # max $0.05/invocation

Importing this package registers the "adk_tracegauge_cost_usd" metric into
google-adk's DEFAULT_METRIC_EVALUATOR_REGISTRY as a side effect, matching
the registration pattern google-adk itself documents for third-party
metrics. If google-adk's @experimental registry API has changed
incompatibly, this import-time call fails loudly (AttributeError/TypeError)
rather than silently doing nothing -- see README, "Compatibility risk".
"""

from __future__ import annotations

from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY

from ._plugin import TraceGaugeUsagePlugin
from ._store import DEFAULT_USAGE_STORE, UsageStore
from .evaluator import (
    _METRIC_INFO,
    METRIC_NAME,
    CostEfficiencyEvaluator,
    CostThresholdCriterion,
    _install_agent_evaluator_marker,
)

DEFAULT_METRIC_EVALUATOR_REGISTRY.register_evaluator(
    metric_info=_METRIC_INFO,
    evaluator=CostEfficiencyEvaluator,
)

# Phase 3 B3: best-effort, defensive (never fails import -- see
# evaluator.py's _install_agent_evaluator_marker docstring). Unlike the
# metric registration above, this is advisory only: it enables
# evaluate_invocations()'s real-time warning when this metric is being
# evaluated under AgentEvaluator.evaluate()'s known-backward pytest-style
# harness (see evaluator.py's module docstring), and silently no-ops if
# AgentEvaluator.evaluate has moved -- it must never be the reason importing
# this package breaks.
_install_agent_evaluator_marker()

__version__ = "0.3.0"

__all__ = [
    "CostEfficiencyEvaluator",
    "CostThresholdCriterion",
    "TraceGaugeUsagePlugin",
    "UsageStore",
    "DEFAULT_USAGE_STORE",
    "METRIC_NAME",
    "__version__",
]
