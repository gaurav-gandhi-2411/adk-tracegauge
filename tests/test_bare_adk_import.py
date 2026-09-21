"""The base install is bare ``google-adk``: importing the package must work when google-adk's metric
registry cannot be imported (its ``[eval]`` extra -- pandas & co. -- is missing), and must still fail
loudly for every OTHER problem with that registry.

Each case runs in a fresh interpreter because the behaviour is decided at ``import adk_tracegauge``
time and this test process already has the real registry (and pandas) loaded. A missing extra is
simulated by putting ``None`` in ``sys.modules['pandas']`` before the import, which is exactly what
google-adk's registry hits on a bare install (``vertex_ai_eval_facade`` imports pandas).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

ADK_EVAL_LINE = "install adk-tracegauge[eval] to use this metric with adk eval"


def _run(code: str, prefix: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed argv, test-controlled code
        [sys.executable, "-W", "ignore", "-c", prefix + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


_BLOCK_PANDAS = "import sys; sys.modules['pandas'] = None\n"


def test_import_succeeds_and_records_why_when_the_registry_needs_a_missing_module():
    r = _run(
        """
        import adk_tracegauge
        print(adk_tracegauge.EVAL_REGISTRATION_SKIPPED_REASON)
        print(adk_tracegauge.TraceGaugeUsagePlugin.__name__)
        """,
        prefix=_BLOCK_PANDAS,
    )
    assert r.returncode == 0, r.stderr
    reason, plugin = r.stdout.strip().splitlines()[-2:]
    assert "ModuleNotFoundError" in reason and "pandas" in reason
    assert plugin == "TraceGaugeUsagePlugin"


def test_metric_is_registered_when_the_registry_is_importable():
    r = _run(
        """
        import adk_tracegauge
        from google.adk.evaluation.eval_metrics import EvalMetric
        from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY
        from adk_tracegauge.evaluator import METRIC_NAME, CostThresholdCriterion

        print(adk_tracegauge.EVAL_REGISTRATION_SKIPPED_REASON)
        metric = EvalMetric(metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=1.0))
        print(type(DEFAULT_METRIC_EVALUATOR_REGISTRY.get_evaluator(metric)).__name__)
        """
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-2:] == ["None", "CostEfficiencyEvaluator"]


_STUB_REGISTRY = """
import sys, types
stub = types.ModuleType("google.adk.evaluation.metric_evaluator_registry")
{body}
sys.modules["google.adk.evaluation.metric_evaluator_registry"] = stub
"""


def test_attribute_error_from_the_adk_registration_call_is_not_swallowed():
    # ADK's @experimental registry API changed: the registry imports fine but has no
    # register_evaluator. That is an API break and must fail the import, not be skipped.
    r = _run(
        _STUB_REGISTRY.format(body="stub.DEFAULT_METRIC_EVALUATOR_REGISTRY = object()")
        + "import adk_tracegauge\n"
    )
    assert r.returncode != 0
    assert "AttributeError" in r.stderr and "register_evaluator" in r.stderr


def test_import_error_for_a_moved_registry_symbol_is_not_swallowed():
    # Only ModuleNotFoundError is tolerated; "cannot import name" is a plain ImportError.
    r = _run(_STUB_REGISTRY.format(body="pass") + "import adk_tracegauge\n")
    assert r.returncode != 0
    assert "ImportError" in r.stderr and "cannot import name" in r.stderr
    assert "ModuleNotFoundError" not in r.stderr.splitlines()[-1]


def test_adk_eval_path_prints_one_actionable_line_when_registration_was_skipped():
    r = _run(
        """
        import sys
        import google.adk.cli  # what `adk eval` has loaded by the time the agent module imports us
        sys.argv = ["adk", "eval", "my_agent", "my.evalset.json"]
        import adk_tracegauge
        """,
        prefix=_BLOCK_PANDAS,
    )
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stderr.splitlines() if ADK_EVAL_LINE in ln]
    assert len(lines) == 1, r.stderr
    assert "pandas" in lines[0]


def test_agent_evaluator_path_prints_the_line_once_before_adk_fails_on_its_own_import():
    # AgentEvaluator.evaluate() is the programmatic eval path. On a bare install ADK itself then
    # fails importing its registry (pandas); our line has already told the user what to install.
    r = _run(
        """
        import asyncio
        import adk_tracegauge
        from google.adk.evaluation.agent_evaluator import AgentEvaluator

        for _ in range(2):  # twice: the hint must appear once per process, not once per call
            try:
                asyncio.run(
                    AgentEvaluator.evaluate(agent_module="no_such_module", eval_dataset_file_path_or_dir="x")
                )
            except BaseException as e:  # noqa: BLE001 -- expected to fail on bare ADK
                print("raised", type(e).__name__)
        """,
        prefix=_BLOCK_PANDAS,
    )
    assert r.returncode == 0, r.stderr
    assert r.stderr.count(ADK_EVAL_LINE) == 1, r.stderr
    assert r.stdout.count("raised") == 2


def test_no_adk_eval_line_outside_adk_eval():
    # A `report` / plugin user on the base install must not be nagged about a feature they don't use.
    r = _run(_BLOCK_PANDAS + "import adk_tracegauge\n")
    assert r.returncode == 0, r.stderr
    assert ADK_EVAL_LINE not in r.stderr


def test_no_adk_eval_line_when_the_registry_is_available():
    r = _run(
        """
        import sys
        import google.adk.cli
        sys.argv = ["adk", "eval", "my_agent"]
        import adk_tracegauge
        """
    )
    assert r.returncode == 0, r.stderr
    assert ADK_EVAL_LINE not in r.stderr
