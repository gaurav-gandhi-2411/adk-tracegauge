"""examples/03_ci_regression_gate.py — the CI regression gate, end to end.

WHAT THIS DOES
    Exercises the exact two-command pattern `docs/ci-snippet.md`'s GitHub
    Actions workflow runs: `adk-tracegauge snapshot` (captures a UsageStore's
    priced invocations to a JSON file) followed by `adk-tracegauge check`
    (bootstrap-CI-tests one snapshot against another, real exit codes
    0/1/3 -- see `_regression.py`'s module docstring for the full
    methodology).

    Two synthetic `UsageStore`s stand in for "a baseline CI run" and "a
    current CI run" -- 40 invocations each (`gemini-2.5-flash-lite`, fixed
    5,000 prompt tokens, output tokens drawn from a seeded Gaussian), with
    `build_current_store` deliberately generating output tokens with a 20%
    higher mean than `build_baseline_store` (a real, injected cost
    regression, not a hypothetical one). Deterministic seeds throughout
    (rule 40) -- the same two numbers below reproduce exactly on every run.

    Runs BOTH real `adk-tracegauge` CLI subcommands as actual subprocesses
    (not calling the Python functions directly) -- this is exactly what a
    CI step does, and this script's output is the real, unedited CLI text.

HOW TO RUN
    uv run python examples/03_ci_regression_gate.py

    To see the two subcommands run manually, exactly as CI would:
        uv run adk-tracegauge snapshot --entrypoint "03_ci_regression_gate:build_baseline_store" --output baseline.json
        uv run adk-tracegauge snapshot --entrypoint "03_ci_regression_gate:build_current_store" --output current.json
        uv run adk-tracegauge check --baseline baseline.json --current current.json
    (run from inside examples/, so `03_ci_regression_gate` is importable --
    or add examples/ to PYTHONPATH.)

EXPECTED OUTPUT (real numbers -- reproduce exactly given the seeds above;
    re-captured Phase 5 S4 for the new DEFAULT_CONFIDENCE=0.98 default (was
    0.95) -- the mean/effect numbers are unchanged (same generator/seeds),
    but the CI bounds and achieved-power figure widen slightly at the new,
    tighter confidence level, as expected)
    adk-tracegauge snapshot: wrote 40 record(s) to <tmp>/baseline.json
    adk-tracegauge snapshot: wrote 40 record(s) to <tmp>/current.json
    adk-tracegauge check: mode=two-sample (--mode auto: best-available pairing key (none) only has 0
    overlapping match(es) < --min-n=30, so falling back from paired -- see snapshot.py's docstring
    for how to enable paired comparison)
    adk-tracegauge check [method=two_sample]: n_baseline=40 n_current=40 (min_n=30)
      mean_baseline=$0.008583  mean_current=$0.009998
      achieved power: minimum reliably-detectable effect at 80% power, given this run's observed
      variance/n, is ~$0.000536 (+6.25% of mean baseline) [normal approximation to the bootstrap CI
      -- see _regression.py module docstring for validated accuracy]
      observed effect: +0.001415 USD (+16.49%), 98% CI [+0.001019, +0.001801] (n_boot=10000, seed=42)
      statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
      WARNING: the configured practical-significance floor (effectively $0.000100, from
      min_effect_usd=$0.000100 OR min_effect_pct=5.00%) is BELOW this run's minimum
      reliably-detectable effect at 80% power (~$0.000536, given the observed variance and n) --
      the statistical test cannot reliably catch a real regression as small as your configured
      floor at this sample size. A clean/passing result here should NOT be read as strong evidence
      of no regression at your configured floor -- consider a larger eval set, a lower-variance
      cost metric, or an explicitly higher floor.
      REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the configured practical-significance floor.
    adk-tracegauge check exit code: 1  (would fail the build in CI -- see docs/ci-snippet.md)
"""

from __future__ import annotations

import random
import subprocess
import sys
import tempfile
from pathlib import Path

from adk_tracegauge._store import CapturedCall, UsageStore


def _build_store(seed: int, mean_output_tokens: float) -> UsageStore:
    """40 synthetic invocations, deterministic given `seed` -- see module
    docstring. Real CapturedCall records run through the exact same
    build_session_digest -> price_digest pricing path as a real eval run
    (via `adk-tracegauge snapshot`), not raw cost floats fabricated directly.
    """
    store = UsageStore()
    rng = random.Random(seed)
    for i in range(40):
        output_tokens = max(1000, int(rng.gauss(mean_output_tokens, mean_output_tokens * 0.1)))
        store.record(
            f"inv-{seed}-{i}",
            CapturedCall(
                model_version="gemini-2.5-flash-lite",
                prompt_token_count=5_000,
                candidates_token_count=output_tokens,
                cached_content_token_count=0,
                total_token_count=5_000 + output_tokens,
            ),
        )
    return store


def build_baseline_store() -> UsageStore:
    """The `--entrypoint` this file's `adk-tracegauge snapshot` call names for
    the baseline run."""
    return _build_store(seed=1234, mean_output_tokens=20_000)


def build_current_store() -> UsageStore:
    """The `--entrypoint` this file's `adk-tracegauge snapshot` call names for
    the current run -- 20% higher mean output tokens than baseline, a real
    injected regression."""
    return _build_store(seed=42, mean_output_tokens=20_000 * 1.20)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        baseline_json = tmp_path / "baseline.json"
        current_json = tmp_path / "current.json"
        examples_dir = str(Path(__file__).parent)

        for entrypoint, output in (
            ("03_ci_regression_gate:build_baseline_store", baseline_json),
            ("03_ci_regression_gate:build_current_store", current_json),
        ):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adk_tracegauge._cli",
                    "snapshot",
                    "--entrypoint",
                    entrypoint,
                    "--output",
                    str(output),
                ],
                cwd=examples_dir,
                check=True,
            )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "adk_tracegauge._cli",
                "check",
                "--baseline",
                str(baseline_json),
                "--current",
                str(current_json),
            ],
            cwd=examples_dir,
        )
        print(f"adk-tracegauge check exit code: {result.returncode}")


if __name__ == "__main__":
    main()
