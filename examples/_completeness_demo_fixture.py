"""examples/_completeness_demo_fixture.py — fixture construction for the
partial-capture completeness demo. The runnable entry point is
`examples/06_partial_capture_completeness_demo.py` (the driver), which
imports everything below and does the actual run/compare/print -- see that
file's own docstring for the run half of this demo.

WHAT THIS MODULE BUILDS (all real, nothing simulated)
    1. A real EvalSet JSON file with 10 eval cases (case_0..case_9)
       (`_write_eval_set`).
    2. Two agent packages -- "baseline" (all 10 cases succeed) and "current"
       (case_3 and case_7 raise inside generate_content_async, simulating a
       crashed inference -- the exact mechanism google-adk's own
       `_perform_inference_single_eval_item` catches per-case: `except
       Exception` sets `InferenceStatus.FAILURE`, `actual_invocations` stays
       None, and `_evaluate_single_inference_result` still writes an
       EvalCaseResult with `final_eval_status=FAILED` and the session_id
       that was allocated before inference ran -- confirmed by reading
       `local_eval_service.py` directly, and verified to run byte-identically
       (same mean costs, achieved power, CI bounds, missing case IDs) against
       BOTH this repo's own pinned dependency range (google-adk==2.6.3, PyPI)
       and a dedicated venv built from a pinned `google/adk-python`
       `origin/main` checkout (`c506ddf3`, `__version__ == 2.8.0`)
       (`_write_agent_package`). Both agents also carry a deterministic
       case-level cost, real per-case Gaussian noise (std=350 tokens,
       independently seeded per agent variant -- see
       NOISE_STD_TOKENS/BASELINE_VARIANT_SEED/CURRENT_VARIANT_SEED below for
       why this matters: a fixed constant bump alone gives every paired
       delta the same value, collapsing the bootstrap's achieved-power
       figure to a degenerate ~$0), plus a fixed regression bump on
       "current" (same generator shape as examples/04), so the driver has a
       genuine, non-synthetic regression to detect -- not a fixture built to
       only exercise the completeness path.
    3. `_run_real_adk_eval_cli` runs the REAL `adk eval` CLI command
       (`cli_eval`, via click.testing.CliRunner, in-process so the driver's
       own DEFAULT_USAGE_STORE captures real usage) against one agent
       package and the eval set from (1).
    4. `_find_eval_history_file` locates the `.evalset_result.json` file
       `adk eval` wrote for a given run, for the driver's own
       `--eval-history` join.
"""

from __future__ import annotations

from pathlib import Path

N_CASES = 10
CRASH_CASE_INDICES = {3, 7}  # exactly 2 of 10 -- a real crashed-inference case each
CASE_LEVEL_BASE_TOKENS = 5_000
CASE_LEVEL_STRIDE = 4_723  # arbitrary deterministic spread -- NOT hash(), which is
# PYTHONHASHSEED-randomized per process and would make this script's own output
# non-reproducible run to run (same rationale as examples/04).
CASE_LEVEL_MOD = 25_000
REGRESSION_BUMP_PROMPT_TOKENS = 6_000  # "current" agent's uniform per-case regression
NOISE_STD_TOKENS = 350  # real within-pair variance -- see "Why real noise" in the
# module docstring: a fixed constant bump alone gives every paired delta the exact
# same value, making the bootstrap's minimum-detectable-effect collapse to ~$0 --
# statistically correct for that degenerate input, but not representative of a real
# eval set, and the first thing a reader who understands power analysis would flag.
BASELINE_VARIANT_SEED = 1  # arbitrary, distinct integers -- NOT hash(), for the same
CURRENT_VARIANT_SEED = 2  # reason as CASE_LEVEL_STRIDE above. Distinct seeds per
# variant mean baseline's and current's per-case noise draws are INDEPENDENT (not
# perfectly correlated), which is what gives each of the 8 paired deltas its own,
# non-constant value -- the actual source of the "real within-pair variance" this
# fixture needs, not just noise for noise's sake.

_AGENT_MODULE_TEMPLATE = '''
import random
import re

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

import adk_tracegauge  # noqa: F401 -- registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin

_CASE_RE = re.compile(r"case (\\d+)")
_CRASH_CASE_INDICES = {crash_indices!r}
_VARIANT_SEED = {variant_seed}
_NOISE_STD_TOKENS = {noise_std}


def _case_level_prompt_tokens(case_idx: int) -> int:
    return {case_level_base} + (case_idx * {case_level_stride}) % {case_level_mod}


def _case_level_noise_tokens(case_idx: int) -> int:
    """Deterministic (given fixed integer inputs -- NOT Python's hash(), which
    is PYTHONHASHSEED-randomized per process) but genuinely per-case, per-agent-
    variant noise: seeding random.Random directly with an int is unaffected by
    PYTHONHASHSEED, so this reproduces byte-identically run to run, while still
    giving baseline and current INDEPENDENT draws for the same case_idx (see
    BASELINE_VARIANT_SEED/CURRENT_VARIANT_SEED's docstring in the driver
    script) -- the source of this fixture's real within-pair variance.
    """
    rng = random.Random(_VARIANT_SEED * 10_007 + case_idx)
    return round(rng.gauss(0, _NOISE_STD_TOKENS))


class _PartialCrashFakeLlm(BaseLlm):
    """A fake model that raises for {{_CRASH_CASE_INDICES}} -- a real crashed
    inference, caught by google-adk's own
    LocalEvalService._perform_inference_single_eval_item, not a mock of that
    catch. Every other case returns case-dependent token usage (base level
    plus this agent variant's own fixed regression bump, {regression_bump}
    tokens, plus real per-case Gaussian noise, std={noise_std} tokens) --
    deterministic given the fixed seeds above, but non-degenerate: baseline
    and current draw INDEPENDENT noise for the same case, so the 8 surviving
    paired deltas are 8 different values, not one repeated constant."""

    model: str = "case-dependent-fake-model"

    @classmethod
    def supported_models(cls):
        return ["case-dependent-fake-model"]

    async def generate_content_async(self, llm_request, stream: bool = False):
        text = "".join(
            p.text or ""
            for c in (llm_request.contents or [])
            for p in (c.parts or [])
        )
        match = _CASE_RE.search(text)
        case_idx = int(match.group(1)) if match else 0
        if case_idx in _CRASH_CASE_INDICES:
            raise RuntimeError(f"simulated crashed inference for case {{case_idx}}")
        prompt_tokens = max(
            1,
            _case_level_prompt_tokens(case_idx)
            + {regression_bump}
            + _case_level_noise_tokens(case_idx),
        )
        yield LlmResponse(
            model_version="gemini-2.5-flash",
            content=genai_types.Content(
                parts=[genai_types.Part(text="4")], role="model"
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt_tokens,
                candidates_token_count=50,
                cached_content_token_count=0,
                total_token_count=prompt_tokens + 50,
            ),
        )


_usage_plugin = TraceGaugeUsagePlugin()

root_agent = LlmAgent(
    name="{agent_name}",
    model=_PartialCrashFakeLlm(),
    instruction="Answer the question.",
    after_model_callback=_usage_plugin.after_model_callback,
)
'''


def _write_agent_package(
    tmp_path: Path,
    package_name: str,
    regression_bump: int,
    crash_indices: set[int],
    variant_seed: int,
) -> Path:
    pkg_dir = tmp_path / package_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("from . import agent\n", encoding="utf-8")
    (pkg_dir / "agent.py").write_text(
        _AGENT_MODULE_TEMPLATE.format(
            case_level_base=CASE_LEVEL_BASE_TOKENS,
            case_level_stride=CASE_LEVEL_STRIDE,
            case_level_mod=CASE_LEVEL_MOD,
            regression_bump=regression_bump,
            agent_name=package_name,
            crash_indices=crash_indices,
            variant_seed=variant_seed,
            noise_std=NOISE_STD_TOKENS,
        ),
        encoding="utf-8",
    )
    return pkg_dir


def _write_eval_set(tmp_path: Path, n_cases: int) -> Path:
    from google.adk.evaluation.eval_case import EvalCase, Invocation
    from google.adk.evaluation.eval_set import EvalSet
    from google.genai import types as genai_types

    eval_set = EvalSet(
        eval_set_id="partial_capture_demo_eval_set",
        eval_cases=[
            EvalCase(
                eval_id=f"case_{i}",
                conversation=[
                    Invocation(
                        user_content=genai_types.Content(
                            parts=[genai_types.Part(text=f"case {i}: what is 2+2?")],
                            role="user",
                        )
                    )
                ],
            )
            for i in range(n_cases)
        ],
    )
    eval_dir = tmp_path / "eval_data"
    eval_dir.mkdir()
    eval_set_path = eval_dir / "partial_capture_demo.evalset.json"
    eval_set_path.write_text(eval_set.model_dump_json(indent=2), encoding="utf-8")
    return eval_set_path


def _run_real_adk_eval_cli(
    agent_pkg_dir: Path, eval_set_path: Path, config_path: Path
) -> tuple[int, str]:
    """Same in-process sys.modules purge as examples/04 -- see that script's
    docstring for the exact gotcha (google-adk always loads the agent
    package under the fixed module name "agent"; without the purge, a
    second CliRunner.invoke() in the same process silently reuses the FIRST
    agent's cached module)."""
    import sys

    for name in list(sys.modules):
        if name == "agent" or name.startswith("agent."):
            del sys.modules[name]

    from click.testing import CliRunner
    from google.adk.cli.cli_tools_click import cli_eval

    runner = CliRunner()
    result = runner.invoke(
        cli_eval,
        [
            str(agent_pkg_dir),
            str(eval_set_path),
            "--config_file_path",
            str(config_path),
            "--print_detailed_results",
        ],
        catch_exceptions=False,
    )
    return result.exit_code, result.output


def _find_eval_history_file(agents_dir: Path, app_name: str) -> Path:
    history_dir = agents_dir / app_name / ".adk" / "eval_history"
    matches = sorted(history_dir.glob("*.evalset_result.json"))
    if not matches:
        raise RuntimeError(f"no .evalset_result.json file found under {history_dir}")
    return matches[-1]
