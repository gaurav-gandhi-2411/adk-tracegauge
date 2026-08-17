"""scripts/measure_q1_within_case_power.py — Q1.3: locate the MEASURED
within-case CV (0.1566, from `scripts/measure_within_case_cv_ollama.py` /
`reports/q1_within_case_cv.json`) on the paired-mode power grid, at the
ACTUAL evalset size (n=36) and the shipped min_n (n=30) -- not an
interpolation between `measure_power_by_cv_grid.py`'s {0.1, 0.2, 0.4, 0.6,
1.0} grid points, a real measurement at the real value.

Reuses `measure_power_by_cv_grid.py`'s own generator
(`generate_paired_pair_cv`) and methodology unchanged (paired mode,
confidence=0.98 shipped default, n_boot=1,000 already validated against
10,000 in that script) -- only the (cv, n, effect) grid is different.

Run: ``uv run python scripts/measure_q1_within_case_power.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from measure_power_by_cv_grid import compute_paired_cv_grid, validate_n_boot_paired  # noqa: E402
from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402

MEASURED_WITHIN_CASE_CV = 0.1566
"""From reports/q1_within_case_cv.json -- measured, not assumed."""
N_GRID = [30, 36]
EFFECT_PCT_GRID = [10.0, 25.0]
CONFIDENCE = 0.98
N_TRIALS = 2_000
N_BOOT = 1_000


def main() -> int:
    print("=== N_BOOT validation (1,000 vs 10,000) at the measured CV ===")
    for n, effect_pct in [(30, 10.0), (36, 25.0)]:
        a, nt = validate_n_boot_paired(MEASURED_WITHIN_CASE_CV, n, effect_pct)
        print(f"  n={n} effect={effect_pct}%: {a}/{nt} = {a / nt * 100:.1f}% agreement")

    print(
        f"\n=== PAIRED power at measured within-case CV={MEASURED_WITHIN_CASE_CV}, "
        f"confidence={CONFIDENCE} ==="
    )
    results = {}
    for e in EFFECT_PCT_GRID:
        grid = compute_paired_cv_grid(
            cv_grid=[MEASURED_WITHIN_CASE_CV],
            n_grid=N_GRID,
            effect_pct=e,
            n_trials=N_TRIALS,
            n_boot=N_BOOT,
            confidence=CONFIDENCE,
        )
        for n in N_GRID:
            det, nt = grid[(MEASURED_WITHIN_CASE_CV, n)]
            phat = det / nt
            lo, hi = wilson_score_interval(det, nt)
            print(f"n={n} effect={e}%: {phat:.4f} [{lo:.4f},{hi:.4f}] ({det}/{nt})")
            results[f"{n}|{e}"] = {
                "detections": det, "n_trials": nt, "detection_rate": phat,
                "wilson_95ci": [lo, hi],
            }

    out_path = Path(__file__).resolve().parent.parent / "reports" / "q1_within_case_power.json"
    out_path.write_text(
        json.dumps(
            {
                "measured_within_case_cv": MEASURED_WITHIN_CASE_CV,
                "confidence": CONFIDENCE,
                "n_trials": N_TRIALS,
                "n_boot": N_BOOT,
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
