"""scripts/measure_regression_alpha_grid.py — Phase 5 S4: FPR/power across
one-sided alpha in {0.025, 0.01, 0.005} AND n AND true effect, extending
Phase 3 B4 / Phase 4 R4's single-alpha (confidence=0.95) power grid
(``scripts/measure_regression_power.py``) with the alpha dimension, to
decide whether the shipped default confidence level should change.

**Why this exists**: Phase 4 R4 measured the shipped default's real
false-positive rate at n=30 (confidence=0.95, i.e. one-sided alpha=0.025)
at ~3.93% — meaningfully above the nominal 2.5% one-sided expectation. This
script asks the natural follow-up: does tightening alpha (a higher
--confidence) bring FPR down to an acceptable level, and at what power
cost, across the realistic (n, effect) space this project actually cares
about?

**alpha <-> confidence mapping** (load-bearing, verified against
``_regression.py``'s own ``_one_sided_alpha``, not guessed): this module's
bootstrap CI is a TWO-SIDED ``confidence``-level percentile interval whose
LOWER bound is then used as a ONE-SIDED test (only a cost *increase* is a
regression) — so the true one-sided alpha is ``(1 - confidence) / 2``, not
``1 - confidence``. Inverting: ``confidence = 1 - 2*alpha``.

    alpha=0.025 (95% one-sided) -> confidence=0.95  (the CURRENT shipped default)
    alpha=0.010 (98% one-sided) -> confidence=0.98
    alpha=0.005 (99% one-sided) -> confidence=0.99

**Generator: SAME shape as Phase 2/3/4's own fixtures and
``measure_regression_power.py``** — per-invocation costs drawn i.i.d. from
``max(0.0001, Gauss(mean, sd))``, mean=$0.010, sd=$0.0015, sd scaling with
the mean under a true effect (``sd * (1 + effect)``). NOT a new,
more-favorable distribution — comparability with the existing grid is the
whole point of this work item's own instruction.

**Methodology notes** (mirrors ``measure_regression_power.py``'s notes 1-2
exactly, extended with a 3rd for the n_boot validation done for this item):

1. ``min_n`` forced to 2 (disables the real min_n=30 practical refusal
   gate) for every cell, including n=10/25 — isolates the underlying
   bootstrap test's raw statistical behavior at each (alpha, n) from the
   separate min_n=30 safeguard, exactly as B4's own grid did.
2. ``min_effect_usd``/``min_effect_pct`` forced to 0.0 for every cell —
   isolates the STATISTICAL half of the gate from the PRACTICAL-
   significance floor. A real `check` run using the real default floors
   is AT MOST as good as (i.e. has a false-positive rate no higher than
   and a detection rate no higher than) the numbers reported here — see
   ``tests/test_regression.py::test_false_positive_rate_at_min_n_with_real_default_config``
   and this item's own supplementary 4.4/4.5 measurements for the
   floors-ENABLED, real-shipped-config numbers.
3. ``n_boot`` reduced from the real default (10,000) to 1,000 for this
   script only, validated first (this item, not reused from B4's
   validation, since a new dimension — tight alpha — is now in play):
   150 trials/cell at 4 cells spanning the two riskiest combinations
   (smallest n, tightest alpha) plus B4's own original borderline cell —
   n=25/10%-effect/alpha=0.025: 145/150=96.7% agreement; n=10/0%-effect/
   alpha=0.005: 150/150=100% agreement; n=50/10%-effect/alpha=0.005:
   146/150=97.3% agreement; n=30/0%-effect/alpha=0.01: 150/150=100%
   agreement (see the S4 session report for the validation script) — all
   comfortably close to or matching B4's own 97.3% bar. n_boot=1000
   trusted for this SURVEY; a single production `check` run still uses the
   real n_boot=10,000 default.

**Trial-sharing across alpha (a deliberate, stated methodology choice)**:
for a FIXED (n, effect_pct, trial_index), the SAME underlying (baseline,
current) sample pair and the SAME bootstrap RNG seed are reused across all
3 alpha levels — only the percentile extracted from the (already-computed)
sorted resampled-difference distribution differs between alpha levels
(confirmed by reading ``bootstrap_diff_of_means``: ``confidence`` only
selects which percentile of the SAME resampled-diffs array is returned as
``ci_lower``/``ci_upper`` — the resampling itself does not depend on
``confidence`` at all). This means each cell still gets >=500 GENUINE,
independent trials of real bootstrap-resampled data (satisfying this work
item's own >=500-trials/cell requirement), while additionally making the
three alpha columns at a fixed (n, effect) a MATCHED comparison (same
underlying data, same resampling draws) rather than three independently-
noisy measurements — this is a strictly stronger design for the 4.3
power-cost comparison (removes sampling noise BETWEEN alpha columns that
would otherwise obscure the true alpha-vs-power tradeoff), not a weaker
one, and does not change what value any individual (alpha, n, effect) cell
estimates.

Run: ``uv run python scripts/measure_regression_alpha_grid.py``
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._regression import bootstrap_diff_of_means  # noqa: E402

N_GRID = [10, 25, 30, 50, 100, 250]
EFFECT_PCT_GRID = [0.0, 5.0, 10.0, 25.0, 50.0]
ALPHA_GRID = [0.025, 0.01, 0.005]
"""One-sided alpha levels. Mapped to `confidence` via 1 - 2*alpha (see
module docstring) -- 0.95 / 0.98 / 0.99."""
N_TRIALS = 500
N_BOOT = 1_000  # see methodology note 3 above
POWER_MIN_N = 2  # disables the min_n=30 practical refusal gate -- see note 1
BASE_MEAN = 0.010
BASE_SD = 0.0015
SEED_BASE = 600_000
"""Distinct from Phase 3 B4's FPR seed base (90_000), B4's paired-comparison
seed base (800_000), and measure_regression_power.py's own seed base
(700_000) so no two measurements in this codebase ever share an RNG
stream."""


def _alpha_to_confidence(alpha: float) -> float:
    """See module docstring's "alpha <-> confidence mapping" note."""
    return 1.0 - 2.0 * alpha


def _generate_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    """One trial's (baseline, current) cost samples -- Phase 2/3/4's exact
    generator shape (see module docstring)."""
    effect = effect_pct / 100.0
    baseline = [max(0.0001, rng.gauss(BASE_MEAN, BASE_SD)) for _ in range(n)]
    current = [
        max(0.0001, rng.gauss(BASE_MEAN * (1 + effect), BASE_SD * (1 + effect))) for _ in range(n)
    ]
    return baseline, current


def compute_alpha_grid(
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    alpha_grid: list[float] = ALPHA_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
) -> dict[tuple[float, int, float], float]:
    """Returns {(alpha, n, effect_pct): detection_rate}. Fully deterministic
    given the same grid/n_trials/n_boot -- every trial's data-generation seed
    is derived only from (n, effect_pct, trial_index) (NOT from alpha, per
    the module docstring's trial-sharing note), and the bootstrap resample
    seed is the trial index itself, matching
    ``measure_regression_power.py``'s own convention.
    """
    results: dict[tuple[float, int, float], float] = {}
    detections: dict[tuple[float, int, float], int] = {
        (a, n, e): 0 for a in alpha_grid for n in n_grid for e in effect_pct_grid
    }
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            for trial in range(n_trials):
                seed = SEED_BASE + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = _generate_pair(gen, n, effect_pct)
                # One bootstrap resample per (n, effect, trial) -- reused
                # across all 3 alpha levels (see module docstring).
                ci_lower_at: dict[float, float] = {}
                for alpha in alpha_grid:
                    confidence = _alpha_to_confidence(alpha)
                    ci_lower, _ci_upper = bootstrap_diff_of_means(
                        baseline, current, confidence=confidence, n_boot=n_boot, seed=trial
                    )
                    ci_lower_at[alpha] = ci_lower
                for alpha in alpha_grid:
                    if ci_lower_at[alpha] > 0.0:  # statistically significant, one-sided
                        detections[(alpha, n, effect_pct)] += 1
    for key, count in detections.items():
        results[key] = count / n_trials
    return results


def _print_grid_for_alpha(
    grid: dict[tuple[float, int, float], float],
    alpha: float,
    n_grid: list[int],
    effect_pct_grid: list[float],
) -> None:
    confidence = _alpha_to_confidence(alpha)
    print(f"\n=== one-sided alpha={alpha} (confidence={confidence:.2f}) ===")
    header = "n\\effect%".ljust(10) + "".join(f"{e:>8.0f}%" for e in effect_pct_grid)
    print(header)
    for n in n_grid:
        row = str(n).ljust(10) + "".join(f"{grid[(alpha, n, e)]:>9.3f}" for e in effect_pct_grid)
        print(row)


def main() -> int:
    total_calls = len(ALPHA_GRID) * len(N_GRID) * len(EFFECT_PCT_GRID) * N_TRIALS
    print(
        f"Computing alpha x n x effect grid: {len(ALPHA_GRID)}x{len(N_GRID)}x"
        f"{len(EFFECT_PCT_GRID)} = {len(ALPHA_GRID) * len(N_GRID) * len(EFFECT_PCT_GRID)} cells, "
        f"{N_TRIALS} trials/cell, n_boot={N_BOOT} ({total_calls} total simulated check() "
        "verdicts, from 1/3 as many actual bootstrap resamples due to alpha trial-sharing)..."
    )
    t0 = time.time()
    grid = compute_alpha_grid()
    elapsed = time.time() - t0
    print(f"\nDetection rate (fraction of {N_TRIALS} trials firing statistically_significant):")
    for alpha in ALPHA_GRID:
        _print_grid_for_alpha(grid, alpha, N_GRID, EFFECT_PCT_GRID)
    print(f"\nWall-clock: {elapsed:.1f}s")

    out_path = Path(__file__).resolve().parent.parent / "reports" / "alpha_grid_s4.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {f"{a}|{n}|{e}": v for (a, n, e), v in grid.items()}
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nWrote raw grid to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
