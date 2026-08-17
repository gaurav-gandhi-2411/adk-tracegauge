"""scripts/measure_regression_skew_sensitivity.py — AC1, adk-tracegauge:
does tracegauge's AB1-corrected mechanism (percentile/BCa bootstrap
undercoverage on RIGHT-SKEWED distributions at small n -- see
token-efficiency-scorer's docs/audit/EDIT_RATIO_BOOTSTRAP_COVERAGE.md, AB1
correction section) also affect this package's own published FPR/power
figures?

**Why this script exists**: every FPR/power grid this package has ever
published (Phase 2 through the FPR-anomaly audit, ``reports/confidence_grid_
u2.json``, this module's own README table) was generated from Gaussian
per-invocation cost draws -- see ``measure_regression_alpha_grid.py``'s
``_generate_pair`` (``rng.gauss``, CV = BASE_SD/BASE_MEAN = 15%) and
``measure_regression_power.py``'s ``generate_case_correlated_pair``
(``rng.gauss`` within-case, CV = WITHIN_CASE_SD/case_level ~= 3-20% across
the case-level range). Both have negligible floor-clamping probability
(nearest case level to the 0.0001 floor is >=4.8 SD away) and are
essentially symmetric by construction. ``_regression.py``'s own module
docstring (line ~72) already states real per-invocation USD cost is
"a right-skewed, non-negative distribution" -- the published grid never
actually tested that regime; it tested a distribution shape acknowledged
elsewhere in this same codebase as unrepresentative.

**No real ADK per-invocation telemetry is bundled in this repo** to fit a
skew parameter to. Absent that, this script uses a lognormal per-invocation
noise model with CV=0.6 (skewness ~= 3*CV + CV**3 ~= 2.02) around each case
level, in place of ``generate_case_correlated_pair``'s Gaussian noise --
chosen as a plausible, clearly-labeled-as-ASSUMED magnitude representative
of documented LLM per-invocation cost variability (variable completion
length, occasional retries/extra tool calls), NOT a value fit to measured
ADK data. This is an UNVERIFIED-against-real-data assumption; the
conclusion below is conditioned on it, and stated as such.

Everything else (case-level heterogeneity model, additive effect injection,
the REAL production ``bootstrap_mean_of_paired_deltas``, Wilson CIs, seed
discipline) is reused unchanged from ``measure_regression_power.py`` /
``measure_regression_confidence_grid.py`` so this is a shape-only
manipulation, isolating skew as the one changed variable per rule 84
(one variable at a time) -- same discipline as this codebase's own H1
discriminant script (matched variance budget across a structural change).

**Scope** (AC1.3): PAIRED mode only (the shipped ``--mode auto`` default),
confidence=0.98 (``DEFAULT_CONFIDENCE``, the shipped value) x n in {30, 50}
(``MIN_N_DEFAULT`` and the next grid point) x effect_pct in {0.0, 10.0,
25.0} (FPR and both power cells actually published in the README table) =
6 cells, N_TRIALS=5,000/cell (>= the required 2,000 floor, matches this
package's own current grid trial count) — >=30,000 simulated bootstrap
evaluations. Wilson score interval on every reported rate.

Run: ``uv run python scripts/measure_regression_skew_sensitivity.py``
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

from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402
from measure_regression_power import (  # noqa: E402
    CASE_CORRELATED_BASE_MEAN,
    CASE_CORRELATED_LEVEL_HIGH,
    CASE_CORRELATED_LEVEL_LOW,
)

from adk_tracegauge._regression import bootstrap_mean_of_paired_deltas  # noqa: E402

CONFIDENCE = 0.98
"""DEFAULT_CONFIDENCE -- the shipped value; AC1.3 scopes this measurement
to the shipped configuration only, not a full confidence sweep."""
N_GRID = [30, 50]
EFFECT_PCT_GRID = [0.0, 10.0, 25.0]
N_TRIALS = 5_000
N_BOOT = 1_000
"""Reduced-n_boot survey convention (see measure_regression_confidence_grid.py
note 3) -- validated below against n_boot=10,000 before being trusted."""
SKEW_CV = 0.6
"""Assumed, NOT measured against real ADK data (none bundled in this repo)
-- see module docstring. Lognormal skewness at this CV ~= 2.02."""

SEED_BASE_SKEWED = 1_200_000
"""Distinct from every seed base already in use in this codebase (see
measure_regression_confidence_grid.py's own docstring for the registry)."""


def generate_case_correlated_pair_skewed(
    rng: random.Random, n: int, effect_pct: float, cv: float = SKEW_CV
) -> tuple[list[float], list[float]]:
    """Same case-level heterogeneity model as
    ``measure_regression_power.generate_case_correlated_pair`` (per-case
    cost level ``d_i ~ Uniform(CASE_CORRELATED_LEVEL_LOW,
    CASE_CORRELATED_LEVEL_HIGH)``, additive per-case-uniform effect), but
    the per-invocation draw around each case level is LOGNORMAL (right-
    skewed, CV=``cv``) instead of Gaussian (symmetric). See module
    docstring for why and the CV's provenance (assumed, not measured).
    """
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    sigma2 = math.log(1.0 + cv * cv)
    sigma = math.sqrt(sigma2)

    def _draw(mean_target: float) -> float:
        mu = math.log(mean_target) - sigma2 / 2.0
        return rng.lognormvariate(mu, sigma)

    baseline = [_draw(d) for d in case_levels]
    current = [_draw(d + effect_usd) for d in case_levels]
    return baseline, current


def generate_case_correlated_pair_gaussian_cv_matched(
    rng: random.Random, n: int, effect_pct: float, cv: float = SKEW_CV
) -> tuple[list[float], list[float]]:
    """CONTROL for ``generate_case_correlated_pair_skewed``: identical
    case-level heterogeneity model and identical per-case CV (=``cv``,
    matching the lognormal generator's variance exactly), but the
    per-invocation noise stays GAUSSIAN (symmetric) instead of lognormal.

    Isolates shape from magnitude: this generator changes CV the same
    10x-realistic amount the skewed generator does, but keeps the
    distribution symmetric. If FPR/power collapse similarly here, the
    published-figures gap is a MAGNITUDE (unrealistically low assumed
    variance) problem, not a skew-specific one. If this control's numbers
    stay close to the published Gaussian figures while the skewed
    generator's collapse, the gap is skew-specific.
    """
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    baseline = [max(0.0001, rng.gauss(d, cv * d)) for d in case_levels]
    current = [max(0.0001, rng.gauss(d + effect_usd, cv * (d + effect_usd))) for d in case_levels]
    return baseline, current


CellKey = tuple[int, float]  # (n, effect_pct)
CellResult = tuple[int, int]  # (detections, n_trials)

SEED_BASE_GAUSSIAN_CONTROL = 1_300_000
"""Distinct from SEED_BASE_SKEWED and every other seed base in this codebase."""


def compute_gaussian_control_grid(
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
    confidence: float = CONFIDENCE,
) -> dict[CellKey, CellResult]:
    detections: dict[CellKey, int] = {(n, e): 0 for n in n_grid for e in effect_pct_grid}
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_GAUSSIAN_CONTROL + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_case_correlated_pair_gaussian_cv_matched(
                    gen, n, effect_pct
                )
                deltas = [c - b for b, c in zip(baseline, current, strict=True)]
                ci_lower, _ci_upper = bootstrap_mean_of_paired_deltas(
                    deltas, confidence=confidence, n_boot=n_boot, seed=trial
                )
                if ci_lower > 0.0:
                    detections[(n, effect_pct)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def compute_skewed_paired_grid(
    n_grid: list[int] = N_GRID,
    effect_pct_grid: list[float] = EFFECT_PCT_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT,
    confidence: float = CONFIDENCE,
) -> dict[CellKey, CellResult]:
    detections: dict[CellKey, int] = {(n, e): 0 for n in n_grid for e in effect_pct_grid}
    for n in n_grid:
        for effect_pct in effect_pct_grid:
            for trial in range(n_trials):
                seed = SEED_BASE_SKEWED + hash((n, effect_pct, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_case_correlated_pair_skewed(gen, n, effect_pct)
                deltas = [c - b for b, c in zip(baseline, current, strict=True)]
                ci_lower, _ci_upper = bootstrap_mean_of_paired_deltas(
                    deltas, confidence=confidence, n_boot=n_boot, seed=trial
                )
                if ci_lower > 0.0:
                    detections[(n, effect_pct)] += 1
    return {key: (count, n_trials) for key, count in detections.items()}


def validate_n_boot(
    n: int, effect_pct: float, n_trials: int = 150, confidence: float = CONFIDENCE
) -> tuple[int, int]:
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE_SKEWED + hash((n, effect_pct, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_case_correlated_pair_skewed(gen, n, effect_pct)
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


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000, identical skewed data) ===")
    for n, effect_pct in [(30, 10.0), (50, 10.0)]:
        agreements, n_trials = validate_n_boot(n, effect_pct)
        print(
            f"  n={n} effect={effect_pct}%: {agreements}/{n_trials} = "
            f"{agreements / n_trials * 100:.1f}% verdict agreement"
        )

    print(
        f"\nComputing PAIRED grid at confidence={CONFIDENCE}, skew_cv={SKEW_CV} "
        f"(lognormal, skewness~=2.02): n x effect = {len(N_GRID)}x{len(EFFECT_PCT_GRID)} "
        f"cells, {N_TRIALS} trials/cell, n_boot={N_BOOT}..."
    )
    t0 = time.time()
    grid = compute_skewed_paired_grid()
    elapsed = time.time() - t0
    print(f"done in {elapsed:.1f}s")

    print(f"\n=== PAIRED, confidence={CONFIDENCE}, SKEWED (lognormal CV={SKEW_CV}) ===")
    print("n\\effect%".ljust(10) + "".join(f"{e:>24.0f}%" for e in EFFECT_PCT_GRID))
    for n in N_GRID:
        cells = []
        for e in EFFECT_PCT_GRID:
            detections, n_trials = grid[(n, e)]
            phat = detections / n_trials
            lo, hi = wilson_score_interval(detections, n_trials)
            cells.append(f"{phat:>8.4f} [{lo:.4f},{hi:.4f}] ({detections}/{n_trials})")
        row = str(n).ljust(10) + "".join(f"{c:>24}" for c in cells)
        print(row)

    print(
        f"\nComputing GAUSSIAN CONTROL grid (same CV={SKEW_CV}, symmetric not skewed) -- "
        "isolates magnitude from shape..."
    )
    t2 = time.time()
    control_grid = compute_gaussian_control_grid()
    control_elapsed = time.time() - t2
    print(f"done in {control_elapsed:.1f}s")

    print(f"\n=== PAIRED, confidence={CONFIDENCE}, GAUSSIAN CONTROL (CV={SKEW_CV}, symmetric) ===")
    print("n\\effect%".ljust(10) + "".join(f"{e:>24.0f}%" for e in EFFECT_PCT_GRID))
    for n in N_GRID:
        cells = []
        for e in EFFECT_PCT_GRID:
            detections, n_trials = control_grid[(n, e)]
            phat = detections / n_trials
            lo, hi = wilson_score_interval(detections, n_trials)
            cells.append(f"{phat:>8.4f} [{lo:.4f},{hi:.4f}] ({detections}/{n_trials})")
        row = str(n).ljust(10) + "".join(f"{c:>24}" for c in cells)
        print(row)

    # Comparison against the published Gaussian-generator figures
    # (reports/confidence_grid_u2.json, confidence=0.98 rows) -- printed
    # inline so a reader doesn't have to cross-reference a second file.
    published = {
        (30, 0.0): 0.0146,
        (30, 10.0): 0.9922,
        (30, 25.0): None,  # not in the published 3-effect grid at 0.98/n=30 for FPR row context
        (50, 0.0): 0.0166,
        (50, 10.0): 1.0000,
        (50, 25.0): None,
    }
    print(
        "\n=== Comparison: skewed vs Gaussian-CV-matched-control vs published (low-CV) figures ==="
    )
    for n in N_GRID:
        for e in EFFECT_PCT_GRID:
            pub = published.get((n, e))
            det, nt = grid[(n, e)]
            phat = det / nt
            cdet, cnt = control_grid[(n, e)]
            cphat = cdet / cnt
            pub_str = f"{pub:.4f}" if pub is not None else "n/a"
            print(
                f"n={n} effect={e}%: skewed_lognormal={phat:.4f}  "
                f"gaussian_control={cphat:.4f}  published_low_cv_gaussian={pub_str}"
            )

    elapsed = elapsed + control_elapsed
    out_path = Path(__file__).resolve().parent.parent / "reports" / "skew_sensitivity_ac1.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "confidence": CONFIDENCE,
        "skew_cv": SKEW_CV,
        "gaussian_control": {
            f"{n}|{e}": {
                "detections": det,
                "n_trials": nt,
                "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (n, e), (det, nt) in control_grid.items()
        },
        "n_boot": N_BOOT,
        "n_trials": N_TRIALS,
        "generator": "lognormal per-invocation noise around case level, CV assumed not measured",
        "paired": {
            f"{n}|{e}": {
                "detections": det,
                "n_trials": nt,
                "detection_rate": det / nt,
                "wilson_95ci": list(wilson_score_interval(det, nt)),
            }
            for (n, e), (det, nt) in grid.items()
        },
        "wall_clock_seconds": elapsed,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nWrote raw grid to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
