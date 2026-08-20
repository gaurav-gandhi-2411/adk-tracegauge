"""scripts/measure_al1_al2_extended_grids.py — AN1.4/AN2.2/AN2.3: two new
power computations, both zero-cost local Monte Carlo simulation (no API
calls), reusing measure_power_by_cv_grid.py's generators/methodology
unchanged.

1. AN2.2 — extend Regime B's two-sample AND paired CV grids to CV in
   {1.5, 2.0}, same n_grid/trial-count/Wilson-CI convention as the
   existing published {0.1, 0.2, 0.4, 0.6, 1.0} grid. Motivated by AD2.6's
   finding: measured across-case CV (1.2326, gemini-3.5-flash-lite) sits
   beyond the published grid's top row (1.0).

2. AN1.4/AN2.3 — paired-mode power at the ACTUAL measured within-case CV
   (0.1307, gemini-3.5-flash-lite, reports/al2_within_case_cv_gemini.json),
   at n in {30, 36}, effect in {10%, 25%} -- an exact computed point, not
   an interpolation between grid rows, same convention as
   measure_q1_within_case_power.py's real-CV point for Ollama (0.1566).

Run: ``uv run python scripts/measure_al1_al2_extended_grids.py``
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
    N_GRID,
    N_TRIALS,
    compute_paired_cv_grid,
    compute_two_sample_cv_grid,
)
from measure_regression_confidence_grid import wilson_score_interval  # noqa: E402

EXTENDED_CV_GRID = [1.5, 2.0]
EFFECT_PCT = 10.0  # matches the original grid's own effect size

MEASURED_WITHIN_CASE_CV_GEMINI = 0.1307
"""From reports/al2_within_case_cv_gemini.json -- measured, not assumed."""
POINT_N_GRID = [30, 36]
POINT_EFFECT_GRID = [10.0, 25.0]


def _print_grid(grid: dict, cv_grid: list[float], n_grid: list[int], label: str) -> None:
    print(f"\n=== {label} (power to detect true {EFFECT_PCT:.0f}% regression, confidence={CONFIDENCE}) ===")
    header = "CV\\n".ljust(8) + "".join(f"{n:>24}" for n in n_grid)
    print(header)
    for cv in cv_grid:
        cells = []
        for n in n_grid:
            detections, n_trials = grid[(cv, n)]
            phat = detections / n_trials
            lo, hi = wilson_score_interval(detections, n_trials)
            cells.append(f"{phat:>7.4f} [{lo:.4f},{hi:.4f}]")
        row = f"{cv:<8}" + "".join(f"{c:>24}" for c in cells)
        print(row)


def main() -> int:
    print(f"=== PART 1 (AN2.2): extending Regime B grids to CV={EXTENDED_CV_GRID} ===")
    print(f"{len(EXTENDED_CV_GRID)}x{len(N_GRID)} cells/mode, {N_TRIALS} trials/cell, n_boot={N_BOOT}, effect={EFFECT_PCT}%, confidence={CONFIDENCE}")

    t0 = time.time()
    two_sample_ext = compute_two_sample_cv_grid(
        cv_grid=EXTENDED_CV_GRID, n_grid=N_GRID, effect_pct=EFFECT_PCT, n_trials=N_TRIALS, n_boot=N_BOOT, confidence=CONFIDENCE
    )
    two_sample_elapsed = time.time() - t0
    print(f"two-sample extension done in {two_sample_elapsed:.1f}s")

    t1 = time.time()
    paired_ext = compute_paired_cv_grid(
        cv_grid=EXTENDED_CV_GRID, n_grid=N_GRID, effect_pct=EFFECT_PCT, n_trials=N_TRIALS, n_boot=N_BOOT, confidence=CONFIDENCE
    )
    paired_elapsed = time.time() - t1
    print(f"paired extension done in {paired_elapsed:.1f}s")

    _print_grid(two_sample_ext, EXTENDED_CV_GRID, N_GRID, "TWO-SAMPLE power, EXTENDED [Wilson 95% CI]")
    _print_grid(paired_ext, EXTENDED_CV_GRID, N_GRID, "PAIRED power, EXTENDED [Wilson 95% CI]")

    print(f"\n=== PART 2 (AN1.4/AN2.3): paired power at MEASURED within-case CV={MEASURED_WITHIN_CASE_CV_GEMINI} (gemini-3.5-flash-lite) ===")
    point_results = {}
    for e in POINT_EFFECT_GRID:
        grid = compute_paired_cv_grid(
            cv_grid=[MEASURED_WITHIN_CASE_CV_GEMINI],
            n_grid=POINT_N_GRID,
            effect_pct=e,
            n_trials=N_TRIALS,
            n_boot=N_BOOT,
            confidence=CONFIDENCE,
        )
        for n in POINT_N_GRID:
            det, nt = grid[(MEASURED_WITHIN_CASE_CV_GEMINI, n)]
            phat = det / nt
            lo, hi = wilson_score_interval(det, nt)
            print(f"n={n} effect={e}%: {phat:.4f} [{lo:.4f},{hi:.4f}] ({det}/{nt})")
            point_results[f"{n}|{e}"] = {
                "detections": det,
                "n_trials": nt,
                "detection_rate": phat,
                "wilson_95ci": [lo, hi],
            }

    out_path = Path(__file__).resolve().parent.parent / "reports" / "al1_al2_extended_grids.json"
    out_path.write_text(
        json.dumps(
            {
                "part1_extended_regime_b": {
                    "confidence": CONFIDENCE,
                    "effect_pct": EFFECT_PCT,
                    "n_boot": N_BOOT,
                    "n_trials": N_TRIALS,
                    "extended_cv_grid": EXTENDED_CV_GRID,
                    "n_grid": N_GRID,
                    "two_sample": {
                        f"{cv}|{n}": {
                            "detections": det,
                            "n_trials": nt,
                            "detection_rate": det / nt,
                            "wilson_95ci": list(wilson_score_interval(det, nt)),
                        }
                        for (cv, n), (det, nt) in two_sample_ext.items()
                    },
                    "paired": {
                        f"{cv}|{n}": {
                            "detections": det,
                            "n_trials": nt,
                            "detection_rate": det / nt,
                            "wilson_95ci": list(wilson_score_interval(det, nt)),
                        }
                        for (cv, n), (det, nt) in paired_ext.items()
                    },
                    "wall_clock_seconds": two_sample_elapsed + paired_elapsed,
                },
                "part2_measured_within_case_point": {
                    "measured_within_case_cv": MEASURED_WITHIN_CASE_CV_GEMINI,
                    "model": "gemini-3.5-flash-lite",
                    "source": "reports/al2_within_case_cv_gemini.json",
                    "confidence": CONFIDENCE,
                    "n_trials": N_TRIALS,
                    "n_boot": N_BOOT,
                    "results": point_results,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
