"""scripts/measure_paired_power_grid.py — Phase 7 U1, 1.5: statistical POWER
of `adk-tracegauge check`'s PAIRED regression gate (`_regression.evaluate_regression_paired`),
laid out for direct comparison against the existing two-sample grid
(`scripts/measure_regression_power.py`'s FLAT-generator grid, Phase 3 B4;
re-shown at `DEFAULT_CONFIDENCE=0.98` in Phase 5 S4's own 90-cell grid,
`PLAN.md`'s Phase 5 S4 entry).

**Why a SEPARATE grid, not a re-run of the existing two-sample script with
`evaluate_regression_paired` substituted in:** the two-sample grid's own
FLAT generator (`_generate_pair`, i.i.d. per-invocation costs with no
between-case structure) makes paired and two-sample statistically
indistinguishable BY CONSTRUCTION -- there is no between-case variance for
pairing to cancel (Phase 3 B4 verified this directly: at n=25/10%-effect
under the flat generator, two_sample=0.665 and paired=0.675). Measuring
paired mode's power against that generator would prove nothing about why
paired mode exists. This script instead uses
`scripts/measure_regression_power.py`'s `generate_case_correlated_pair`
-- the SAME case-correlated generator Phase 3 B4 and Phase 4 R2 already
validated for paired mode (`tests/test_regression_power.py`), not a newly
invented one, per this work item's own explicit instruction.

**Methodology, matching the existing two-sample grid's own conventions
(see that script's docstring) so the two are comparable, not just
adjacent:**

1. ``min_n`` forced to 2 for every cell -- isolates the underlying paired
   bootstrap's raw statistical power from the separate `--min-n=30`
   practical refusal gate (same convention as the two-sample grid's note 1).
2. ``min_effect_usd``/``min_effect_pct`` forced to 0.0 -- isolates
   STATISTICAL significance from the practical-significance floor (same
   convention as the two-sample grid's note 2). A real `check --mode
   paired` run using the real default floors is AT MOST as good as the
   number reported here for a given cell.
3. ``confidence`` is the real shipped default (`DEFAULT_CONFIDENCE=0.98`,
   Phase 5 S4) -- NOT the historical 0.95 `tests/test_regression_power.py`
   pins its own reference measurements to. This grid characterizes the
   CURRENT shipped gate, not a historical finding.
4. ``n_boot``: validated empirically before use, same discipline as every
   prior phase's grid (Phase 3 B4, Phase 5 S4) -- see the validation run
   captured in this module's own session report / PLAN.md Phase 7 U1
   entry, not re-derived from a stale prior validation (paired mode uses a
   ONE-sample bootstrap over deltas, a different resampling procedure from
   two-sample's TWO-sample bootstrap, so its own convergence at reduced
   n_boot needed its own check, not an inherited one).

Run: ``uv run python scripts/measure_paired_power_grid.py``
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from measure_regression_power import generate_case_correlated_pair  # noqa: E402

from adk_tracegauge._regression import DEFAULT_CONFIDENCE, evaluate_regression_paired  # noqa: E402

N_GRID = [10, 25, 50, 100]
"""1.5's own required grid -- deliberately does NOT include the two-sample
grid's n=250: this project's own stated realistic ADK eval-set size ceiling
(README, PLAN.md Phase 3 B4) is in the tens-to-low-hundreds; n=100 is
already a generous upper bound for a paired (per-eval-case) comparison,
where each additional n means one more real (or at minimum, evaluated)
eval case. n=10/25/50 are shared with the two-sample grid for direct,
same-n comparison; n=100 is also shared with that grid (its full range is
{10, 25, 50, 100, 250} at DEFAULT_CONFIDENCE=0.98 via Phase 5 S4's 90-cell
grid) -- only n=250 is where the two grids' coverage diverges."""
EFFECT_PCT_GRID = [0.0, 5.0, 10.0, 25.0, 50.0]
N_TRIALS = 1_000
"""1.5's own explicit minimum (">=1000 trials/cell"), not the 200 the
two-sample power grid used -- 20 cells * 1,000 trials = 20,000 simulated
`check` calls minimum, per this work item's own required floor."""
N_BOOT = 1_000
"""See module docstring point 4 -- validated against n_boot=10,000 at
several cells before being trusted for this survey; see this script's own
`validate_n_boot` function and the session report for the real validation
numbers."""
POWER_MIN_N = 2  # disables the min_n=30 practical refusal gate -- see note 1 above
SEED_BASE = 900_000
"""Distinct from every other seed base already in use in this codebase
(Phase 2 FPR: 90_000; Phase 3 B4 two-sample power grid: 700_000; Phase 3 B4
case-correlated paired-vs-two-sample comparison: 800_000; Phase 5 S4 alpha
grid and shipped-default FPR scripts have their own bases too) -- so no two
measurements in this codebase ever share an RNG stream."""


def compute_paired_power_grid(
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[tuple[int, float], float]:
    """Returns {(n, effect_pct): detection_rate}. Fully deterministic given
    the same (n_grid, effect_pct_grid, n_trials, n_boot, confidence) --
    every trial's RNG seed is derived only from (n, effect_pct, trial_index),
    never from wall-clock or dict iteration order -- mirrors
    `measure_regression_power.compute_power_grid`'s own determinism
    contract exactly.
    """
    results: dict[tuple[int, float], float] = {}
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            detections = 0
            for trial in range(n_trials):
                seed = SEED_BASE + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_case_correlated_pair(gen, n, effect_pct)
                result = evaluate_regression_paired(
                    baseline,
                    current,
                    confidence=confidence,
                    min_n=POWER_MIN_N,
                    min_effect_usd=0.0,
                    min_effect_pct=0.0,
                    n_boot=n_boot,
                    seed=trial,
                )
                if result.status == "regression":
                    detections += 1
            results[(n, effect_pct)] = detections / n_trials
    return results


def validate_n_boot(n: int = 25, effect_pct: float = 10.0, n_trials: int = 150) -> tuple[int, int]:
    """Empirical validation of N_BOOT=1,000 against the real default
    n_boot=10,000, on IDENTICAL underlying data (same seeds), at the
    borderline-most cell in the grid (smallest reasonable n with a real
    effect) -- same discipline Phase 3 B4/Phase 5 S4 used before trusting a
    reduced n_boot for their own grids. Returns (agreements, n_trials).
    """
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE + hash((n, effect_pct, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_case_correlated_pair(gen, n, effect_pct)
        r_fast = evaluate_regression_paired(
            baseline,
            current,
            confidence=DEFAULT_CONFIDENCE,
            min_n=POWER_MIN_N,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=1_000,
            seed=trial,
        )
        r_full = evaluate_regression_paired(
            baseline,
            current,
            confidence=DEFAULT_CONFIDENCE,
            min_n=POWER_MIN_N,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=10_000,
            seed=trial,
        )
        if r_fast.status == r_full.status:
            agreements += 1
    return agreements, n_trials


def _print_grid(
    grid: dict[tuple[int, float], float], n_grid: list[int], effect_pct_grid: list[float]
) -> None:
    header = "n\\effect%".ljust(10) + "".join(f"{e:>8.0f}%" for e in effect_pct_grid)
    print(header)
    for n in n_grid:
        row = str(n).ljust(10) + "".join(f"{grid[(n, e)]:>9.3f}" for e in effect_pct_grid)
        print(row)


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000, identical data) ===")
    for n, effect_pct in [(25, 10.0), (10, 5.0), (50, 10.0)]:
        agreements, n_trials = validate_n_boot(n=n, effect_pct=effect_pct)
        print(
            f"  n={n} effect={effect_pct}%: {agreements}/{n_trials} = "
            f"{agreements / n_trials * 100:.1f}% verdict agreement"
        )

    print(
        f"\nComputing PAIRED-mode power grid: {len(N_GRID)}x{len(EFFECT_PCT_GRID)} cells, "
        f"{N_TRIALS} trials/cell, n_boot={N_BOOT}, confidence={DEFAULT_CONFIDENCE} "
        f"({len(N_GRID) * len(EFFECT_PCT_GRID) * N_TRIALS} total simulated check() calls)..."
    )
    t0 = time.time()
    grid = compute_paired_power_grid()
    elapsed = time.time() - t0
    print(f"\nDetection rate (fraction of {N_TRIALS} trials firing status='regression'):\n")
    _print_grid(grid, N_GRID, EFFECT_PCT_GRID)
    print(f"\nWall-clock: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
