"""examples/01_minimal_cost_gate.py — the quickstart pattern, as a runnable script.

WHAT THIS DOES
    Registers adk-tracegauge's cost metric with a threshold, wires
    TraceGaugeUsagePlugin into an agent via `after_model_callback` (the
    documented workaround that lets usage capture survive `adk eval`'s own
    bare-Runner construction -- see README, "Workaround for capturing usage
    inside adk eval/AgentEvaluator"), then runs the REAL `adk eval` CLI
    against it twice: once with a threshold above the real cost (PASSED),
    once below it (FAILED). Prints both real, unedited `adk eval` outputs
    and the real verdict parsed out of each.

    The agent's model is a tiny fake `BaseLlm` returning a fixed,
    deterministic token count (1M input + 1M output on `gemini-2.5-flash`,
    for a real, reproducible $2.80) instead of a live network call -- so
    this script is exactly zero-cost and needs no API key or local model
    server to run. Swap `_FixedCostLlm()` for a real model string (e.g.
    `model="gemini-2.5-flash"`, or `model=LiteLlm(model="ollama_chat/qwen2.5:7b")`
    for a $0-cost local model via Ollama) to see this against a real call.

REAL FINDING FROM RUNNING THIS SCRIPT (Phase 2 W5), worth knowing before you
wire this into CI: `adk eval`'s own PROCESS EXIT CODE does not reflect
PASSED/FAILED -- confirmed live, it is 0 in both runs below, regardless of
the printed "Overall Eval Status". The real verdict lives in `adk eval`'s
stdout table and the persisted `eval_history/*.evalset_result.json`, not in
$?. This is exactly why `tracegauge check` (examples/03) exists as a
separate step with its own real, distinguishable exit codes (0/1/3) --
don't gate a CI job on `adk eval`'s exit code alone.

HOW TO RUN
    uv run python examples/01_minimal_cost_gate.py

    (Or, from an installed `pip install adk-tracegauge` environment:
    `python examples/01_minimal_cost_gate.py`.)

EXPECTED OUTPUT (abridged -- the real run also prints ADK's own startup
warnings and a full results table; see README's "Real terminal captures"
section for the complete, unedited output this exact script produces)
    === Run 1: threshold=$5.00 (above the real $2.80 cost) ===
    ...
    Overall Eval Status: PASSED
    Metric: adk_tracegauge_cost_usd, Status: PASSED, Score: 2.8, Threshold: 5.0
    parsed verdict: PASSED (adk eval process exit code was 0)

    === Run 2: threshold=$1.00 (below the real $2.80 cost) ===
    ...
    Overall Eval Status: FAILED
    Metric: adk_tracegauge_cost_usd, Status: FAILED, Score: 2.8, Threshold: 1.0
    parsed verdict: FAILED (adk eval process exit code was ALSO 0 -- see
    "REAL FINDING" above)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_AGENT_MODULE_SOURCE = '''
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

import adk_tracegauge  # noqa: F401 -- registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin


class _FixedCostLlm(BaseLlm):
    """A fake model returning a fixed, deterministic token count -- swap
    for a real model string (e.g. "gemini-2.5-flash", or a LiteLlm-wrapped
    Ollama model) to price a real call instead."""

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


# adk eval/AgentEvaluator build their own bare Runner and never fire an
# App-wired plugin -- after_model_callback is the documented workaround
# that survives it (see README).
_usage_plugin = TraceGaugeUsagePlugin()

root_agent = LlmAgent(
    name="quickstart_agent",
    model=_FixedCostLlm(),
    instruction="Answer the question.",
    after_model_callback=_usage_plugin.after_model_callback,
)
'''


def _build_eval_fixture(tmp_path: Path) -> Path:
    """Writes a real, importable agent package + a real EvalSet JSON file,
    using ADK's own Pydantic models to serialize the eval set (never
    hand-written JSON that could silently drift from the real schema)."""
    from google.adk.evaluation.eval_case import EvalCase, Invocation
    from google.adk.evaluation.eval_set import EvalSet
    from google.genai import types as genai_types

    agent_pkg = tmp_path / "quickstart_agent_pkg"
    agent_pkg.mkdir()
    (agent_pkg / "__init__.py").write_text("from . import agent\n", encoding="utf-8")
    (agent_pkg / "agent.py").write_text(_AGENT_MODULE_SOURCE, encoding="utf-8")

    eval_set = EvalSet(
        eval_set_id="quickstart_eval_set",
        eval_cases=[
            EvalCase(
                eval_id="case_1",
                conversation=[
                    Invocation(
                        invocation_id="quickstart-invocation",
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
    eval_set_path = eval_dir / "quickstart.evalset.json"
    eval_set_path.write_text(eval_set.model_dump_json(indent=2), encoding="utf-8")
    return eval_set_path


def _run_adk_eval(tmp_path: Path, eval_set_path: Path, threshold_usd: float) -> tuple[int, str]:
    config_path = tmp_path / f"test_config_{threshold_usd}.json"
    config_path.write_text(
        json.dumps({"criteria": {"adk_tracegauge_cost_usd": threshold_usd}}), encoding="utf-8"
    )
    # Invoked exactly the way the installed `adk` console script does
    # (`google.adk.cli:main`, per its own pyproject.toml console_scripts
    # entry) -- not `python -m google.adk.cli`, which is equivalent here but
    # less obviously "the real CLI a user would type".
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from google.adk.cli import main; main()",
            "eval",
            "quickstart_agent_pkg",
            str(eval_set_path),
            "--config_file_path",
            str(config_path),
            "--print_detailed_results",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    print(result.stderr)
    return result.returncode, result.stdout


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        eval_set_path = _build_eval_fixture(tmp_path)

        print("=== Run 1: threshold=$5.00 (above the real $2.80 cost) ===")
        exit_code, stdout = _run_adk_eval(tmp_path, eval_set_path, threshold_usd=5.00)
        verdict = "PASSED" if "Overall Eval Status: PASSED" in stdout else "FAILED"
        print(f"parsed verdict: {verdict} (adk eval process exit code was {exit_code})\n")

        print("=== Run 2: threshold=$1.00 (below the real $2.80 cost) ===")
        exit_code, stdout = _run_adk_eval(tmp_path, eval_set_path, threshold_usd=1.00)
        verdict = "PASSED" if "Overall Eval Status: PASSED" in stdout else "FAILED"
        print(f"parsed verdict: {verdict} (adk eval process exit code was ALSO {exit_code})")


if __name__ == "__main__":
    main()
