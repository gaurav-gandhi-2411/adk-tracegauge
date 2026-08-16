"""scripts/measure_fpr_anomaly_reproducibility.py — FPR-anomaly audit,
reproducibility check (see ``docs/audit/FPR_ANOMALY.md`` for the full
investigation; this is the discriminating test for hypothesis 2, formed
after ``measure_fpr_anomaly_h1_discriminant.py`` REFUTED hypothesis 1 --
a structural one-sample-vs-two-sample variance-averaging effect did not
reproduce on matched-variance synthetic Gaussian data).

**H2**: the original Phase 7 U2 grid's "paired FPR > two-sample FPR at 4/6
cells" comparison (``reports/confidence_grid_u2.json``, 2,000 trials/cell) is
dominated by ordinary sampling noise in a rare-event binomial proportion
(as few as 10 successes out of 2,000 trials at some cells), not a robust,
reproducible property of paired mode's real generator specifically.
Percentile-bootstrap FPR is ALREADY documented (``_regression.py``'s "Anti-
conservatism at small n" section) as generically anti-conservative at small
``n`` for BOTH modes -- the specific per-cell magnitude of that
anti-conservatism is itself noisy at 2,000 trials/cell when the true rate is
0.5-3.7%.

**Design**: re-measure the REAL null generators
(``measure_regression_alpha_grid._generate_pair`` for two-sample,
``measure_regression_power.generate_case_correlated_pair`` for paired -- the
SAME generators the original grid used, reused by import, not
reimplemented) at the SAME (confidence, n) FPR cells, with a NEW,
independent seed base and 2.5x the original trial count (5,000 vs 2,000).
If H2 is TRUE: the paired-vs-two-sample gap should not reproduce as
significant, and/or the specific cells "significant" in the original run
should not all replicate (regression to the mean of a noisy estimate). If
H2 is FALSE (the anomaly reproduces essentially unchanged, or strengthens):
it is a real, reproducible property, and the investigation must return to
what differs between the two REAL generators.

**RESULT (this exact run, N_TRIALS=5,000, N_BOOT=1,000, seed bases below):
H2 CONFIRMED.** None of the 6 paired-vs-two-sample comparisons reach
significance (largest z=1.29, p=0.20 at confidence=0.95/n=50) -- the
original grid's ranking does not reproduce. Both modes independently show
significant elevation above their OWN nominal one-sided alpha at 5-6 of 6
cells each (e.g. two-sample at confidence=0.99/n=30: 0.86% vs 0.5% nominal,
z=3.61, p=0.0003) -- the SAME generic small-n percentile-bootstrap
anti-conservatism already documented for both modes, not a paired-specific
defect. See ``docs/audit/FPR_ANOMALY.md`` section 3.4/3.5 for the full
table, the original-vs-new per-cell consistency check, and the resulting
correction to the published grid
(``scripts/measure_regression_confidence_grid.py``, ``N_TRIALS`` raised to
5,000).

Run: ``uv run python scripts/measure_fpr_anomaly_reproducibility.py``
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from measure_regression_alpha_grid import _generate_pair as generate_two_sample_pair  # noqa: E402
from measure_regression_confidence_grid import (  # noqa: E402
    two_proportion_z_test,
    wilson_score_interval,
)
from measure_regression_power import generate_case_correlated_pair  # noqa: E402

from adk_tracegauge._regression import (  # noqa: E402
    bootstrap_diff_of_means,
    bootstrap_mean_of_paired_deltas,
)

CONFIDENCE_GRID = [0.95, 0.98, 0.99]
N_GRID = [30, 50]
N_TRIALS = 5_000  # 2.5x the original grid's 2,000/cell, independent seed base
N_BOOT = 1_000
SEED_BASE_TWO_SAMPLE = 9_000_000
"""Distinct from every other seed base in this codebase (see
measure_regression_confidence_grid.py's own SEED_BASE docstring for the
full registry this extends) -- deliberately DIFFERENT from
measure_regression_confidence_grid.py's own 1_000_000/1_100_000, since the
whole point here is an INDEPENDENT re-measurement, not an extension of the
same RNG stream."""
SEED_BASE_PAIRED = 9_100_000


def run_two_sample_fpr_cell(confidence: float, n: int, n_trials: int = N_TRIALS) -> tuple[int, int]:
    """Real two-sample null generator + real production bootstrap, at the
    given (confidence, n) cell, effect_pct=0.0 (no true regression)."""
    detections = 0
    for trial in range(n_trials):
        seed = SEED_BASE_TWO_SAMPLE + hash((n, 0.0, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_two_sample_pair(gen, n, 0.0)
        ci_lower, _ = bootstrap_diff_of_means(
            baseline, current, confidence=confidence, n_boot=N_BOOT, seed=trial
        )
        if ci_lower > 0.0:
            detections += 1
    return detections, n_trials


def run_paired_fpr_cell(confidence: float, n: int, n_trials: int = N_TRIALS) -> tuple[int, int]:
    """Real case-correlated (paired) null generator + real production
    bootstrap, at the given (confidence, n) cell, effect_pct=0.0."""
    detections = 0
    for trial in range(n_trials):
        seed = SEED_BASE_PAIRED + hash((n, 0.0, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = generate_case_correlated_pair(gen, n, 0.0)
        deltas = [c - b for b, c in zip(baseline, current, strict=True)]
        ci_lower, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=confidence, n_boot=N_BOOT, seed=trial
        )
        if ci_lower > 0.0:
            detections += 1
    return detections, n_trials


def main() -> int:
    print(
        f"REPRODUCIBILITY CHECK: N_TRIALS={N_TRIALS}/cell (vs original 2,000), independent seed base"
    )
    print(f"SEED_BASE_TWO_SAMPLE={SEED_BASE_TWO_SAMPLE} SEED_BASE_PAIRED={SEED_BASE_PAIRED}\n")
    header = (
        f"{'conf':>5} {'n':>3} | {'2samp x/n':>11} {'rate%':>7} {'Wilson95%':>18} | "
        f"{'paired x/n':>11} {'rate%':>7} {'Wilson95%':>18} | {'nominal%':>8} | "
        "z(2s-nom) p | z(pr-nom) p | z(pr-2s) p"
    )
    print(header)
    t0 = time.time()
    for confidence in CONFIDENCE_GRID:
        nominal = (1 - confidence) / 2
        for n in N_GRID:
            x1, n1 = run_two_sample_fpr_cell(confidence, n)
            x2, n2 = run_paired_fpr_cell(confidence, n)
            w1 = wilson_score_interval(x1, n1)
            w2 = wilson_score_interval(x2, n2)
            # nominal treated as a fixed reference value: z = (phat - p0) / sqrt(p0*(1-p0)/n)
            se1 = math.sqrt(nominal * (1 - nominal) / n1)
            z1n = (x1 / n1 - nominal) / se1
            p1n = math.erfc(abs(z1n) / math.sqrt(2))
            se2 = math.sqrt(nominal * (1 - nominal) / n2)
            z2n = (x2 / n2 - nominal) / se2
            p2n = math.erfc(abs(z2n) / math.sqrt(2))
            z12, p12 = two_proportion_z_test(x1, n1, x2, n2)
            print(
                f"{confidence:>5} {n:>3} | {x1:>4}/{n1:<6} {x1 / n1 * 100:>6.3f}% "
                f"[{w1[0] * 100:.3f},{w1[1] * 100:.3f}]% | {x2:>4}/{n2:<6} {x2 / n2 * 100:>6.3f}% "
                f"[{w2[0] * 100:.3f},{w2[1] * 100:.3f}]% | {nominal * 100:>7.3f}% | "
                f"z={z1n:>6.3f} p={p1n:.4f} | z={z2n:>6.3f} p={p2n:.4f} | z={z12:>6.3f} p={p12:.4f}"
            )
    print(f"\nWall-clock: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
