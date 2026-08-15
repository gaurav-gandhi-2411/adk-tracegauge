"""examples/04_paired_mode_via_adk_eval_cli.py — Phase 4 R2's real end-to-end
proof: `adk-tracegauge check --mode paired` against the ACTUAL `adk eval` CLI
command, not a hand-rolled Runner harness.

WHY THIS SCRIPT EXISTS
    Phase 3 B4 shipped `--mode paired` keyed on `session_id`, believing it to
    be a stable pairing key available to any eval harness. Phase 4 R2 found
    two independent problems with that, both confirmed by reading google-adk's
    own source directly (see snapshot.py's module docstring for exact
    file:line citations):

    1. `session_id` is regenerated fresh and random on every `adk eval` run
       UNLESS the eval case's own `session_input.session_id` is authored in
       the .evalset.json file (most eval sets don't set this).
    2. INDEPENDENTLY of (1): TraceGaugeUsagePlugin's session-capture hook
       (`before_run_callback`) never fires at all during `adk eval` --
       it builds its own bare Runner with no App/Plugin wiring.

    So B4's paired mode was, in fact, unreachable for the `adk eval` CLI path
    -- this package's own documented PRIMARY workflow (see README) --
    regardless of whether session_id happened to be stable. It only ever
    worked for a hand-rolled Runner harness that explicitly pins
    `session_id` itself, which is exactly how B4's own test suite validated
    it (never against real `adk eval`).

    The fix: `EvalCase.eval_id` -- authored directly in the .evalset.json
    file, confirmed stable across every run of the same file by reading
    google-adk's `eval_case.py` -- is now the PRIMARY pairing key. It is not
    reachable from adk-tracegauge's live capture path at all (no callback
    object carries it), so it's recovered post-hoc by joining ADK's own
    persisted `.evalset_result.json` file (which DOES carry both `eval_id`
    and `session_id` per case) against adk-tracegauge's own live-captured
    `session_id` (now ALSO fixed to actually fire during `adk eval`, via
    `after_model_callback` instead of `before_run_callback`).

WHAT THIS SCRIPT ACTUALLY DOES (all real, nothing simulated)
    1. Writes a real EvalSet JSON file with 32 eval cases (n=32, above the
       real default --min-n=30 -- this is a genuine, gate-passing paired
       verdict, not a demo that bypasses the real refusal floor).
    2. Writes TWO agent packages -- "baseline" and "current" -- each wired
       with `after_model_callback=plugin.after_model_callback` (the
       documented quickstart mechanism), using a fake `BaseLlm` whose
       per-case token count is DETERMINISTIC and CASE-DEPENDENT (real
       case-to-case cost heterogeneity, deliberately -- see B4's own
       case-correlated generator rationale in tests/test_regression_power.py
       for why this is the realistic shape a pairing key is meant to catch).
       The "current" agent adds a fixed per-call token bump on top of every
       case's own baseline level -- a real, uniform regression.
    3. Runs the REAL `adk eval` CLI command -- literally `cli_eval`, the
       exact Click command `adk eval` invokes, via `click.testing.CliRunner`
       (in-process so this script's own DEFAULT_USAGE_STORE captures real
       usage during the call -- NOT a subprocess, and NOT a reimplementation
       of any part of `adk eval`) -- once against the baseline agent, once
       against the current agent, both against the SAME EvalSet file.
    4. After each run, locates the REAL `.evalset_result.json` file `adk
       eval` wrote to `<agents_dir>/<app_name>/.adk/eval_history/` and
       prints, per case, whether `session_id` matches or differs between the
       two runs (empirical proof of 2.1/2.3's finding) alongside `eval_id`
       (stable both times).
    5. Snapshots each run via `adk-tracegauge snapshot --eval-history <path>`
       (the real CLI subcommand, in-process via `_cli.main()`), then runs
       `adk-tracegauge check --mode paired` (also the real CLI) against the two
       snapshots and prints the ACTUAL, UNEDITED output.

HOW TO RUN
    uv run python examples/04_paired_mode_via_adk_eval_cli.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

N_CASES = 32  # above the real default --min-n=30 -- a genuine paired verdict
CASE_LEVEL_BASE_TOKENS = 5_000
CASE_LEVEL_STRIDE = 4_723  # arbitrary deterministic spread -- NOT hash(), which
# is PYTHONHASHSEED-randomized per process and would make this script's own
# output non-reproducible run to run.
CASE_LEVEL_MOD = 25_000
REGRESSION_BUMP_PROMPT_TOKENS = 6_000  # the "current" agent's uniform per-case regression

_AGENT_MODULE_TEMPLATE = '''
import re

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

import adk_tracegauge  # noqa: F401 -- registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin

_CASE_RE = re.compile(r"case (\\d+)")


def _case_level_prompt_tokens(case_idx: int) -> int:
    return {case_level_base} + (case_idx * {case_level_stride}) % {case_level_mod}


class _CaseDependentFakeLlm(BaseLlm):
    """A fake model whose token usage is DETERMINISTIC and depends on which
    eval case it was asked -- real case-to-case cost heterogeneity, plus a
    fixed per-call regression bump ({regression_bump} tokens) baked into
    this specific agent variant. No network call, no real cost."""

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
        prompt_tokens = _case_level_prompt_tokens(case_idx) + {regression_bump}
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
    model=_CaseDependentFakeLlm(),
    instruction="Answer the question.",
    after_model_callback=_usage_plugin.after_model_callback,
)
'''


def _write_agent_package(tmp_path: Path, package_name: str, regression_bump: int) -> Path:
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
        ),
        encoding="utf-8",
    )
    return pkg_dir


def _write_eval_set(tmp_path: Path, n_cases: int) -> Path:
    from google.adk.evaluation.eval_case import EvalCase, Invocation
    from google.adk.evaluation.eval_set import EvalSet
    from google.genai import types as genai_types

    eval_set = EvalSet(
        eval_set_id="r2_paired_proof_eval_set",
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
    eval_set_path = eval_dir / "r2_paired_proof.evalset.json"
    eval_set_path.write_text(eval_set.model_dump_json(indent=2), encoding="utf-8")
    return eval_set_path


def _run_real_adk_eval_cli(
    tmp_path: Path, agent_pkg_dir: Path, eval_set_path: Path, config_path: Path
) -> tuple[int, str]:
    """Invokes the REAL `adk eval` Click command -- `cli_eval`, the literal
    function `adk eval` runs -- via click.testing.CliRunner, IN-PROCESS (not
    a subprocess) so this script's own adk_tracegauge.DEFAULT_USAGE_STORE
    captures real usage during the call via after_model_callback.

    Real gotcha found while building this script, worth documenting: ADK's
    own `_get_agent_module` (google/adk/cli/cli_eval.py) always loads the
    agent package under the FIXED module name "agent" (not a name derived
    from the package's own directory), via `importlib.util.spec_from_file_location`.
    Reassigning `sys.modules["agent"]` alone is NOT enough to force a fresh
    reload across two different agent packages in the SAME process (this
    script's whole point -- two runs, one process, so the usage store
    survives into the snapshot step): `__init__.py`'s own `from . import
    agent` relative import resolves against `sys.modules["agent.agent"]`,
    which stays cached from the FIRST run and is silently reused for the
    SECOND -- confirmed live: without the explicit sys.modules purge below,
    both runs measured byte-identical costs despite genuinely different
    agent source. `python -m adk_tracegauge._cli`/a real `adk eval` shell
    invocation never hits this, since each is a fresh process -- it is
    purely an artifact of running two in-process CliRunner invocations back
    to back for this proof script's own sake.
    """
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
    return matches[-1]  # newest, if somehow more than one


def _session_ids_by_eval_id(history_path: Path) -> dict[str, str]:
    """The inverse direction of _compat.load_eval_case_ids_by_session_id --
    used only by this script's own printed evidence for 2.1/2.3 (session_id
    differs run to run; eval_id does not), not by the real pairing pipeline."""
    raw = json.loads(history_path.read_text(encoding="utf-8"))
    return {case["eval_id"]: case["session_id"] for case in raw["eval_case_results"]}


def main() -> None:
    from adk_tracegauge._cli import main as tracegauge_main
    from adk_tracegauge._compat import load_eval_case_ids_by_session_id
    from adk_tracegauge._store import DEFAULT_USAGE_STORE
    from adk_tracegauge.snapshot import write_snapshot

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        eval_set_path = _write_eval_set(tmp_path, N_CASES)
        config_path = tmp_path / "test_config.json"
        config_path.write_text(
            json.dumps({"criteria": {"adk_tracegauge_cost_usd": 1_000_000.0}}), encoding="utf-8"
        )

        runs = [
            ("baseline_agent_pkg", 0, tmp_path / "baseline_snapshot.json"),
            (
                "current_agent_pkg",
                REGRESSION_BUMP_PROMPT_TOKENS,
                tmp_path / "current_snapshot.json",
            ),
        ]
        history_paths: dict[str, Path] = {}

        for app_name, regression_bump, snapshot_path in runs:
            print(f"=== Running REAL `adk eval` CLI: {app_name} (n={N_CASES} cases) ===")
            DEFAULT_USAGE_STORE.clear()
            agent_pkg_dir = _write_agent_package(tmp_path, app_name, regression_bump)

            exit_code, output = _run_real_adk_eval_cli(
                tmp_path, agent_pkg_dir, eval_set_path, config_path
            )
            print(f"(adk eval exit_code={exit_code}, {len(output.splitlines())} output lines)")

            history_path = _find_eval_history_file(tmp_path, app_name)
            history_paths[app_name] = history_path
            print(f"eval-history file: {history_path.relative_to(tmp_path)}")

            eval_case_ids_by_session = load_eval_case_ids_by_session_id(history_path)
            write_snapshot(
                DEFAULT_USAGE_STORE,
                snapshot_path,
                eval_case_ids_by_session=eval_case_ids_by_session,
            )
            print(f"wrote snapshot: {snapshot_path.name}\n")

        print("=== 2.1/2.3 empirical proof: session_id regenerates, eval_id does not ===")
        baseline_sessions = _session_ids_by_eval_id(history_paths["baseline_agent_pkg"])
        current_sessions = _session_ids_by_eval_id(history_paths["current_agent_pkg"])
        sample_ids = sorted(baseline_sessions)[:3]
        for eval_id in sample_ids:
            b_sess = baseline_sessions[eval_id]
            c_sess = current_sessions[eval_id]
            print(
                f"  {eval_id}: session_id run1={b_sess!r} run2={c_sess!r} "
                f"(differ={b_sess != c_sess})"
            )
        all_differ = all(
            baseline_sessions[eid] != current_sessions[eid] for eid in baseline_sessions
        )
        print(f"  ALL {len(baseline_sessions)} session_ids differ between run1/run2: {all_differ}")
        print(
            f"  (eval_id set is IDENTICAL both runs: {set(baseline_sessions) == set(current_sessions)})\n"
        )

        print(
            "=== Real `adk-tracegauge check --mode paired` output (against the two ADK-eval-CLI-produced snapshots) ==="
        )
        exit_code = tracegauge_main(
            [
                "check",
                "--baseline",
                str(tmp_path / "baseline_snapshot.json"),
                "--current",
                str(tmp_path / "current_snapshot.json"),
                "--mode",
                "paired",
            ]
        )
        print(f"\nadk-tracegauge check exit code: {exit_code}")


if __name__ == "__main__":
    main()
