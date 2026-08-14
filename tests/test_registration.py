from __future__ import annotations

from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY

import adk_tracegauge
from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import DEFAULT_USAGE_STORE, UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator


def test_importing_the_package_registers_the_metric():
    registered_names = [
        m.metric_name for m in DEFAULT_METRIC_EVALUATOR_REGISTRY.get_registered_metrics()
    ]
    assert METRIC_NAME in registered_names


def test_registry_resolves_our_metric_to_our_evaluator_class():
    from google.adk.evaluation.eval_metrics import EvalMetric

    evaluator = DEFAULT_METRIC_EVALUATOR_REGISTRY.get_evaluator(
        EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0)
    )
    assert isinstance(evaluator, CostEfficiencyEvaluator)


def test_public_exports_are_importable():
    assert adk_tracegauge.CostEfficiencyEvaluator is CostEfficiencyEvaluator
    # Identity checks against the internal module's own symbols, matching the
    # CostEfficiencyEvaluator assertion above -- strengthened from the original
    # shallow `is not None` (Phase 1 finding D14): a real identity/type check
    # actually proves the public re-export is the same object as the source of
    # truth, not merely that __init__.py's re-export line didn't raise.
    assert adk_tracegauge.TraceGaugeUsagePlugin is TraceGaugeUsagePlugin
    assert adk_tracegauge.DEFAULT_USAGE_STORE is DEFAULT_USAGE_STORE
    assert isinstance(adk_tracegauge.DEFAULT_USAGE_STORE, UsageStore)
