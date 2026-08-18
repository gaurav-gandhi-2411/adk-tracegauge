"""scripts/measure_absolute_sd_grid.py — AE1.3: the fixed-absolute-SD
regime's own power grid, parallel in shape to `measure_power_by_cv_grid.
py`'s proportional-CV grid, so the README can show both regimes side by
side instead of one replacing the other (see AE1.1/1.2 and
`docs/audit/Q1A_RECONCILIATION.md` for why these are two different
workload assumptions, not two measurements of the same thing).

**Regime A (this script)**: fixed absolute per-invocation dollar noise,
identical regardless of a case's own cost level — `generate_case_
correlated_pair_absolute_sd`, reused unchanged from `measure_q1a_
reconciliation.py`. Approximates an evalset of near-identical cases where
cost varies by a roughly CONSTANT DOLLAR AMOUNT invocation to invocation
(e.g. the same prompt run repeatedly, with response-length noise that
doesn't scale with how much the case costs to begin with).

**Regime B (measure_power_by_cv_grid.py, already in the README)**:
proportional CV, where noise scales WITH each case's own cost level.
Approximates an evalset of genuinely varying task complexity (a mix of
short factual questions and long-form generation), where a $0.01 case and
a $0.001 case are not expected to have the same absolute dollar noise.

**Grid**: absolute SD in {$0.0002, $0.0004, $0.0008, $0.0016, $0.0032}
(the original shipped generator's own constant, $0.0008, sits in the
middle, so this grid is a direct extension of -- not a break from -- the
historical figure) x n in {30, 50, 100} x effect=10% (matching the CV
grid's own headline effect size), confidence=0.98, >=2,000 trials/cell,
Wilson 95% CIs.

Run: ``uv run python scripts/measure_absolute_sd_grid.py``
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402
from measure_regression_power import (  # noqa: E402
    CASE_CORRELATED_BASE_MEAN,
    CASE_CORRELATED_LEVEL_HIGH,
    CASE_CORRELATED_LEVEL_LOW,
)

from adk_tracegauge._regression import bootstrap_mean_of_paired_deltas  # noqa: E402


def generate_case_correlated_pair_absolute_sd(
    rng: random.Random, n: int, effect_pct: float, absolute_sd: float
) -> tuple[list[float], list[float]]:
    """Structurally IDENTICAL to `measure_regression_power.py`'s
    `generate_case_correlated_pair` -- same case-level heterogeneity model,
    same additive effect injection, same FIXED ABSOLUTE within-case SD
    (parameterized here instead of a hardcoded module constant)."""
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    baseline = [max(0.0001, rng.gauss(d, absolute_sd)) for d in case_levels]
    current = [max(0.0001, rng.gauss(d + effect_usd, absolute_sd)) for d in case_levels]
    return baseline, current


CONFIDENCE = 0.98
N_GRID = [30, 50, 100]
SD_GRID = [0.0002, 0.0004, 0.0008, 0.0016, 0.0032]
EFFECT_PCT = 10.0
N_TRIALS = 2_000
N_BOOT = 1_000

SEED_BASE = 1_800_000
"""Distinct from every seed base already in use in this codebase."""


def validate_n_boot(sd: float, n: int, n_trials: int = 150) -> tuple[int, int]:
    agreements = 0
    for trial in range(n_trials):
        seed = SEED_BASE + hash((sd, n, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_case_correlated_pair_absolute_sd(gen, n, EFFECT_PCT, sd)
        deltas = [c - b for b, c in zip(baseline, current, strict=True)]
        lo_fast, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=CONFIDENCE, n_boot=1_000, seed=trial
        )
        lo_full, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=CONFIDENCE, n_boot=10_000, seed=trial
        )
        if (lo_fast > 0.0) == (lo_full > 0.0):
            agreements += 1
    return agreements, n_trials


def compute_grid() -> dict[tuple[float, int], tuple[int, int]]:
    detections: dict[tuple[float, int], int] = {(sd, n): 0 for sd in SD_GRID for n in N_GRID}
    for sd in SD_GRID:
        for n in N_GRID:
            for trial in range(N_TRIALS):
                seed = SEED_BASE + hash((sd, n, trial)) % 1_000_000
                gen = random.Random(seed)
                baseline, current = generate_case_correlated_pair_absolute_sd(
                    gen, n, EFFECT_PCT, sd
                )
                deltas = [c - b for b, c in zip(baseline, current, strict=True)]
                ci_lower, _ = bootstrap_mean_of_paired_deltas(
                    deltas, confidence=CONFIDENCE, n_boot=N_BOOT, seed=trial
                )
                if ci_lower > 0.0:
                    detections[(sd, n)] += 1
    return {key: (count, N_TRIALS) for key, count in detections.items()}


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000) ===")
    for sd, n in [(0.0002, 30), (0.0032, 100)]:
        a, nt = validate_n_boot(sd, n)
        print(f"  sd={sd} n={n}: {a}/{nt} = {a / nt * 100:.1f}% agreement")

    grid = compute_grid()

    print(
        f"\n=== Regime A (fixed absolute SD), power to detect 10% effect, confidence={CONFIDENCE} ==="
    )
    header = "SD\\n".ljust(10) + "".join(f"{n:>24}" for n in N_GRID)
    print(header)
    results = {}
    for sd in SD_GRID:
        cells = []
        for n in N_GRID:
            det, nt = grid[(sd, n)]
            phat = det / nt
            lo, hi = wilson_score_interval(det, nt)
            cells.append(f"{phat:>7.4f} [{lo:.4f},{hi:.4f}]")
            results[f"{sd}|{n}"] = {
                "detections": det,
                "n_trials": nt,
                "detection_rate": phat,
                "wilson_95ci": [lo, hi],
            }
        row = f"${sd}".ljust(10) + "".join(f"{c:>24}" for c in cells)
        print(row)

    out_path = Path(__file__).resolve().parent.parent / "reports" / "absolute_sd_grid.json"
    out_path.write_text(
        json.dumps(
            {
                "confidence": CONFIDENCE,
                "effect_pct": EFFECT_PCT,
                "n_boot": N_BOOT,
                "n_trials": N_TRIALS,
                "sd_grid": SD_GRID,
                "n_grid": N_GRID,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
