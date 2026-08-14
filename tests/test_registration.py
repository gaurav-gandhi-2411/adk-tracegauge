from __future__ import annotations

from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY

import adk_tracegauge
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
    assert adk_tracegauge.TraceGaugeUsagePlugin is not None
    assert adk_tracegauge.DEFAULT_USAGE_STORE is not None
