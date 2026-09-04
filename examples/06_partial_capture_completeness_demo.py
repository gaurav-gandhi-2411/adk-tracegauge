"""examples/06_partial_capture_completeness_demo.py — the completeness
check's real lead example: a PARTIAL drop (2 of 10 eval cases), not a total
one. Fixture construction (the eval set, agent packages, crash mechanism,
noise seeding) lives in `examples/_completeness_demo_fixture.py`, imported
below -- see that module's own docstring for what it builds and why.

WHY THIS SCRIPT EXISTS
    The README's original completeness-check demo reproduced #6951's
    num_runs=0 mechanism -- a TOTAL capture failure (0 of 1 case). A user
    already notices an empty snapshot; the check's real value is a PARTIAL
    drop that still produces a confident, "nothing looks wrong" regression
    verdict from `adk-tracegauge check`. This script runs that case: 10 real
    eval cases, 2 of which crash during inference and are silently absent
    from the captured sample, 8 of which capture normally.

WHAT THIS SCRIPT ACTUALLY DOES (all real, nothing simulated)
    1. Runs the REAL `adk eval` CLI command (`cli_eval`, via
       click.testing.CliRunner, in-process so this script's own
       DEFAULT_USAGE_STORE captures real usage) once per agent package the
       fixture module builds, against the SAME 10-case EvalSet file.
    2. Snapshots the "current" run TWO ways from the IDENTICAL captured
       store data -- WITHOUT --eval-set-file (today's default behavior) and
       WITH --eval-set-file (this feature) -- and prints both, side by side.
    3. Runs the real `adk-tracegauge check` CLI against baseline vs. the
       WITHOUT-completeness-check current snapshot, and prints the ACTUAL,
       UNEDITED output -- the regression verdict and achieved-power figure
       computed on the silently-shortened n=8, no different from a genuinely
       complete n=8 eval set.

HOW TO RUN
    .venv/Scripts/python.exe examples/06_partial_capture_completeness_demo.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from _completeness_demo_fixture import (
    BASELINE_VARIANT_SEED,
    CRASH_CASE_INDICES,
    CURRENT_VARIANT_SEED,
    N_CASES,
    REGRESSION_BUMP_PROMPT_TOKENS,
    _find_eval_history_file,
    _run_real_adk_eval_cli,
    _write_agent_package,
    _write_eval_set,
)

MIN_N = 8  # both baseline (n=10) and current (n=8, post-drop) clear this bar


def main() -> int:
    from adk_tracegauge._cli import main as tracegauge_main
    from adk_tracegauge._compat import load_eval_case_ids_by_session_id, load_expected_case_sizes
    from adk_tracegauge._store import DEFAULT_USAGE_STORE
    from adk_tracegauge.snapshot import evaluate_completeness, write_snapshot

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        eval_set_path = _write_eval_set(tmp_path, N_CASES)
        config_path = tmp_path / "test_config.json"
        config_path.write_text(
            json.dumps({"criteria": {"adk_tracegauge_cost_usd": 1_000_000.0}}), encoding="utf-8"
        )

        # --- Baseline run: all 10 cases succeed, no regression bump ---
        print(
            f"=== Running REAL `adk eval` CLI: baseline_agent_pkg (n={N_CASES} cases, crash=none) ==="
        )
        DEFAULT_USAGE_STORE.clear()
        baseline_pkg_dir = _write_agent_package(
            tmp_path, "baseline_agent_pkg", 0, set(), BASELINE_VARIANT_SEED
        )
        b_exit, b_output = _run_real_adk_eval_cli(baseline_pkg_dir, eval_set_path, config_path)
        print(
            f"(adk eval exit_code={b_exit}, {len(b_output.splitlines())} output lines, "
            f"{len(DEFAULT_USAGE_STORE.invocation_ids())} invocation(s) captured)\n"
        )
        baseline_history_path = _find_eval_history_file(tmp_path, "baseline_agent_pkg")
        baseline_history = load_eval_case_ids_by_session_id(baseline_history_path)
        baseline_snapshot_path = tmp_path / "baseline_snapshot.json"
        write_snapshot(
            DEFAULT_USAGE_STORE, baseline_snapshot_path, eval_case_ids_by_session=baseline_history
        )

        # --- Current run: case_3 and case_7 crash during inference; the other
        # 8 succeed with a real regression bump baked in. Snapshotted once
        # (one capture, one store) -- both demo halves below read the SAME
        # captured data, only the snapshot-time flags differ. ---
        print(
            f"=== Running REAL `adk eval` CLI: current_agent_pkg (n={N_CASES} cases, crash={CRASH_CASE_INDICES}) ==="
        )
        DEFAULT_USAGE_STORE.clear()
        current_pkg_dir = _write_agent_package(
            tmp_path,
            "current_agent_pkg",
            REGRESSION_BUMP_PROMPT_TOKENS,
            CRASH_CASE_INDICES,
            CURRENT_VARIANT_SEED,
        )
        c_exit, c_output = _run_real_adk_eval_cli(current_pkg_dir, eval_set_path, config_path)
        n_captured = len(DEFAULT_USAGE_STORE.invocation_ids())
        print(
            f"(adk eval exit_code={c_exit}, {len(c_output.splitlines())} output lines, "
            f"{n_captured} invocation(s) captured)\n"
        )
        current_history_path = _find_eval_history_file(tmp_path, "current_agent_pkg")
        current_history = load_eval_case_ids_by_session_id(current_history_path)

        print("=" * 78)
        print(
            f"=== WITHOUT --eval-set-file (current behavior): `adk-tracegauge check` on the silently-shortened n={n_captured} ==="
        )
        print("=" * 78)
        current_snapshot_no_check_path = tmp_path / "current_snapshot_no_completeness.json"
        snap_no_check = write_snapshot(
            DEFAULT_USAGE_STORE,
            current_snapshot_no_check_path,
            eval_case_ids_by_session=current_history,
        )
        print(
            f"adk-tracegauge snapshot: wrote {len(snap_no_check.records)} record(s) to "
            f"{current_snapshot_no_check_path.name}"
        )
        check_exit = tracegauge_main(
            [
                "check",
                "--baseline",
                str(baseline_snapshot_path),
                "--current",
                str(current_snapshot_no_check_path),
                "--min-n",
                str(MIN_N),
            ]
        )
        print(f"\nadk-tracegauge check exit code: {check_exit}\n")

        print("=" * 78)
        print(
            "=== WITH --eval-set-file (this feature): the SAME current-run capture, completeness-checked ==="
        )
        print("=" * 78)
        current_snapshot_with_check_path = tmp_path / "current_snapshot_with_completeness.json"
        expected_case_sizes = load_expected_case_sizes(eval_set_path)
        snap_with_check = write_snapshot(
            DEFAULT_USAGE_STORE,
            current_snapshot_with_check_path,
            eval_case_ids_by_session=current_history,
            expected_case_sizes=expected_case_sizes,
        )
        n_resolved = sum(1 for r in snap_with_check.records if r.eval_case_id is not None)
        print(
            f"adk-tracegauge snapshot: wrote {len(snap_with_check.records)} record(s) to "
            f"{current_snapshot_with_check_path.name}, "
            f"{n_resolved}/{len(snap_with_check.records)} record(s) resolved to a real eval_case_id "
            f"via --eval-history"
        )
        completeness = evaluate_completeness(snap_with_check, expected_case_sizes, num_runs=1)
        print(completeness.report())
        completeness_exit = (
            5
            if completeness.status == "incomplete_capture"
            else (6 if completeness.status == "wrong_eval_set" else 0)
        )
        print(f"\nexit_code: {completeness_exit}")

        return 0


if __name__ == "__main__":
    sys.exit(main())
