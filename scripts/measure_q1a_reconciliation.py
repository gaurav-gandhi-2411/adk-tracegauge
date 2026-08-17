"""scripts/measure_q1a_reconciliation.py — Q1a: reconcile the published
paired-mode grid (`generate_case_correlated_pair`, constant-ABSOLUTE
within-case SD) against Q1's real measurement (a scale-invariant CV,
0.1566, from a real evalset with its own, much smaller, dollar scale).

**Diagnosis (1a.2), from reading both generators directly — effect model
is IDENTICAL, not the source of the gap:**

    # original (generate_case_correlated_pair, measure_regression_power.py):
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    current = [max(0.0001, rng.gauss(d + effect_usd, CASE_CORRELATED_WITHIN_CASE_SD))
               for d in case_levels]

    # mine (generate_paired_pair_cv, measure_power_by_cv_grid.py):
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    current = [max(0.0001, rng.gauss(d + effect_usd, cv * (d + effect_usd)))
               for d in case_levels]

Both add the SAME flat `effect_usd` to every case identically (case-
correlated, per Q1a.6). NOT where the harnesses diverge.

**1a.3 — the real divergence, and a FALSE START worth recording plainly.**
The original uses a FIXED ABSOLUTE dollar SD (`CASE_CORRELATED_WITHIN_
CASE_SD=0.0008`), implying a CV that VARIES 3.3%-20% across the case-level
range (0.0008/0.024 to 0.0008/0.004) -- this is where Q1a.1's "3-20% CV
band" comes from. Mine uses an explicit `cv` PROPORTIONAL to each case's
own level.

The FIRST attempt at this script plugged Q1's raw measured dollar SD
($0.0000285, from a real evalset with mean cost ~$0.000182) directly into
a constant-absolute-SD generator whose case levels are Uniform(0.004,
0.024) -- a completely different dollar scale (~$0.014 mean, ~78x
larger). That produced 100% power at every cell -- an ARTIFACT of the
scale mismatch (the same $0.0000285 that is 15.66% of Q1's real mean cost
becomes an utterly negligible ~0.2% of the synthetic case levels' mean),
not a real finding, and NOT published. Caught by checking the result
against a scale-invariant re-derivation before trusting it (below) --
same "audit the measurement" discipline as every other correction in this
investigation.

**The scale-CORRECT reconciliation**: CV is dimensionless and portable
across dollar scales; raw dollar SDs are not. The right comparison is
apples-to-apples WITHIN the same proportional-CV generator
(`generate_paired_pair_cv`, already built and validated in AD1's own CV
sweep -- reused unchanged here, no new generator) at TWO cv values:

1. The original published grid's own IMPLIED AVERAGE CV:
   ``CASE_CORRELATED_WITHIN_CASE_SD / mean(case_level) = 0.0008 / 0.014
   ~= 0.0571`` (using the case-level range's own mean as the reference --
   the same range both generators share). If the proportional-CV model at
   this cv reproduces ~99% power, that CONFIRMS the two harnesses are
   consistent once compared on the same (proportional-CV) footing, and
   the "gap" is fully explained by REAL measured CV (15.66%) being ~2.7x
   the original's ASSUMED average CV (5.71%) -- not a bug in either
   harness.
2. Q1's real measured CV (0.1566) -- same generator, same everything else.

Run: ``uv run python scripts/measure_q1a_reconciliation.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from measure_power_by_cv_grid import (  # noqa: E402
    compute_paired_cv_grid,
    validate_n_boot_paired,
)
from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402
from measure_regression_power import (  # noqa: E402
    CASE_CORRELATED_LEVEL_HIGH,
    CASE_CORRELATED_LEVEL_LOW,
    CASE_CORRELATED_WITHIN_CASE_SD,
)

CONFIDENCE = 0.98
N_GRID = [30, 36]
EFFECT_PCT_GRID = [10.0, 25.0]
N_TRIALS = 2_000
N_BOOT = 1_000

MEAN_CASE_LEVEL = (CASE_CORRELATED_LEVEL_LOW + CASE_CORRELATED_LEVEL_HIGH) / 2.0
ORIGINAL_IMPLIED_AVERAGE_CV = CASE_CORRELATED_WITHIN_CASE_SD / MEAN_CASE_LEVEL
"""~=0.0571 -- the original published grid's own constant-absolute-SD,
re-expressed as a CV relative to the case-level range's mean, for a
like-for-like comparison against the proportional-CV model."""

MEASURED_WITHIN_CASE_CV = 0.15664010949078117
"""Exact measured value from reports/q1_within_case_cv.json."""


def main() -> int:
    print(f"Original implied average CV: {ORIGINAL_IMPLIED_AVERAGE_CV:.4f}")
    print(f"Measured within-case CV: {MEASURED_WITHIN_CASE_CV:.4f}")

    print("\n=== N_BOOT validation (1,000 vs 10,000) ===")
    for cv, n in [(ORIGINAL_IMPLIED_AVERAGE_CV, 30), (MEASURED_WITHIN_CASE_CV, 36)]:
        a, nt = validate_n_boot_paired(cv, n, 10.0)
        print(f"  cv={cv:.4f} n={n}: {a}/{nt} = {a / nt * 100:.1f}% agreement")

    all_results = {}
    for cv, label in [
        (ORIGINAL_IMPLIED_AVERAGE_CV, "REPRODUCTION at original's own implied average CV"),
        (MEASURED_WITHIN_CASE_CV, "MEASURED at Q1's real within-case CV"),
    ]:
        results = {}
        for e in EFFECT_PCT_GRID:
            grid = compute_paired_cv_grid(
                cv_grid=[cv], n_grid=N_GRID, effect_pct=e,
                n_trials=N_TRIALS, n_boot=N_BOOT, confidence=CONFIDENCE,
            )
            print(f"\n=== {label} (cv={cv:.4f}, effect={e}%) ===")
            for n in N_GRID:
                det, nt = grid[(cv, n)]
                phat = det / nt
                lo, hi = wilson_score_interval(det, nt)
                print(f"n={n}: {phat:.4f} [{lo:.4f},{hi:.4f}] ({det}/{nt})")
                results[f"{n}|{e}"] = {
                    "detections": det, "n_trials": nt, "detection_rate": phat,
                    "wilson_95ci": [lo, hi],
                }
        all_results[label] = {"cv": cv, "results": results}

    out_path = Path(__file__).resolve().parent.parent / "reports" / "q1a_reconciliation.json"
    out_path.write_text(
        json.dumps(
            {
                "confidence": CONFIDENCE,
                "n_boot": N_BOOT,
                "n_trials": N_TRIALS,
                "original_implied_average_cv": ORIGINAL_IMPLIED_AVERAGE_CV,
                "measured_within_case_cv": MEASURED_WITHIN_CASE_CV,
                "results": all_results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
