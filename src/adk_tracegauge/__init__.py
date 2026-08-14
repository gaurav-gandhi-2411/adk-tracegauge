"""adk-tracegauge — per-invocation cost-in-USD evaluator for custom ADK eval harnesses.

Not a drop-in metric for `adk eval` / `AgentEvaluator.evaluate()`: ADK's
LocalEvalService discards per-invocation results for any metric reporting
EvalStatus.NOT_EVALUATED (this metric's permanent status, since cost is
lower-is-better and has no honest fit in ADK's score>=threshold->PASSED
convention). AgentEvaluator.evaluate() raises unconditionally as a result;
adk eval doesn't raise but discards the per-invocation score/rationale too.
Filed upstream: https://github.com/google/adk-python/issues/6725. See
README, "Read this first", for the full explanation and the hand-rolled
Runner harness this package is actually meant to be used through:

    from adk_tracegauge import TraceGaugeUsagePlugin
    app = App(name="my_app", root_agent=root_agent, plugins=[TraceGaugeUsagePlugin()])

Importing this package registers the "adk_tracegauge_cost_usd" metric into
google-adk's DEFAULT_METRIC_EVALUATOR_REGISTRY as a side effect, matching
the registration pattern google-adk itself documents for third-party
metrics -- registration itself works fine; it's ADK's eval-result plumbing
downstream of it that discards the output. If google-adk's @experimental
registry API has changed incompatibly, this import-time call fails loudly
(AttributeError/TypeError) rather than silently doing nothing -- see
README, "Compatibility risk".
"""

from __future__ import annotations

from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY

from ._plugin import TraceGaugeUsagePlugin
from ._store import DEFAULT_USAGE_STORE, UsageStore
from .evaluator import _METRIC_INFO, METRIC_NAME, CostEfficiencyEvaluator

DEFAULT_METRIC_EVALUATOR_REGISTRY.register_evaluator(
    metric_info=_METRIC_INFO,
    evaluator=CostEfficiencyEvaluator,
)

__version__ = "0.2.0"

__all__ = [
    "CostEfficiencyEvaluator",
    "TraceGaugeUsagePlugin",
    "UsageStore",
    "DEFAULT_USAGE_STORE",
    "METRIC_NAME",
    "__version__",
]
