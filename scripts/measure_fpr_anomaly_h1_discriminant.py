"""scripts/measure_fpr_anomaly_h1_discriminant.py — FPR-anomaly audit, hypothesis 1
discriminating test (see ``docs/audit/FPR_ANOMALY.md`` for the full investigation).

**Anomaly under investigation**: the original Phase 7 U2 grid
(``scripts/measure_regression_confidence_grid.py``, ``reports/confidence_grid_u2.json``)
reported paired mode's measured FPR exceeding two-sample's at 4 of 6 shared
(confidence, n) cells -- published without a significance test. Steps 3.1-3.3 of
the audit confirmed no bug in either mode's null-data generator (both correctly
preserve/omit case-level pairing structure as appropriate) and no bug in the
paired bootstrap's resampling loop (it correctly resamples delta VECTORS as a
unit, never baseline/current independently -- see ``bootstrap_mean_of_paired_deltas``
in ``_regression.py``).

**H1 (this script)**: the gap is a real, generic structural effect of comparing a
ONE-SAMPLE bootstrap (paired: one resampled sequence of deltas) against a
TWO-SAMPLE bootstrap (two independently-resampled sequences) at the SAME total
variance budget and the SAME n -- specifically, that the two-sample CI's width
benefits from averaging TWO independent empirical variance estimates
(baseline's own sample variance, current's own), while the paired CI's width
relies on only ONE (the deltas' own sample variance), and since a sample
variance's own sampling distribution (chi-squared) is right-skewed (more often
below the true value than above), a single-estimate CI could be systematically
too narrow slightly more often than an averaged-two-estimate CI -- elevating
one-sample FPR above two-sample FPR even on an EXACTLY Gaussian population
(zero skewness), which is what both real null generators produce for the
per-observation/per-delta quantity under H0 (confirmed in the audit's 3.1).

**Design**: strip away everything generator-specific (case levels, floor
clipping) and compare, on EXACTLY Gaussian synthetic data with the SAME TOTAL
VARIANCE BUDGET and the SAME n, using the REAL production bootstrap functions
(not a reimplementation):

- ONE-SAMPLE (paired-style): ``n`` iid ~ N(0, SD_DELTA), passed to the real
  ``bootstrap_mean_of_paired_deltas``.
- TWO-SAMPLE (two-sample-style): ``n`` iid ~ N(MU, SD_COMPONENT) for baseline,
  ``n`` iid ~ N(MU, SD_COMPONENT) for current (SD_COMPONENT = SD_DELTA /
  sqrt(2), so Var(diff of means) = Var(one-sample mean) exactly), passed to
  the real ``bootstrap_diff_of_means``.

If H1 is TRUE: one-sample FPR is measurably, significantly higher than
two-sample FPR across multiple cells. If REFUTED: no systematic gap at this
matched-variance/matched-n setup, and the real explanation must lie elsewhere.

**RESULT (this exact run, N_TRIALS=3,000, N_BOOT=1,000, this repo's committed
seed bases below): H1 REFUTED.** 5 of 6 cells show no significant difference
(p>0.19); the 6th (confidence=0.99, n=50) reaches z=2.153, p=0.0313 but in a
trial count this size that single crossing is exactly what ~5% false-discovery
would produce by chance across 6 independent tests, not a robust pattern (no
other cell replicates the direction/magnitude). See
``docs/audit/FPR_ANOMALY.md`` section 3.4 for the full table and reasoning,
and ``scripts/measure_fpr_anomaly_reproducibility.py`` for the follow-up
hypothesis this refutation led to.

Run: ``uv run python scripts/measure_fpr_anomaly_h1_discriminant.py``
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

from measure_regression_confidence_grid import (  # noqa: E402
    two_proportion_z_test,
    wilson_score_interval,
)

from adk_tracegauge._regression import (  # noqa: E402
    bootstrap_diff_of_means,
    bootstrap_mean_of_paired_deltas,
)

# Matches the real case-correlated generator's implied null delta sd exactly:
# delta_i = N(d_i, WITHIN_CASE_SD) - N(d_i, WITHIN_CASE_SD) => N(0, sqrt(2)*WITHIN_CASE_SD)
# (WITHIN_CASE_SD taken from measure_regression_power.py's CASE_CORRELATED_WITHIN_CASE_SD)
WITHIN_CASE_SD = 0.0008
SD_DELTA = math.sqrt(2.0) * WITHIN_CASE_SD  # true sd of the one-sample (paired-style) population
SD_COMPONENT = SD_DELTA / math.sqrt(2.0)  # = WITHIN_CASE_SD; each two-sample group's own sd
MU = (
    0.010  # arbitrary nonzero mean for the two-sample groups (irrelevant to a mean-DIFFERENCE test)
)

CONFIDENCE_GRID = [0.95, 0.98, 0.99]
N_GRID = [30, 50]
N_TRIALS = 3_000
N_BOOT = 1_000
SEED_BASE_ONE_SAMPLE = 5_000_000
"""Distinct from every other seed base in this codebase (see
measure_regression_confidence_grid.py's own SEED_BASE docstring for the
full registry this extends)."""
SEED_BASE_TWO_SAMPLE = 5_100_000


def run_one_sample_cell(confidence: float, n: int, n_trials: int = N_TRIALS) -> tuple[int, int]:
    """One-sample (paired-style) FPR cell: ``n`` iid ~ N(0, SD_DELTA), the
    REAL production ``bootstrap_mean_of_paired_deltas``, ``ci_lower > 0.0``
    counted as a false positive (true mean is exactly 0 by construction).
    """
    detections = 0
    for trial in range(n_trials):
        seed = SEED_BASE_ONE_SAMPLE + hash((confidence, n, trial)) % 1_000_000
        gen = random.Random(seed)
        deltas = [gen.gauss(0.0, SD_DELTA) for _ in range(n)]
        ci_lower, _ = bootstrap_mean_of_paired_deltas(
            deltas, confidence=confidence, n_boot=N_BOOT, seed=trial
        )
        if ci_lower > 0.0:
            detections += 1
    return detections, n_trials


def run_two_sample_cell(confidence: float, n: int, n_trials: int = N_TRIALS) -> tuple[int, int]:
    """Two-sample-style FPR cell, SAME total variance budget/n as
    ``run_one_sample_cell``: ``n`` iid ~ N(MU, SD_COMPONENT) for baseline and
    current independently, the REAL production ``bootstrap_diff_of_means``.
    """
    detections = 0
    for trial in range(n_trials):
        seed = SEED_BASE_TWO_SAMPLE + hash((confidence, n, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline = [gen.gauss(MU, SD_COMPONENT) for _ in range(n)]
        current = [gen.gauss(MU, SD_COMPONENT) for _ in range(n)]
        ci_lower, _ = bootstrap_diff_of_means(
            baseline, current, confidence=confidence, n_boot=N_BOOT, seed=trial
        )
        if ci_lower > 0.0:
            detections += 1
    return detections, n_trials


def main() -> int:
    print(
        f"SD_DELTA={SD_DELTA:.8f}  SD_COMPONENT={SD_COMPONENT:.8f}  (matched total variance budget)"
    )
    print(
        f"N_TRIALS={N_TRIALS} per cell, N_BOOT={N_BOOT}, real production "
        "bootstrap_mean_of_paired_deltas / bootstrap_diff_of_means\n"
    )
    header = (
        f"{'conf':>5} {'n':>3} | {'one-sample x/n':>15} {'rate%':>7} {'Wilson95%':>18} | "
        f"{'two-sample x/n':>15} {'rate%':>7} {'Wilson95%':>18} | {'nominal%':>8} | z(1s vs 2s) p"
    )
    print(header)
    t0 = time.time()
    for confidence in CONFIDENCE_GRID:
        nominal = (1 - confidence) / 2 * 100
        for n in N_GRID:
            x1, n1 = run_one_sample_cell(confidence, n)
            x2, n2 = run_two_sample_cell(confidence, n)
            w1 = wilson_score_interval(x1, n1)
            w2 = wilson_score_interval(x2, n2)
            z, p = two_proportion_z_test(x2, n2, x1, n1)  # positive z means one-sample > two-sample
            print(
                f"{confidence:>5} {n:>3} | {x1:>4}/{n1:<6} {x1 / n1 * 100:>6.3f}% "
                f"[{w1[0] * 100:.3f},{w1[1] * 100:.3f}]% | {x2:>4}/{n2:<6} {x2 / n2 * 100:>6.3f}% "
                f"[{w2[0] * 100:.3f},{w2[1] * 100:.3f}]% | {nominal:>7.3f}% | z={z:>6.3f} p={p:.4f}"
            )
    print(f"\nWall-clock: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
