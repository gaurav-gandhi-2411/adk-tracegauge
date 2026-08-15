"""scripts/measure_regression_confidence_grid.py — Phase 7 U2, 2.1/2.2: FPR/power
across ``confidence`` x ``n`` x ``true effect``, for BOTH two-sample and paired
mode, with a real confidence interval (Wilson score) on every reported detection
rate -- not a bare point estimate.

**Why this exists**: Phase 7 U1 made PAIRED mode the DEFAULT ``--mode auto``
preference whenever a pairing key resolves (the common case for the primary
``adk eval`` documented workflow, per Phase 4 R2). Every prior alpha/confidence
decision (Phase 5 S4's choice of ``DEFAULT_CONFIDENCE=0.98``) was made using
ONLY two-sample data, before paired-by-default existed, and used single-run
detection-rate point estimates with no confidence interval. This script closes
both gaps: it re-measures the deciding cells at >=2,000 trials/cell (versus
Phase 5 S4's 500) for BOTH modes side by side, at the SAME (confidence, n,
effect) grid, so the U2 2.3 re-decision has real, comparable, uncertainty-
quantified evidence for both the two-sample fallback path and the paired
default path.

**Grid** (U2's own explicit spec, not a re-derivation): ``confidence`` in
{0.95, 0.98, 0.99} x ``n`` in {30, 50} x true effect in {0%, 10%, 25%} = 18
cells per mode, 36 cells total, >=2,000 trials/cell (>=72,000 simulated
``check`` calls minimum). ``n=30`` is ``MIN_N_DEFAULT`` itself (the smallest n
a real ``check`` run will ever actually evaluate under); ``n=50`` is the value
Phase 6 T4 found "marginal" for two-sample at the OLD single-run measurement
convention -- both are exactly the cells a confidence-interval re-measurement
is most useful for, since they sit closest to a decision boundary.

**Generators** (deliberately DIFFERENT per mode, reusing each mode's own
already-validated generator, NOT a shared one -- see
``scripts/measure_paired_power_grid.py``'s own docstring for why a flat
generator would make paired and two-sample statistically indistinguishable BY
CONSTRUCTION and prove nothing about why paired mode exists):

- two-sample: ``measure_regression_alpha_grid.py``'s ``_generate_pair`` (flat,
  i.i.d. per-invocation costs, Phase 2's original fixture shape) -- reused by
  IMPORT, not reimplemented, per this work item's own "reuse the exact
  generator/methodology from Phase 5's S4 script" instruction.
- paired: ``measure_regression_power.py``'s ``generate_case_correlated_pair``
  (per-eval-case cost levels + additive per-case-uniform regression) -- the
  SAME generator Phase 3 B4/Phase 4 R2/Phase 7 U1 already validated for paired
  mode, reused by import per this work item's own "reuse that script's
  approach, don't reinvent" instruction.

**Methodology notes (same conventions as every prior power/FPR grid in this
codebase -- Phase 3 B4, Phase 5 S4, Phase 7 U1 -- so this grid is directly
comparable to all of them, not measuring something subtly different):**

1. ``min_n`` forced to 2 for every cell (real gate default: 30) -- isolates
   the underlying bootstrap test's raw statistical behavior at each
   (confidence, n) from the separate ``min_n=30``/``_paired_mode_viable``
   practical refusal gate, which is orthogonal to what this grid measures.
2. ``min_effect_usd``/``min_effect_pct`` forced to 0.0 for every cell --
   isolates STATISTICAL significance (does the bootstrap CI exclude zero?)
   from the PRACTICAL-significance floor. A real ``check`` run using the real
   default floors has a detection/false-positive rate AT MOST as high as what
   is reported here.
3. ``n_boot`` reduced from the real default (10,000) to 1,000 for this survey
   only, VALIDATED first against the real default at the two most sensitive
   cells in the grid (tightest confidence x smallest n, both modes) -- see
   ``validate_n_boot`` and the printed validation output in ``main()``. A
   single production ``check`` run still uses the real ``n_boot=10,000``
   default.
4. **Trial-sharing across confidence, per mode** (same deliberate, stated
   design as ``measure_regression_alpha_grid.py``): for a fixed
   (n, effect_pct, trial_index), the SAME underlying (baseline, current)
   sample pair is reused across all 3 confidence levels, and the bootstrap
   resample RNG seed is the trial index itself at every confidence level too
   -- since ``bootstrap_diff_of_means``/``bootstrap_mean_of_paired_deltas``
   only use ``confidence`` to select which percentile of the (seed-determined)
   resampled distribution to return, calling them 3x with the same
   ``(baseline, current, seed)`` and different ``confidence`` reproduces the
   IDENTICAL underlying resampled distribution each time -- a matched
   comparison across the 3 confidence columns at a fixed cell, not 3
   independently-noisy measurements. This does not change what value any
   individual (confidence, n, effect) cell estimates; each cell still gets
   >=2,000 genuine, independent trials.

**Confidence interval on the detection rate itself**: a Wilson score interval
(``wilson_score_interval``), NOT a naive normal-approximation interval
(``phat +/- z*sqrt(phat*(1-phat)/n)``) -- the naive interval is well known to
break down (can extend below 0 or above 1, has poor actual coverage) exactly
in the regime several of these cells sit in: FPR cells with ``phat`` near 0.02
at n_trials=2,000 have few "successes," and near-100% power cells at n=25 true
effect have ``phat`` near 1.0. Wilson's interval is a standard, textbook fix
for a binomial proportion CI in this near-0/near-1 regime (Wilson 1927; see
e.g. Brown, Cai & DasGupta 2001 for the modern recommendation over the naive
Wald interval). Reported at the conventional 95% level (``z=1.96``) for the
CI ON THE DETECTION-RATE ESTIMATE -- a SEPARATE quantity from the swept
``confidence`` parameter (0.95/0.98/0.99), which is the regression TEST's own
significance level, not the level of the interval reported around its
measured detection rate. Both meanings of "confidence" appear in this script;
they are never the same number by coincidence and are labeled distinctly in
all printed/written output to avoid conflating them.

Run: ``uv run python scripts/measure_regression_confidence_grid.py``
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from measure_regression_alpha_grid import _generate_pair as generate_two_sample_pair  # noqa: E402
from measure_regression_power import generate_case_correlated_pair  # noqa: E402

from adk_tracegauge._regression import (  # noqa: E402
    bootstrap_diff_of_means,
    bootstrap_mean_of_paired_deltas,
)

CONFIDENCE_GRID = [0.95, 0.98, 0.99]
N_GRID = [30, 50]
EFFECT_PCT_GRID = [0.0, 10.0, 25.0]
N_TRIALS = 2_000
"""U2's own explicit minimum ('>=2000 trials each'). 3x2x3 = 18 cells/mode x
2 modes x 2,000 trials = 72,000 total simulated `check`-equivalent bootstrap
evaluations minimum -- matches U2's own '>=36,000 simulated calls minimum'
requirement PER 2.1/2.2 (18 cells x 2,000 trials each), x2 for both modes."""
N_BOOT = 1_000
"""See module docstring note 3 -- validated against n_boot=10,000 below
before being trusted for this grid."""
POWER_MIN_N = 2  # disables the real min_n=30 / _paired_mode_viable refusal gate -- see note 1
WILSON_Z = 1.959963984540054
"""Exact standard-normal quantile for a 95% two-sided interval
(``_inverse_normal_cdf(0.975)`` in ``_regression.py`` would give the same
value; hardcoded here to keep this script import-independent of that
internal helper). See module docstring: this is the CI level on the
detection-rate ESTIMATE, unrelated to the swept ``confidence`` grid values."""

SEED_BASE_TWO_SAMPLE = 1_000_000
"""Distinct from every other seed base already in use in this codebase
(Phase 2 FPR: 90_000; Phase 3 B4 two-sample power grid: 700_000; Phase 3 B4
paired comparison: 800_000; Phase 5 S4 alpha grid: 600_000; Phase 7 U1 paired
grid: 900_000) -- so no two measurements anywhere in this codebase ever share
an RNG stream."""
SEED_BASE_PAIRED = 1_100_000
"""Distinct from SEED_BASE_TWO_SAMPLE and every base listed above."""


def wilson_score_interval(successes: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion
    ``successes / n``, at the two-sided level implied by ``z`` (default
    z=1.959963984540054 -> 95%). See module docstring for why this method
    (not the naive normal approximation) is used.

    Returns ``(lower, upper)``, both clamped to ``[0.0, 1.0]``. ``n=0`` is
    treated as maximally uninformative: returns ``(0.0, 1.0)`` rather than
    dividing by zero.
    """
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) / n) + (z2 / (4.0 * n * n)))
    lower = (center - margin) / denom
    upper = (center + margin) / denom
    return (max(0.0, lower), min(1.0, upper))


CellKey = tuple[float, int, float]  # (confidence, n, effect_pct)
CellResult = tuple[int, int]  # (detections, n_trials)


def compute_two_sample_confidence_grid(
    confidence_grid: list[float] = CONFIDENCE_GRID,
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
) -> dict[CellKey, CellResult]:
    """Returns {(confidence, n, effect_pct): (detections, n_trials)} for
    TWO-SAMPLE mode (``bootstrap_diff_of_means``), using the flat generator
    (Phase 5 S4's own). Fully deterministic given the same inputs -- every
    trial's data-generation seed is derived only from (n, effect_pct,
    trial_index), never from confidence (see module docstring's
    trial-sharing note) or wall-clock.
    """
    detections: dict[CellKey, int] = {
        (c, n, e): 0 for c in confidence_grid for n in n_grid for e in effect_pct_grid
    }
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_TWO_SAMPLE + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_two_sample_pair(gen, n, effect_pct)
                for confidence in confidence_grid:
                    ci_lower, _ci_upper = bootstrap_diff_of_means(
                        baseline, current, confidence=confidence, n_boot=n_boot, seed=trial
                    )
                    if ci_lower > 0.0:  # statistically significant, one-sided; floors disabled
                        detections[(confidence, n, effect_pct)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def compute_paired_confidence_grid(
    confidence_grid: list[float] = CONFIDENCE_GRID,
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
) -> dict[CellKey, CellResult]:
    """Returns {(confidence, n, effect_pct): (detections, n_trials)} for
    PAIRED mode (``bootstrap_mean_of_paired_deltas``), using the
    case-correlated generator (Phase 3 B4 / Phase 7 U1's own). Same
    determinism contract as ``compute_two_sample_confidence_grid``.
    """
    detections: dict[CellKey, int] = {
        (c, n, e): 0 for c in confidence_grid for n in n_grid for e in effect_pct_grid
    }
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_PAIRED + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_case_correlated_pair(gen, n, effect_pct)
                deltas = [c - b for b, c in zip(baseline, current, strict=True)]
                for confidence in confidence_grid:
                    ci_lower, _ci_upper = bootstrap_mean_of_paired_deltas(
                        deltas, confidence=confidence, n_boot=n_boot, seed=trial
                    )
                    if ci_lower > 0.0:
                        detections[(confidence, n, effect_pct)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def validate_n_boot_two_sample(
    confidence: float, n: int, effect_pct: float, n_trials: int = 150
) -> tuple[int, int]:
    """Empirical validation of N_BOOT=1,000 against the real default
    n_boot=10,000, on IDENTICAL underlying data, for two-sample mode. Same
    discipline as every prior grid (Phase 3 B4, Phase 5 S4, Phase 7 U1)
    before trusting a reduced n_boot. Returns (agreements, n_trials)."""
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE_TWO_SAMPLE + hash((n, effect_pct, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_two_sample_pair(gen, n, effect_pct)
        lo_fast, _ = bootstrap_diff_of_means(
            baseline, current, confidence=confidence, n_boot=1_000, seed=trial
        )
        lo_full, _ = bootstrap_diff_of_means(
            baseline, current, confidence=confidence, n_boot=10_000, seed=trial
        )
        if (lo_fast > 0.0) == (lo_full > 0.0):
            agreements += 1
    return agreements, n_trials


def validate_n_boot_paired(
    confidence: float, n: int, effect_pct: float, n_trials: int = 150
) -> tuple[int, int]:
    """Paired-mode analogue of ``validate_n_boot_two_sample``."""
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE_PAIRED + hash((n, effect_pct, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_case_correlated_pair(gen, n, effect_pct)
        deltas = [c - b for b, c in zip(baseline, current, strict=True)]
        lo_fast, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=confidence, n_boot=1_000, seed=trial
        )
        lo_full, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=confidence, n_boot=10_000, seed=trial
        )
        if (lo_fast > 0.0) == (lo_full > 0.0):
            agreements += 1
    return agreements, n_trials


def _print_grid(
    grid: dict[CellKey, CellResult],
    label: str,
    confidence_grid: list[float],
    n_grid: list[int],
    effect_pct_grid: list[float],
) -> None:
    print(f"\n=== {label} ===")
    for confidence in confidence_grid:
        print(f"\n-- confidence={confidence} --")
        header = "n\\effect%".ljust(10) + "".join(f"{e:>22.0f}%" for e in effect_pct_grid)
        print(header)
        for n in n_grid:
            cells = []
            for e in effect_pct_grid:
                detections, n_trials = grid[(confidence, n, e)]
                phat = detections / n_trials
                lo, hi = wilson_score_interval(detections, n_trials)
                cells.append(f"{phat:>7.4f} [{lo:.4f},{hi:.4f}]")
            row = str(n).ljust(10) + "".join(f"{c:>22}" for c in cells)
            print(row)


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000, identical data) ===")
    print("two-sample (tightest confidence x smallest n, and the min_n cell):")
    for confidence, n, effect_pct in [(0.99, 30, 10.0), (0.95, 50, 10.0)]:
        agreements, n_trials = validate_n_boot_two_sample(confidence, n, effect_pct)
        print(
            f"  confidence={confidence} n={n} effect={effect_pct}%: {agreements}/{n_trials} = "
            f"{agreements / n_trials * 100:.1f}% verdict agreement"
        )
    print("paired (tightest confidence x smallest n, and the min_n cell):")
    for confidence, n, effect_pct in [(0.99, 30, 10.0), (0.95, 50, 10.0)]:
        agreements, n_trials = validate_n_boot_paired(confidence, n, effect_pct)
        print(
            f"  confidence={confidence} n={n} effect={effect_pct}%: {agreements}/{n_trials} = "
            f"{agreements / n_trials * 100:.1f}% verdict agreement"
        )

    total_cells = len(CONFIDENCE_GRID) * len(N_GRID) * len(EFFECT_PCT_GRID)
    total_calls = total_cells * N_TRIALS * 2  # x2 for both modes
    print(
        f"\nComputing confidence x n x effect grid for BOTH modes: {total_cells} cells/mode, "
        f"{N_TRIALS} trials/cell, n_boot={N_BOOT} ({total_calls} total simulated bootstrap "
        "evaluations)..."
    )
    t0 = time.time()
    two_sample_grid = compute_two_sample_confidence_grid()
    two_sample_elapsed = time.time() - t0
    print(f"two-sample grid done in {two_sample_elapsed:.1f}s")

    t1 = time.time()
    paired_grid = compute_paired_confidence_grid()
    paired_elapsed = time.time() - t1
    print(f"paired grid done in {paired_elapsed:.1f}s")

    elapsed = time.time() - t0
    _print_grid(
        two_sample_grid,
        "TWO-SAMPLE (detection rate [Wilson 95% CI])",
        CONFIDENCE_GRID,
        N_GRID,
        EFFECT_PCT_GRID,
    )
    _print_grid(
        paired_grid,
        "PAIRED (detection rate [Wilson 95% CI])",
        CONFIDENCE_GRID,
        N_GRID,
        EFFECT_PCT_GRID,
    )
    print(
        f"\nWall-clock: {elapsed:.1f}s total (two-sample {two_sample_elapsed:.1f}s + "
        f"paired {paired_elapsed:.1f}s)"
    )

    out_path = Path(__file__).resolve().parent.parent / "reports" / "confidence_grid_u2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "two_sample": {
            f"{c}|{n}|{e}": {
                "detections": det,
                "n_trials": nt,
                "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (c, n, e), (det, nt) in two_sample_grid.items()
        },
        "paired": {
            f"{c}|{n}|{e}": {
                "detections": det,
                "n_trials": nt,
                "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (c, n, e), (det, nt) in paired_grid.items()
        },
        "wall_clock_seconds": elapsed,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nWrote raw grid to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
