"""scripts/measure_ap2_n_sweep.py — AP2: at the real measured hosted-model
within-case CV (0.1307, gemini-3.5-flash-lite, docs/audit/
AD2_REAL_CV_MEASUREMENT.md), what n does paired mode actually need to
clear 80% power?

Zero-cost local Monte Carlo simulation (no API calls), reusing
measure_power_by_cv_grid.py's compute_paired_cv_grid unchanged -- only the
(cv, n_grid, effect) combination is new. Sweeps n in {50, 75, 100, 150,
200} at the real measured CV, for both a 10% effect (the one the shipped
default's power tables have always used) and a 25% effect (AP2.2, so the
contrast against a regression size the gate ALREADY reliably catches at
n=30 is explicit).

Run: ``uv run python scripts/measure_ap2_n_sweep.py``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from measure_power_by_cv_grid import (  # noqa: E402
    CONFIDENCE,
    N_BOOT,
    N_TRIALS,
    compute_paired_cv_grid,
)
from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402

MEASURED_WITHIN_CASE_CV_GEMINI = 0.1307
N_SWEEP = [50, 75, 100, 150, 200]
EFFECT_GRID = [10.0, 25.0]


def main() -> int:
    print(
        f"=== AP2: paired power at CV={MEASURED_WITHIN_CASE_CV_GEMINI}, "
        f"n in {N_SWEEP}, effects {EFFECT_GRID}%, {N_TRIALS} trials/cell, "
        f"confidence={CONFIDENCE} ==="
    )

    results = {}
    n_clearing_80pct = {}
    for effect in EFFECT_GRID:
        t0 = time.time()
        grid = compute_paired_cv_grid(
            cv_grid=[MEASURED_WITHIN_CASE_CV_GEMINI],
            n_grid=N_SWEEP,
            effect_pct=effect,
            n_trials=N_TRIALS,
            n_boot=N_BOOT,
            confidence=CONFIDENCE,
        )
        elapsed = time.time() - t0
        print(f"\n--- effect={effect}% (done in {elapsed:.1f}s) ---")
        first_80 = None
        for n in N_SWEEP:
            det, nt = grid[(MEASURED_WITHIN_CASE_CV_GEMINI, n)]
            phat = det / nt
            lo, hi = wilson_score_interval(det, nt)
            print(f"  n={n}: {phat:.4f} [{lo:.4f},{hi:.4f}] ({det}/{nt})")
            results[f"{effect}|{n}"] = {
                "detections": det,
                "n_trials": nt,
                "detection_rate": phat,
                "wilson_95ci": [lo, hi],
            }
            if first_80 is None and phat >= 0.80:
                first_80 = n
        n_clearing_80pct[str(effect)] = first_80
        print(f"  first n in sweep clearing 80% power: {first_80}")

    out_path = Path(__file__).resolve().parent.parent / "reports" / "ap2_n_sweep.json"
    out_path.write_text(
        json.dumps(
            {
                "measured_within_case_cv": MEASURED_WITHIN_CASE_CV_GEMINI,
                "model": "gemini-3.5-flash-lite",
                "confidence": CONFIDENCE,
                "n_trials": N_TRIALS,
                "n_boot": N_BOOT,
                "n_sweep": N_SWEEP,
                "effect_grid": EFFECT_GRID,
                "results": results,
                "first_n_clearing_80pct_power": n_clearing_80pct,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
