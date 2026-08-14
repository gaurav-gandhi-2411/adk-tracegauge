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
