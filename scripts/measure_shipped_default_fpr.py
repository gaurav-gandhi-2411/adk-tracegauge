"""scripts/measure_shipped_default_fpr.py — Phase 5 S4, 4.4/4.5: the REAL
shipped-configuration false-positive rate at a given ``--confidence``
value, for the README/CHANGELOG numbers and for comparing against the
STATISTICAL-ONLY rate (``scripts/measure_regression_alpha_grid.py``,
floors disabled) to isolate the practical-significance floor's own,
independent contribution to false-positive suppression (4.5).

Mirrors Phase 4 R4's own 4.4 measurement pattern exactly (real
`min_effect_usd`/`min_effect_pct` defaults NOT disabled, real
`n_boot=10,000`, 500 trials, two independent seed bases as a
cross-check) — just parameterized over `--confidence` so it can be re-run
for both the OLD (0.95) and the NEW shipped default chosen by this work
item, at min_n (n=30).

Run: ``uv run python scripts/measure_shipped_default_fpr.py --confidence 0.98``
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._regression import (  # noqa: E402
    DEFAULT_MIN_EFFECT_PCT,
    DEFAULT_MIN_EFFECT_USD,
    DEFAULT_N_BOOT,
    MIN_N_DEFAULT,
    evaluate_regression,
)

BASE_MEAN = 0.010
BASE_SD = 0.0015


def _measure(
    confidence: float,
    seed_base: int,
    n_trials: int,
    n_per_group: int,
    n_boot: int,
    disable_floors: bool,
) -> tuple[int, int]:
    false_positives = 0
    for trial in range(n_trials):
        gen = random.Random(seed_base + trial)
        baseline = [max(0.0001, gen.gauss(BASE_MEAN, BASE_SD)) for _ in range(n_per_group)]
        current = [max(0.0001, gen.gauss(BASE_MEAN, BASE_SD)) for _ in range(n_per_group)]
        result = evaluate_regression(
            baseline,
            current,
            confidence=confidence,
            min_effect_usd=0.0 if disable_floors else DEFAULT_MIN_EFFECT_USD,
            min_effect_pct=0.0 if disable_floors else DEFAULT_MIN_EFFECT_PCT,
            n_boot=n_boot,
            seed=trial,
        )
        if result.status == "regression":
            false_positives += 1
    return false_positives, n_trials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confidence", type=float, required=True)
    parser.add_argument("--n-trials", type=int, default=500)
    parser.add_argument("--n-per-group", type=int, default=MIN_N_DEFAULT)
    parser.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    args = parser.parse_args()

    print(
        f"Measuring FPR at confidence={args.confidence}, n={args.n_per_group}, "
        f"n_boot={args.n_boot}, n_trials={args.n_trials} (real shipped-config floors "
        "AND statistical-only floors-disabled, two independent seed bases each)..."
    )
    t0 = time.time()
    for label, disable_floors in (
        ("STATISTICAL-ONLY (floors disabled)", True),
        ("FULL SHIPPED CONFIG (floors enabled)", False),
    ):
        print(f"\n=== {label} ===")
        for seed_base in (500_000, 777_777):
            fp, n = _measure(
                args.confidence,
                seed_base,
                args.n_trials,
                args.n_per_group,
                args.n_boot,
                disable_floors,
            )
            print(f"  seed_base={seed_base}: {fp}/{n} = {fp / n * 100:.2f}%")
    elapsed = time.time() - t0
    print(f"\nWall-clock: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
