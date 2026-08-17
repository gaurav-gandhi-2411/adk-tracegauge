"""scripts/measure_power_by_cv_grid.py — AD1.3: replace the single published
power figure (99.22%/100% at n=30/50, confidence=0.98, 10% effect) with a
table across the user's own per-invocation cost coefficient of variation
(CV) -- since AC1 (docs/audit/AC1_SKEW_SENSITIVITY.md) found power is
highly sensitive to CV and adopting any one CV (0.15 from the original
generator, or 0.6 from AC1's skew probe) is itself an unmeasured
assumption, not a fix. AD1.1's framing: "CV=0.15 and CV=0.6 are both
assumptions. Replacing one with the other is not a fix." This script does
not pick a number -- it sweeps CV and reports power as a function of it,
so a reader can find the column matching their own data (which `check`
itself already reports per-run -- see AD1.2 / this module's own
``minimum_detectable_effect_usd``).

**Scope** (AD1.3's own spec): CV in {0.1, 0.2, 0.4, 0.6, 1.0} x n in
{30, 50, 100} x mode in {two-sample, paired}, confidence=0.98
(``DEFAULT_CONFIDENCE``, unchanged shipped value -- AD1.6), true effect
fixed at 10% (the exact effect size the original single published power
figure was measured at -- this table replaces that number, not a
different one), >=2,000 trials/cell, Wilson 95% CI.

**Shape**: Gaussian (symmetric) at every CV, deliberately -- AC1 already
established (Gaussian-CV-matched control vs lognormal-skewed, same CV)
that power is a function of CV (variance magnitude), not distribution
shape; see AC1_SKEW_SENSITIVITY.md's "Harness self-audit" section. Reusing
the simpler symmetric generator here avoids re-litigating a question this
codebase already answered, and keeps this table's only manipulated
variable CV itself.

**Generators**: proportional-SD variants of this codebase's own two
existing generators (``measure_regression_alpha_grid.py::_generate_pair``
for two-sample, ``measure_regression_power.py::generate_case_correlated_pair``
for paired) -- same case-level heterogeneity model and same additive
effect-injection mechanics, with SD expressed as ``cv * mean`` instead of
a fixed constant, so CV is swept as a single explicit parameter rather
than a family of unrelated generators.

Run: ``uv run python scripts/measure_power_by_cv_grid.py``
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

from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402
from measure_regression_power import (  # noqa: E402
    CASE_CORRELATED_BASE_MEAN,
    CASE_CORRELATED_LEVEL_HIGH,
    CASE_CORRELATED_LEVEL_LOW,
)

from adk_tracegauge._regression import (  # noqa: E402
    bootstrap_diff_of_means,
    bootstrap_mean_of_paired_deltas,
)

TWO_SAMPLE_BASE_MEAN = 0.010
"""Same BASE_MEAN as measure_regression_alpha_grid.py -- unchanged so the
mean cost level this table sweeps CV around matches the existing grids."""

CV_GRID = [0.1, 0.2, 0.4, 0.6, 1.0]
N_GRID = [30, 50, 100]
CONFIDENCE = 0.98
"""DEFAULT_CONFIDENCE -- unchanged shipped value, per AD1.6."""
EFFECT_PCT = 10.0
"""The exact effect size the original single published power figure
("power to detect a true 10% cost regression") was measured at -- this
table replaces that number's assumption, not its effect size."""
N_TRIALS = 2_000
"""AD1.3's own explicit floor."""
N_BOOT = 1_000
"""Reduced-n_boot survey convention -- validated below against 10,000."""

SEED_BASE_TWO_SAMPLE_CV = 1_400_000
"""Distinct from every seed base already in use in this codebase."""
SEED_BASE_PAIRED_CV = 1_500_000
"""Distinct from every seed base already in use in this codebase."""


def generate_two_sample_pair_cv(
    rng: random.Random, n: int, effect_pct: float, cv: float
) -> tuple[list[float], list[float]]:
    """Proportional-SD variant of ``measure_regression_alpha_grid.py::
    _generate_pair`` -- same flat i.i.d. shape, SD expressed as
    ``cv * mean`` instead of a fixed constant, so CV is the single swept
    parameter."""
    effect = effect_pct / 100.0
    base_sd = cv * TWO_SAMPLE_BASE_MEAN
    baseline = [max(0.0001, rng.gauss(TWO_SAMPLE_BASE_MEAN, base_sd)) for _ in range(n)]
    current = [
        max(0.0001, rng.gauss(TWO_SAMPLE_BASE_MEAN * (1 + effect), base_sd * (1 + effect)))
        for _ in range(n)
    ]
    return baseline, current


def generate_paired_pair_cv(
    rng: random.Random, n: int, effect_pct: float, cv: float
) -> tuple[list[float], list[float]]:
    """Proportional-SD variant of ``measure_regression_power.py::
    generate_case_correlated_pair`` -- same case-level heterogeneity model
    and additive effect injection, within-case SD expressed as
    ``cv * case_level`` instead of a fixed constant."""
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    baseline = [max(0.0001, rng.gauss(d, cv * d)) for d in case_levels]
    current = [max(0.0001, rng.gauss(d + effect_usd, cv * (d + effect_usd))) for d in case_levels]
    return baseline, current


CellKey = tuple[float, int]  # (cv, n)
CellResult = tuple[int, int]  # (detections, n_trials)


def compute_two_sample_cv_grid(
    cv_grid: list[float] = CV_GRID,
    n_grid: list[int] = N_GRID,
    effect_pct: float = EFFECT_PCT,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
    confidence: float = CONFIDENCE,
) -> dict[CellKey, CellResult]:
    detections: dict[CellKey, int] = {(cv, n): 0 for cv in cv_grid for n in n_grid}
    for cv in cv_grid:
        for n in n_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_TWO_SAMPLE_CV + hash((cv, n, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_two_sample_pair_cv(gen, n, effect_pct, cv)
                ci_lower, _ = bootstrap_diff_of_means(
                    baseline, current, confidence=confidence, n_boot=n_boot, seed=trial
                )
                if ci_lower > 0.0:
                    detections[(cv, n)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def compute_paired_cv_grid(
    cv_grid: list[float] = CV_GRID,
    n_grid: list[int] = N_GRID,
    effect_pct: float = EFFECT_PCT,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
    confidence: float = CONFIDENCE,
) -> dict[CellKey, CellResult]:
    detections: dict[CellKey, int] = {(cv, n): 0 for cv in cv_grid for n in n_grid}
    for cv in cv_grid:
        for n in n_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_PAIRED_CV + hash((cv, n, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_paired_pair_cv(gen, n, effect_pct, cv)
                deltas = [c - b for b, c in zip(baseline, current, strict=True)]
                ci_lower, _ = bootstrap_mean_of_paired_deltas(
                    deltas, confidence=confidence, n_boot=n_boot, seed=trial
                )
                if ci_lower > 0.0:
                    detections[(cv, n)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def validate_n_boot_two_sample(
    cv: float, n: int, effect_pct: float = EFFECT_PCT, n_trials: int = 150,
    confidence: float = CONFIDENCE,
) -> tuple[int, int]:
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE_TWO_SAMPLE_CV + hash((cv, n, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_two_sample_pair_cv(gen, n, effect_pct, cv)
        lo_fast, _ = bootstrap_diff_of_means(baseline, current, confidence=confidence, n_boot=1_000, seed=trial)
        lo_full, _ = bootstrap_diff_of_means(baseline, current, confidence=confidence, n_boot=10_000, seed=trial)
        if (lo_fast > 0.0) == (lo_full > 0.0):
            agreements += 1
    return agreements, n_trials


def validate_n_boot_paired(
    cv: float, n: int, effect_pct: float = EFFECT_PCT, n_trials: int = 150,
    confidence: float = CONFIDENCE,
) -> tuple[int, int]:
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE_PAIRED_CV + hash((cv, n, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_paired_pair_cv(gen, n, effect_pct, cv)
        deltas = [c - b for b, c in zip(baseline, current, strict=True)]
        lo_fast, _ = bootstrap_mean_of_paired_deltas(deltas, confidence=confidence, n_boot=1_000, seed=trial)
        lo_full, _ = bootstrap_mean_of_paired_deltas(deltas, confidence=confidence, n_boot=10_000, seed=trial)
        if (lo_fast > 0.0) == (lo_full > 0.0):
            agreements += 1
    return agreements, n_trials


def _print_grid(grid: dict[CellKey, CellResult], label: str) -> None:
    print(f"\n=== {label} (power to detect true {EFFECT_PCT:.0f}% regression, confidence={CONFIDENCE}) ===")
    header = "CV\\n".ljust(8) + "".join(f"{n:>24}" for n in N_GRID)
    print(header)
    for cv in CV_GRID:
        cells = []
        for n in N_GRID:
            detections, n_trials = grid[(cv, n)]
            phat = detections / n_trials
            lo, hi = wilson_score_interval(detections, n_trials)
            cells.append(f"{phat:>7.4f} [{lo:.4f},{hi:.4f}]")
        row = f"{cv:<8}" + "".join(f"{c:>24}" for c in cells)
        print(row)


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000) ===")
    for cv, n in [(0.1, 30), (1.0, 100)]:
        a2, nt2 = validate_n_boot_two_sample(cv, n)
        ap, ntp = validate_n_boot_paired(cv, n)
        print(f"  two-sample cv={cv} n={n}: {a2}/{nt2} = {a2/nt2*100:.1f}% agreement")
        print(f"  paired     cv={cv} n={n}: {ap}/{ntp} = {ap/ntp*100:.1f}% agreement")

    print(
        f"\nComputing CV x n power grid: {len(CV_GRID)}x{len(N_GRID)} cells/mode, "
        f"{N_TRIALS} trials/cell, n_boot={N_BOOT}, effect={EFFECT_PCT}%, confidence={CONFIDENCE}..."
    )
    t0 = time.time()
    two_sample_grid = compute_two_sample_cv_grid()
    two_sample_elapsed = time.time() - t0
    print(f"two-sample grid done in {two_sample_elapsed:.1f}s")

    t1 = time.time()
    paired_grid = compute_paired_cv_grid()
    paired_elapsed = time.time() - t1
    print(f"paired grid done in {paired_elapsed:.1f}s")

    _print_grid(two_sample_grid, "TWO-SAMPLE power [Wilson 95% CI]")
    _print_grid(paired_grid, "PAIRED power [Wilson 95% CI]")

    out_path = Path(__file__).resolve().parent.parent / "reports" / "power_by_cv_grid.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "confidence": CONFIDENCE,
        "effect_pct": EFFECT_PCT,
        "n_boot": N_BOOT,
        "n_trials": N_TRIALS,
        "cv_grid": CV_GRID,
        "n_grid": N_GRID,
        "two_sample": {
            f"{cv}|{n}": {
                "detections": det, "n_trials": nt, "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (cv, n), (det, nt) in two_sample_grid.items()
        },
        "paired": {
            f"{cv}|{n}": {
                "detections": det, "n_trials": nt, "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (cv, n), (det, nt) in paired_grid.items()
        },
        "wall_clock_seconds": two_sample_elapsed + paired_elapsed,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nWrote raw grid to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
