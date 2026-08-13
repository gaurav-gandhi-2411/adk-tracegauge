"""adk-tracegauge — per-invocation cost-in-USD evaluator for the Agent Development Kit.

Importing this package registers the "adk_tracegauge_cost_usd" metric into
google-adk's DEFAULT_METRIC_EVALUATOR_REGISTRY as a side effect, matching
the registration pattern google-adk itself documents for third-party
metrics. If google-adk's @experimental registry API has changed
incompatibly, this import-time call fails loudly (AttributeError/TypeError)
rather than silently doing nothing -- see README, "Compatibility risk".

Required setup (not optional -- see README for why):

    from adk_tracegauge import TraceGaugeUsagePlugin
    app = App(agent=root_agent, plugins=[TraceGaugeUsagePlugin()])

Then reference "adk_tracegauge_cost_usd" as a metric_name in your eval_config.json.
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

__version__ = "0.1.0rc1"

__all__ = [
    "CostEfficiencyEvaluator",
    "TraceGaugeUsagePlugin",
    "UsageStore",
    "DEFAULT_USAGE_STORE",
    "METRIC_NAME",
    "__version__",
]
