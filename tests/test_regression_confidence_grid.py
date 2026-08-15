"""tests/test_regression_confidence_grid.py — Phase 7 U2: permanent
regression-protection for ``scripts/measure_regression_confidence_grid.py``
(the confidence x n x effect grid, both modes, with Wilson score CIs, that
2.1/2.2/2.3's re-decision rests on).

The FULL 18-cell-per-mode grid at the real >=2,000 trials/cell (72,000 total
simulated bootstrap evaluations, ~903s wall-clock) is intentionally NOT run
here on every `pytest tests/` invocation -- see that script's own docstring
for the reproduction command and PLAN.md's Phase 7 U2 entry for the full,
already-measured grid with real Wilson CIs. This file instead smoke-tests
the harness itself (determinism, shape, basic sanity) on a tiny slice, the
same discipline `tests/test_regression_power.py` already uses for
``compute_power_grid``/``compute_paired_power_grid``, plus a standalone unit
test of ``wilson_score_interval`` against known closed-form values.
"""

from __future__ import annotations

import pytest
from measure_regression_confidence_grid import (
    compute_paired_confidence_grid,
    compute_two_sample_confidence_grid,
    wilson_score_interval,
)

# --- wilson_score_interval: standalone correctness -------------------------


def test_wilson_score_interval_matches_known_textbook_values():
    # p_hat=0.5, n=100, 95% CI: a standard textbook example, Wilson interval
    # is documented (Brown/Cai/DasGupta 2001) as approximately [0.404, 0.596]
    # -- noticeably narrower than the naive Wald interval's [0.402, 0.598]
    # at the boundary but converges to it near p_hat=0.5, large n.
    lo, hi = wilson_score_interval(50, 100)
    assert lo == pytest.approx(0.404, abs=0.001)
    assert hi == pytest.approx(0.596, abs=0.001)


def test_wilson_score_interval_stays_within_0_1_bounds_near_extremes():
    # This is exactly the regime the naive normal-approximation interval
    # breaks in (can extend below 0 or above 1) -- Wilson must not.
    lo, hi = wilson_score_interval(0, 2000)  # phat=0.0, e.g. a very low FPR cell
    # Mathematically exactly 0 (the phat*(1-phat) term vanishes, leaving
    # center==margin identically) -- allow floating-point sqrt-of-a-square
    # rounding noise (observed ~1e-19) rather than asserting bit-exact 0.0.
    assert lo < 1e-9
    assert 0.0 < hi < 0.01  # a real, small, non-degenerate upper bound

    lo, hi = wilson_score_interval(2000, 2000)  # phat=1.0, e.g. a saturated-power cell
    # Same floating-point symmetry as the phat=0.0 case above -- mathematically
    # exactly 1, allow the same rounding tolerance.
    assert hi > 1.0 - 1e-9
    assert 0.99 < lo < 1.0


def test_wilson_score_interval_zero_trials_is_maximally_uninformative():
    assert wilson_score_interval(0, 0) == (0.0, 1.0)


def test_wilson_score_interval_widens_as_trial_count_shrinks():
    # Same phat, fewer trials -> a wider (more honest) interval -- the whole
    # point of reporting a CI instead of a bare point estimate.
    lo_many, hi_many = wilson_score_interval(20, 2000)  # phat=0.01, n=2000
    lo_few, hi_few = wilson_score_interval(1, 100)  # phat=0.01, n=100
    assert (hi_many - lo_many) < (hi_few - lo_few)


# --- harness smoke tests: determinism + shape -------------------------------


def test_compute_two_sample_confidence_grid_is_deterministic_and_shaped_correctly():
    grid_a = compute_two_sample_confidence_grid(
        confidence_grid=[0.95, 0.99],
        n_grid=[30],
        effect_pct_grid=[0.0, 10.0],
        n_trials=20,
        n_boot=200,
    )
    grid_b = compute_two_sample_confidence_grid(
        confidence_grid=[0.95, 0.99],
        n_grid=[30],
        effect_pct_grid=[0.0, 10.0],
        n_trials=20,
        n_boot=200,
    )

    assert grid_a == grid_b  # fully deterministic given the same inputs
    assert set(grid_a.keys()) == {
        (0.95, 30, 0.0),
        (0.95, 30, 10.0),
        (0.99, 30, 0.0),
        (0.99, 30, 10.0),
    }
    for detections, n_trials in grid_a.values():
        assert n_trials == 20
        assert 0 <= detections <= n_trials


def test_compute_paired_confidence_grid_is_deterministic_and_shaped_correctly():
    grid_a = compute_paired_confidence_grid(
        confidence_grid=[0.95, 0.99],
        n_grid=[30],
        effect_pct_grid=[0.0, 10.0],
        n_trials=20,
        n_boot=200,
    )
    grid_b = compute_paired_confidence_grid(
        confidence_grid=[0.95, 0.99],
        n_grid=[30],
        effect_pct_grid=[0.0, 10.0],
        n_trials=20,
        n_boot=200,
    )

    assert grid_a == grid_b
    assert set(grid_a.keys()) == {
        (0.95, 30, 0.0),
        (0.95, 30, 10.0),
        (0.99, 30, 0.0),
        (0.99, 30, 10.0),
    }
    for detections, n_trials in grid_a.values():
        assert n_trials == 20
        assert 0 <= detections <= n_trials


def test_confidence_grids_detect_a_large_injected_regression_more_often_than_no_effect():
    # Coarse sanity check independent of the exact measured numbers (which
    # live in reports/confidence_grid_u2.json and PLAN.md's Phase 7 U2
    # entry) -- a 25% true regression at n=50 must be detected dramatically
    # more often than a 0% (no) regression at the same n/confidence, for
    # BOTH modes, or the harness itself (not just the gate) is broken.
    two_sample_grid = compute_two_sample_confidence_grid(
        confidence_grid=[0.98], n_grid=[50], effect_pct_grid=[0.0, 25.0], n_trials=50, n_boot=500
    )
    det_0, n_0 = two_sample_grid[(0.98, 50, 0.0)]
    det_25, n_25 = two_sample_grid[(0.98, 50, 25.0)]
    assert (det_25 / n_25) > (det_0 / n_0) + 0.5

    paired_grid = compute_paired_confidence_grid(
        confidence_grid=[0.98], n_grid=[50], effect_pct_grid=[0.0, 25.0], n_trials=50, n_boot=500
    )
    p_det_0, p_n_0 = paired_grid[(0.98, 50, 0.0)]
    p_det_25, p_n_25 = paired_grid[(0.98, 50, 25.0)]
    assert (p_det_25 / p_n_25) > (p_det_0 / p_n_0) + 0.5


def test_paired_grid_out_detects_two_sample_grid_at_a_realistic_small_n_and_effect():
    # Ties this new grid harness back to U1's 1.5 headline finding (paired
    # is dramatically more powerful at a shared n) -- using the harness's
    # own grid-shaped API, at n=30/10%-effect/confidence=0.98 (the shipped
    # default, MIN_N_DEFAULT itself). Different generators per mode (flat
    # vs case-correlated -- see each compute_* function's own module
    # docstring for why), so this is a coarse sanity check, not an
    # apples-to-apples same-data comparison.
    two_sample_grid = compute_two_sample_confidence_grid(
        confidence_grid=[0.98], n_grid=[30], effect_pct_grid=[10.0], n_trials=100, n_boot=500
    )
    paired_grid = compute_paired_confidence_grid(
        confidence_grid=[0.98], n_grid=[30], effect_pct_grid=[10.0], n_trials=100, n_boot=500
    )
    two_sample_det, two_sample_n = two_sample_grid[(0.98, 30, 10.0)]
    paired_det, paired_n = paired_grid[(0.98, 30, 10.0)]
    assert (paired_det / paired_n) >= (two_sample_det / two_sample_n)
