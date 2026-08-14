"""End-to-end: a real `AgentEvaluator.evaluate()` run, registered metric, no mocking
of ADK's own eval machinery.

This is the Phase 2 W2 regression proof: Phase 1 found `AgentEvaluator.evaluate()`
raised `AssertionError` unconditionally whenever this metric was registered,
"regardless of the actual computed cost or the threshold you configure" (the
old README's own words). If W2's redesign ever regresses back to a permanent
`NOT_EVALUATED`, this test starts failing -- it drives the real, installed,
unpatched `google-adk` package's own `AgentEvaluator.evaluate()`, not a
reimplementation of its failure-classification logic.

Also documents (test_agent_evaluator_still_misclassifies_a_well_under_budget_run
below) a residual, source-confirmed ADK-side limitation this package cannot
fix from its own code: `AgentEvaluator.evaluate()`'s pytest-style helper
(`agent_evaluator.py::_process_metrics_and_get_failures`) recomputes
PASSED/FAILED itself from raw scores and the deprecated `EvalMetric.threshold`
scalar via `mean(scores) >= threshold` -- the wrong direction for a
lower-is-better metric -- bypassing this evaluator's own real `eval_status`
entirely. See `evaluator.py`'s module docstring. `adk eval`/`LocalEvalService`
are unaffected (see `test_integration.py`/manual CLI proof in the W2 commit).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator
from google.adk.evaluation.eval_case import EvalCase, Invocation
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types as genai_types

# Ensures AgentEvaluator.evaluate() is marker-wrapped before any real call below --
# see evaluator.py's _install_agent_evaluator_marker docstring, "Known,
# mechanism-explained gap": the AgentEvaluator-directionality warning (Phase 3
# B3) can only detect a call if adk_tracegauge's wrap around
# AgentEvaluator.evaluate was installed *before* that call started -- and the
# quickstart's own pattern (adk_tracegauge imported by the *agent module*,
# loaded from *inside* the evaluate() call) misses exactly the first such call
# per process. Importing here, at test-module level, ahead of every
# AgentEvaluator.evaluate() call below, is the documented workaround -- and
# makes this file's own warning-related tests deterministic regardless of
# pytest's collection order or which other test files ran first.
import adk_tracegauge  # noqa: F401

_FIXED_COST_USD = 2.80
"""1M input + 1M output tokens on gemini-2.5-flash: 1M*$0.30/Mtok + 1M*$2.50/Mtok."""

_AGENT_MODULE_SOURCE = """
from collections.abc import AsyncGenerator

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

import adk_tracegauge  # noqa: F401 -- registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin


class _FixedCostLlm(BaseLlm):
    model: str = "fixed-cost-fake-model"

    @classmethod
    def supported_models(cls):
        return ["fixed-cost-fake-model"]

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            model_version="gemini-2.5-flash",
            content=genai_types.Content(
                parts=[genai_types.Part(text="the answer is 4")], role="model"
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1_000_000,
                candidates_token_count=1_000_000,
                cached_content_token_count=0,
                total_token_count=2_000_000,
            ),
        )


# AgentEvaluator.evaluate() builds its own bare Runner that never fires an
# App-wired plugin -- after_model_callback is the documented workaround
# (see README) for capturing usage through this specific integration path.
_usage_plugin = TraceGaugeUsagePlugin()

root_agent = LlmAgent(
    name="w2_agent_evaluator_proof_agent",
    model=_FixedCostLlm(),
    instruction="Answer the question.",
    after_model_callback=_usage_plugin.after_model_callback,
)
"""


def _write_agent_module(tmp_path: Path) -> str:
    """Writes a uniquely-named, real importable agent package and returns its dotted name.

    `AgentEvaluator._get_agent_for_eval` requires either a member named
    `agent` on the module, or a module name ending in `.agent` -- so
    root_agent lives in an `agent.py` submodule, not directly in
    `__init__.py`, and the returned dotted name is `<pkg>.agent`.
    """
    package_name = f"w2_agent_evaluator_proof_{uuid.uuid4().hex}"
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "agent.py").write_text(_AGENT_MODULE_SOURCE, encoding="utf-8")
    return f"{package_name}.agent"


def _write_eval_fixture(tmp_path: Path, threshold_usd: float) -> Path:
    eval_set = EvalSet(
        eval_set_id="w2_agent_evaluator_proof_set",
        eval_cases=[
            EvalCase(
                eval_id="case_1",
                conversation=[
                    Invocation(
                        invocation_id="w2-proof-invocation",
                        user_content=genai_types.Content(
                            parts=[genai_types.Part(text="what is 2+2?")], role="user"
                        ),
                    )
                ],
            )
        ],
    )
    eval_dir = tmp_path / "eval_data"
    eval_dir.mkdir()
    eval_set_path = eval_dir / "proof.test.json"
    eval_set_path.write_text(eval_set.model_dump_json(indent=2), encoding="utf-8")
    (eval_dir / "test_config.json").write_text(
        json.dumps({"criteria": {"adk_tracegauge_cost_usd": threshold_usd}}), encoding="utf-8"
    )
    return eval_set_path


def test_install_agent_evaluator_marker_is_idempotent():
    """`_install_agent_evaluator_marker` is called exactly once for real, as an
    `adk_tracegauge` import side effect (see `__init__.py`) -- this exercises the
    second-call-onward short-circuit directly, which nothing else in this
    session's normal control flow reaches (every test shares the one process-wide
    installation)."""
    from adk_tracegauge import evaluator as ev

    assert ev._AGENT_EVALUATOR_MARKER_INSTALLED is True
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    before = AgentEvaluator.evaluate
    ev._install_agent_evaluator_marker()
    assert AgentEvaluator.evaluate is before, "a second install must not re-wrap"


def test_install_agent_evaluator_marker_degrades_gracefully_on_failure(monkeypatch):
    """If wrapping AgentEvaluator.evaluate fails for any reason (an ADK release
    renamed/removed it, per the docstring), this must never raise -- advisory
    only, exactly like `_compat.py`'s own out-of-range-version handling."""
    from adk_tracegauge import evaluator as ev

    monkeypatch.setattr(ev, "_AGENT_EVALUATOR_MARKER_INSTALLED", False)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "google.adk.evaluation.agent_evaluator":
            raise ImportError("simulated: AgentEvaluator moved")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    ev._install_agent_evaluator_marker()  # must not raise

    assert ev._AGENT_EVALUATOR_MARKER_INSTALLED is False


@pytest.mark.asyncio
async def test_agent_evaluator_evaluate_warns_naming_the_adk_behavior_and_version():
    """Phase 3 B3, 3.2: a real runtime warning, not just a documented limitation.

    Confirms `evaluator._warn_if_running_under_agent_evaluator` actually
    fires when driven by the real, installed `AgentEvaluator.evaluate()` --
    via `pytest.warns`, not by calling the detection helper in isolation --
    and that the warning text names both the exact ADK behavior
    (`_process_metrics_and_get_failures`, `mean(scores) >= threshold`) and
    the installed `google-adk` version, so a reader doesn't have to
    cross-reference anything else to know what's happening or which
    installed version it was observed on.
    """
    import tempfile

    import google.adk as google_adk

    installed_version = getattr(google_adk, "__version__", "unknown")

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        module_name = _write_agent_module(tmp_path)
        eval_set_path = _write_eval_fixture(tmp_path, threshold_usd=0.01)

        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            with pytest.warns(UserWarning, match="AgentEvaluator.evaluate\\(\\)") as records:
                await AgentEvaluator.evaluate(
                    agent_module=module_name,
                    eval_dataset_file_path_or_dir=str(eval_set_path),
                    num_runs=1,
                    print_detailed_results=False,
                )
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop(module_name, None)
            sys.modules.pop(module_name.rsplit(".", 1)[0], None)

        directionality_warnings = [
            r for r in records if "_process_metrics_and_get_failures" in str(r.message)
        ]
        assert directionality_warnings, (
            "expected a warning naming agent_evaluator.py's "
            "_process_metrics_and_get_failures function"
        )
        message = str(directionality_warnings[0].message)
        assert "mean(scores) >= threshold" in message
        assert f"google-adk=={installed_version}" in message


@pytest.mark.asyncio
async def test_direct_evaluate_invocations_does_not_emit_the_agent_evaluator_warning():
    """The warning is specific to AgentEvaluator.evaluate() -- calling this
    evaluator directly (the same path `adk eval`/LocalEvalService use, no
    agent_evaluator.py frame on the stack) must never trip it, or the
    warning would be noise on the package's own primary, unaffected,
    documented-as-fully-correct integration path."""
    import warnings as warnings_module

    from google.adk.evaluation.eval_case import Invocation
    from google.adk.evaluation.eval_metrics import EvalMetric
    from google.genai import types as genai_types

    from adk_tracegauge._store import UsageStore
    from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator

    store = UsageStore()
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0),
        store=store,
    )
    invocation = Invocation(
        invocation_id="direct-call-invocation",
        user_content=genai_types.Content(parts=[genai_types.Part(text="hi")], role="user"),
    )

    with warnings_module.catch_warnings(record=True) as records:
        warnings_module.simplefilter("always")
        evaluator.evaluate_invocations([invocation])

    directionality_warnings = [r for r in records if "AgentEvaluator.evaluate()" in str(r.message)]
    assert not directionality_warnings, (
        f"expected no AgentEvaluator-directionality warning on a direct call, got: "
        f"{directionality_warnings}"
    )


@pytest.mark.asyncio
async def test_agent_evaluator_evaluate_completes_without_assertionerror():
    """The Phase 1 P0 regression proof.

    A threshold below the real cost makes AgentEvaluator's own (backward)
    reclassification resolve PASSED (score >= threshold), so this completes
    cleanly -- proving the old *unconditional* AssertionError (which fired
    "regardless of the actual computed cost or the threshold you configure")
    no longer holds: a threshold now exists that avoids it, which was never
    true before this redesign.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        module_name = _write_agent_module(tmp_path)
        eval_set_path = _write_eval_fixture(tmp_path, threshold_usd=0.01)

        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            # No pytest.raises -- an AssertionError here IS the test failing.
            await AgentEvaluator.evaluate(
                agent_module=module_name,
                eval_dataset_file_path_or_dir=str(eval_set_path),
                num_runs=1,
                print_detailed_results=False,
            )
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop(module_name, None)
            sys.modules.pop(module_name.rsplit(".", 1)[0], None)


@pytest.mark.asyncio
async def test_agent_evaluator_still_misclassifies_a_well_under_budget_run():
    """Documents the residual, ADK-side-only limitation (not this package's bug).

    A generous threshold ($1000, real cost $2.80 -- genuinely PASSED per this
    evaluator's own correct eval_status) still trips AgentEvaluator's own
    backward reclassification (2.80 >= 1000.0 is False), raising
    AssertionError for a run that is, by this package's own correct
    accounting, well under budget. If google-adk ever fixes
    `_process_metrics_and_get_failures` to honor each Evaluator's own
    eval_status, this test starts failing -- which would be good news,
    worth revisiting this module's docstring over.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        module_name = _write_agent_module(tmp_path)
        eval_set_path = _write_eval_fixture(tmp_path, threshold_usd=1_000.0)

        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            with pytest.raises(AssertionError, match="adk_tracegauge_cost_usd"):
                await AgentEvaluator.evaluate(
                    agent_module=module_name,
                    eval_dataset_file_path_or_dir=str(eval_set_path),
                    num_runs=1,
                    print_detailed_results=False,
                )
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop(module_name, None)
            sys.modules.pop(module_name.rsplit(".", 1)[0], None)


def test_the_documented_first_call_gap_is_real_not_a_hypothetical_caveat():
    """Proves, in a fresh subprocess, the exact gap `_install_agent_evaluator_marker`'s
    docstring documents: if `adk_tracegauge` is imported for the first time as a side
    effect of the agent module `AgentEvaluator._get_agent_for_eval` loads -- the
    quickstart's own pattern, and the only import in THIS subprocess -- the wrap
    installs too late for that same, already-in-progress `evaluate()` call, so the
    directionality warning does not fire on it. A real, mechanism-backed limitation,
    not an assumption -- reproduced here exactly as it was originally found during
    development (see the module-level `import adk_tracegauge` comment above and
    `evaluator.py`'s docstring for the full explanation and the recommended
    workaround this file itself demonstrates: importing adk_tracegauge before, not
    via, the first real AgentEvaluator.evaluate() call).

    A subprocess (not the in-process `sys.modules` tricks used elsewhere in this
    file) is required here because every other test in this session may have
    already imported `adk_tracegauge` and installed the wrap -- only a genuinely
    fresh interpreter reproduces "nothing has imported it yet."
    """
    import subprocess
    import sys
    import tempfile
    import textwrap

    agent_module_source = textwrap.dedent(
        """
        from google.adk.agents.llm_agent import LlmAgent
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types as genai_types

        import adk_tracegauge  # first import of adk_tracegauge in THIS process
        from adk_tracegauge import TraceGaugeUsagePlugin


        class _FixedCostLlm(BaseLlm):
            model: str = "fixed-cost-fake-model"

            @classmethod
            def supported_models(cls):
                return ["fixed-cost-fake-model"]

            async def generate_content_async(self, llm_request, stream: bool = False):
                yield LlmResponse(
                    model_version="gemini-2.5-flash",
                    content=genai_types.Content(
                        parts=[genai_types.Part(text="the answer is 4")], role="model"
                    ),
                    usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=1_000_000,
                        candidates_token_count=1_000_000,
                        cached_content_token_count=0,
                        total_token_count=2_000_000,
                    ),
                )


        _usage_plugin = TraceGaugeUsagePlugin()
        root_agent = LlmAgent(
            name="first_call_gap_agent",
            model=_FixedCostLlm(),
            instruction="Answer.",
            after_model_callback=_usage_plugin.after_model_callback,
        )
        """
    )

    # Embedded via repr(), not a nested triple-quoted literal -- avoids
    # textwrap.dedent's common-whitespace calculation getting confused by
    # this string's own (differently-indented) embedded lines, which is
    # exactly what broke the first version of this test (IndentationError
    # in the generated subprocess script, caught and fixed during
    # development, not left as a silent flake).
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        import tempfile
        import warnings
        from pathlib import Path

        from google.adk.evaluation.agent_evaluator import AgentEvaluator
        from google.adk.evaluation.eval_case import EvalCase, Invocation
        from google.adk.evaluation.eval_set import EvalSet
        from google.genai import types as genai_types

        _AGENT_MODULE_SOURCE = {agent_module_source!r}

        tmp_path = Path(tempfile.mkdtemp())
        package_dir = tmp_path / "first_call_gap_pkg"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "agent.py").write_text(_AGENT_MODULE_SOURCE, encoding="utf-8")

        eval_set = EvalSet(
            eval_set_id="first_call_gap_set",
            eval_cases=[
                EvalCase(
                    eval_id="case_1",
                    conversation=[
                        Invocation(
                            invocation_id="first-call-gap-invocation",
                            user_content=genai_types.Content(
                                parts=[genai_types.Part(text="hi")], role="user"
                            ),
                        )
                    ],
                )
            ],
        )
        eval_dir = tmp_path / "eval_data"
        eval_dir.mkdir()
        eval_set_path = eval_dir / "proof.test.json"
        eval_set_path.write_text(eval_set.model_dump_json(indent=2), encoding="utf-8")
        (eval_dir / "test_config.json").write_text(
            json.dumps({{"criteria": {{"adk_tracegauge_cost_usd": 0.01}}}}), encoding="utf-8"
        )

        sys.path.insert(0, str(tmp_path))

        async def main():
            with warnings.catch_warnings(record=True) as records:
                warnings.simplefilter("always")
                await AgentEvaluator.evaluate(
                    agent_module="first_call_gap_pkg.agent",
                    eval_dataset_file_path_or_dir=str(eval_set_path),
                    num_runs=1,
                    print_detailed_results=False,
                )
            found = [r for r in records if "AgentEvaluator.evaluate()" in str(r.message)]
            print("DIRECTIONALITY_WARNING_COUNT=" + str(len(found)))

        asyncio.run(main())
        """
    ).format(agent_module_source=agent_module_source)

    with tempfile.TemporaryDirectory() as script_dir_str:
        script_path = Path(script_dir_str) / "first_call_gap_script.py"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    assert result.returncode == 0, (
        f"subprocess failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "DIRECTIONALITY_WARNING_COUNT=0" in result.stdout, (
        "expected the documented first-call gap to reproduce (0 warnings on the "
        f"first, self-importing AgentEvaluator.evaluate() call), got:\n{result.stdout}"
    )
