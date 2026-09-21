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

The one exception is a *missing module*: google-adk's registry module imports
pandas & co. (its ``[eval]`` extra) at import time, so on a bare ``google-adk``
install (this package's base install) the registry cannot be imported at all.
Only that ``ModuleNotFoundError`` is tolerated -- registration is skipped, the
reason is kept in ``EVAL_REGISTRATION_SKIPPED_REASON``, everything that does not
go through ``adk eval`` (plugin, ``report``, ``snapshot``, ``check``,
``quickstart``, ``CostEfficiencyEvaluator``) keeps working, and under
``AgentEvaluator.evaluate()`` (and ``adk eval``, whenever it gets as far as importing
the agent module) one line says how to fix it -- on a bare install the ``adk eval`` CLI
itself stops earlier with its own "Eval module is not installed" error, before this
package is imported. Every other exception, including an ``AttributeError`` from the
registration call, still propagates.
"""

from __future__ import annotations

import sys
from typing import Any

from ._plugin import DoubleRegistrationError, TraceGaugeUsagePlugin
from ._store import DEFAULT_USAGE_STORE, UsageStore
from .evaluator import (
    _METRIC_INFO,
    METRIC_NAME,
    CostEfficiencyEvaluator,
    CostThresholdCriterion,
    _announce_eval_extra_missing,
    _install_agent_evaluator_marker,
)

# ModuleNotFoundError ONLY, and only around this import (see the module docstring): anything else
# -- an ImportError for a renamed symbol, an AttributeError -- means ADK's API moved and must fail
# loudly, not be mistaken for "extra not installed".
_registry: Any
try:
    from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY

    _registry = DEFAULT_METRIC_EVALUATOR_REGISTRY
    EVAL_REGISTRATION_SKIPPED_REASON: str | None = None
except ModuleNotFoundError as _registry_import_error:
    _registry = None
    EVAL_REGISTRATION_SKIPPED_REASON = (
        f"{type(_registry_import_error).__name__}: {_registry_import_error}"
    )

if _registry is not None:
    # Deliberately outside the try/except above: a failure HERE is an API break, not a missing extra.
    _registry.register_evaluator(
        metric_info=_METRIC_INFO,
        evaluator=CostEfficiencyEvaluator,
    )


def _running_under_adk_eval() -> bool:
    """True for ``adk eval ...`` (argv[1] == "eval" with ADK's CLI loaded)."""
    return sys.argv[1:2] == ["eval"] and any(m.startswith("google.adk.cli") for m in sys.modules)


if EVAL_REGISTRATION_SKIPPED_REASON is not None and _running_under_adk_eval():
    _announce_eval_extra_missing()

# Phase 3 B3: best-effort, defensive (never fails import -- see
# evaluator.py's _install_agent_evaluator_marker docstring). Unlike the
# metric registration above, this is advisory only: it enables
# evaluate_invocations()'s real-time warning when this metric is being
# evaluated under AgentEvaluator.evaluate()'s known-backward pytest-style
# harness (see evaluator.py's module docstring), and silently no-ops if
# AgentEvaluator.evaluate has moved -- it must never be the reason importing
# this package breaks.
_install_agent_evaluator_marker()

__version__ = "0.9.0"

__all__ = [
    "DoubleRegistrationError",
    "CostEfficiencyEvaluator",
    "CostThresholdCriterion",
    "TraceGaugeUsagePlugin",
    "UsageStore",
    "DEFAULT_USAGE_STORE",
    "METRIC_NAME",
    "__version__",
]
