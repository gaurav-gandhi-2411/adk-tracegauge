"""scripts/measure_regression_power.py — Phase 3 B4: statistical POWER of
`adk-tracegauge check`'s two-sample regression gate (`_regression.evaluate_regression`).

Phase 2 W4 measured the gate's false-positive rate (~2%) at a single n=40 --
it never measured POWER, the probability of actually detecting a REAL cost
regression, at realistic ADK eval-set sizes. This script does that: for
every (n, true_effect_pct) cell in a 5x5 grid, it runs >=200 independent
simulated `check` calls and reports the fraction that correctly fired
`status="regression"` -- the gate's detection rate at that cell.

**Generator: intentionally the SAME shape as Phase 2's own fixtures**
(``tests/test_regression.py``'s ``test_injected_regression_is_detected``
and ``test_false_positive_rate_under_pure_noise``) -- per-invocation costs
drawn i.i.d. from ``max(0.0001, Gauss(mean, sd))``, mean=$0.010, sd=$0.0015,
sd scaling with the mean under a true effect (``sd * (1 + effect)``,
matching Phase 2's own 20%-regression fixture exactly). This script does
NOT invent a more favorable distribution -- see the module docstring's
methodology note if this ever needs to change, and B4's session report for
why an intentionally DIFFERENT (case-correlated) generator is used
separately in ``tests/test_regression_power.py``'s paired-vs-unpaired
comparison (a deliberate, justified deviation for a different question --
see that test's own docstring).

**Methodology notes, both load-bearing for interpreting the grid below:**

1. ``min_n`` is overridden to 2 (effectively disabled) for every cell,
   INCLUDING n=10 and n=25 -- below the real default min_n=30. This is
   deliberate: the point of this script is to measure the underlying
   bootstrap test's raw statistical power at each n, independent of the
   separate min_n=30 practical refusal gate. Left at the real default, every
   n=10/n=25 cell would trivially read 0.0 for the wrong reason (refused,
   not failed to detect) and the grid would say nothing about the test's
   actual power at small n. The min_n=30 gate itself is a SEPARATE
   safeguard layered on top of this measurement, not part of it.
2. ``min_effect_usd``/``min_effect_pct`` are both set to 0.0 for every cell
   -- this isolates the STATISTICAL half of the gate (does the bootstrap CI
   exclude zero?) from the PRACTICAL-significance floor (evaluate_regression's
   real default, min_effect_pct=5.0, would additionally suppress some real,
   small detections as "not worth failing a build over" -- see
   _regression.py's module docstring, "AND between two different
   questions"). A real `check` run using the real default floors is AT MOST
   as good as the number reported here for a given cell, and can be
   meaningfully worse for effects that clear the statistical bar on a given
   trial but not the 5%/$0.0001 practical one (this matters most right at
   the 5% effect column, where roughly half of individual trials will
   measure an effect somewhat below 5% purely from sampling noise even when
   the true injected effect is exactly 5%). This grid reports the
   STATISTICAL power ceiling, not the as-configured-by-default detection
   rate -- the honest verdict in the B4 session report states both.
3. ``n_boot`` is reduced from the real default (10,000) to 1,000 for this
   script only -- see WALL-CLOCK NOTE below.

WALL-CLOCK NOTE: at n_boot=10,000, a single bootstrap_diff_of_means call at
n=250/group measured ~0.91s (this machine, this session) -- 5,000+ total
simulated `check` calls at that n_boot would take on the order of an hour,
impractical for an on-demand script. n_boot=1,000 was validated empirically
before use: 150 independent trials at n=25/10%-effect (the noisiest,
most-borderline cell in this grid -- picked deliberately, not the easiest
case) compared verdicts at n_boot=1,000 vs n_boot=10,000 on IDENTICAL
underlying data and found 146/150 = 97.3% verdict agreement -- close enough
that n_boot=1,000 is trusted for a power SURVEY (identifying where the gate
is and isn't reliable) even though a single production `check` run should
still use the real n_boot=10,000 default for the tightest possible CI.

Run: ``uv run python scripts/measure_regression_power.py``
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._regression import evaluate_regression  # noqa: E402

N_GRID = [10, 25, 50, 100, 250]
EFFECT_PCT_GRID = [0.0, 5.0, 10.0, 25.0, 50.0]
N_TRIALS = 200
N_BOOT = 1_000  # see WALL-CLOCK NOTE above
POWER_MIN_N = 2  # disables the min_n=30 practical refusal gate -- see note 1 above
BASE_MEAN = 0.010
BASE_SD = 0.0015
SEED_BASE = 700_000
"""Distinct from Phase 2's FPR seed base (90_000) and B4's paired-comparison
seed base (800_000, in test_regression_power.py) so no two measurements in
this codebase ever share an RNG stream."""


def _generate_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    """One trial's (baseline, current) cost samples -- Phase 2's exact
    generator shape (see module docstring)."""
    effect = effect_pct / 100.0
    baseline = [max(0.0001, rng.gauss(BASE_MEAN, BASE_SD)) for _ in range(n)]
    current = [
        max(0.0001, rng.gauss(BASE_MEAN * (1 + effect), BASE_SD * (1 + effect))) for _ in range(n)
    ]
    return baseline, current


# --- Case-correlated generator (Phase 3 B4, moved here Phase 7 U1) --------
#
# A DELIBERATELY DIFFERENT generator from `_generate_pair` above -- lives
# here (not duplicated a second time) so both `tests/test_regression_power.py`'s
# paired-vs-two-sample comparison AND `scripts/measure_paired_power_grid.py`
# (Phase 7 U1, 1.5's dedicated paired-mode power grid) share the exact same
# generator, per that work item's own instruction not to invent a new one.
# Originally defined only in tests/test_regression_power.py (Phase 3 B4) --
# moved to this module (this package's existing home for power-measurement
# generators) rather than left duplicated a second time once 1.5 needed it
# too; tests/test_regression_power.py now imports it from here instead of
# defining its own copy. The math itself is byte-for-byte unchanged, so
# every existing measured number (200/200, 0/200, etc.) still reproduces
# exactly under the same seeds.

CASE_CORRELATED_BASE_MEAN = 0.010
CASE_CORRELATED_WITHIN_CASE_SD = 0.0008
CASE_CORRELATED_LEVEL_LOW = 0.004
CASE_CORRELATED_LEVEL_HIGH = 0.024


def generate_case_correlated_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    """Each of ``n`` synthetic eval CASES gets its own fixed per-case cost
    level ``d_i ~ Uniform(0.004, 0.024)`` -- representing real heterogeneity
    across eval cases (different prompts/tool-call trajectories cost
    different amounts, often by several x, independent of any regression).
    A baseline run's cost for case i is ``max(0.0001, Gauss(d_i,
    within_case_sd))``; a "current" run injects an ADDITIVE, per-case-UNIFORM
    dollar bump (``effect_usd = CASE_CORRELATED_BASE_MEAN * effect_pct/100``
    -- e.g. +$0.001 at effect_pct=10, the same absolute injected effect size
    as `_generate_pair`'s own generator uses relative to BASE_MEAN, but
    applied as a flat add rather than a multiplicative scale):
    ``max(0.0001, Gauss(d_i + effect_usd, within_case_sd))``.

    This additive-per-case model is the realistic shape for exactly the
    kind of regression a pairing key is meant to catch -- e.g. a bigger
    system prompt or an added tool-schema definition costs roughly the same
    EXTRA dollars on every case, regardless of that case's own base cost --
    and it is also the shape that makes pairing's mechanism (subtracting
    away the shared d_i term) most legible. A multiplicative case-correlated
    regression would still benefit from pairing (d_i's contribution to
    variance is still substantially reduced, just not fully cancelled), but
    by a smaller margin than measured here -- flagged explicitly, not left
    implicit, so the measured numbers are not read as a universal
    multiplier.

    Reusing `_generate_pair`'s own FLAT generator (no case structure at
    all) instead would prove nothing: with no between-case variance to
    remove, pairing and two-sample are approximately equivalent BY
    CONSTRUCTION (Phase 3 B4 verified this directly: at n=25/10%-effect
    under the flat generator, two_sample=0.665 and paired=0.675 --
    statistically indistinguishable). That control measurement is what
    justifies using this different, case-structured generator instead of
    reusing the flat one uncritically -- see `tests/test_regression_power.py`
    for the reproducible version of that control.
    """
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    baseline = [max(0.0001, rng.gauss(d, CASE_CORRELATED_WITHIN_CASE_SD)) for d in case_levels]
    current = [
        max(0.0001, rng.gauss(d + effect_usd, CASE_CORRELATED_WITHIN_CASE_SD)) for d in case_levels
    ]
    return baseline, current


def compute_power_grid(
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
) -> dict[tuple[int, float], float]:
    """Returns {(n, effect_pct): detection_rate}. Fully deterministic given
    the same (n_grid, effect_pct_grid, n_trials, n_boot) -- every trial's RNG
    seed is derived only from (n, effect_pct, trial_index), never from
    wall-clock or dict iteration order.
    """
    results: dict[tuple[int, float], float] = {}
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            detections = 0
            for trial in range(n_trials):
                seed = SEED_BASE + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = _generate_pair(gen, n, effect_pct)
                result = evaluate_regression(
                    baseline,
                    current,
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


def _print_grid(
    grid: dict[tuple[int, float], float], n_grid: list[int], effect_pct_grid: list[float]
) -> None:
    header = "n\\effect%".ljust(10) + "".join(f"{e:>8.0f}%" for e in effect_pct_grid)
    print(header)
    for n in n_grid:
        row = str(n).ljust(10) + "".join(f"{grid[(n, e)]:>9.3f}" for e in effect_pct_grid)
        print(row)


def main() -> int:
    print(
        f"Computing power grid: {len(N_GRID)}x{len(EFFECT_PCT_GRID)} cells, "
        f"{N_TRIALS} trials/cell, n_boot={N_BOOT} ({len(N_GRID) * len(EFFECT_PCT_GRID) * N_TRIALS} "
        "total simulated check() calls)..."
    )
    t0 = time.time()
    grid = compute_power_grid()
    elapsed = time.time() - t0
    print(f"\nDetection rate (fraction of {N_TRIALS} trials firing status='regression'):\n")
    _print_grid(grid, N_GRID, EFFECT_PCT_GRID)
    print(f"\nWall-clock: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
