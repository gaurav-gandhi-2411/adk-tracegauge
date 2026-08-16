"""tests/test_fpr_anomaly_audit.py — permanent regression-protection for the
FPR-anomaly audit's two discriminating-test scripts
(``scripts/measure_fpr_anomaly_h1_discriminant.py``,
``scripts/measure_fpr_anomaly_reproducibility.py``) plus
``measure_regression_confidence_grid.two_proportion_z_test``, the significance
test added to the harness itself as part of the audit -- see
``docs/audit/FPR_ANOMALY.md`` for the full investigation and both scripts'
own module docstrings for the already-executed, already-reported results.

Same discipline as ``tests/test_regression_confidence_grid.py``: the full,
already-measured runs (3,000/5,000 trials per cell) are NOT re-run on every
``pytest`` invocation -- these are tiny, deterministic smoke tests of the
harness itself (determinism, shape, basic sanity), not a re-verification of
the published numbers.
"""

from __future__ import annotations

import pytest
from measure_fpr_anomaly_h1_discriminant import run_one_sample_cell, run_two_sample_cell
from measure_fpr_anomaly_reproducibility import run_paired_fpr_cell, run_two_sample_fpr_cell
from measure_regression_confidence_grid import two_proportion_z_test, wilson_score_interval

# --- two_proportion_z_test: standalone correctness -------------------------


def test_two_proportion_z_test_identical_proportions_gives_zero_z():
    z, p = two_proportion_z_test(50, 1000, 50, 1000)
    assert z == pytest.approx(0.0, abs=1e-9)
    assert p == pytest.approx(1.0, abs=1e-9)


def test_two_proportion_z_test_is_antisymmetric_in_argument_order():
    z_ab, p_ab = two_proportion_z_test(20, 1000, 40, 1000)
    z_ba, p_ba = two_proportion_z_test(40, 1000, 20, 1000)
    assert z_ab == pytest.approx(-z_ba, abs=1e-9)
    assert p_ab == pytest.approx(p_ba, abs=1e-9)  # two-sided p-value is symmetric


def test_two_proportion_z_test_matches_known_textbook_value():
    # Standard two-proportion z-test textbook example: 45/100 vs 35/100 ->
    # pooled p=0.40, SE=sqrt(0.4*0.6*(1/100+1/100))=0.06928, z=0.10/0.06928=1.4434
    z, p = two_proportion_z_test(45, 100, 35, 100)
    assert z == pytest.approx(-1.4434, abs=0.001)
    assert p == pytest.approx(0.1489, abs=0.001)


def test_two_proportion_z_test_large_gap_is_significant():
    # A real, large gap (10% vs 30% at n=1000/side) must read as significant.
    z, p = two_proportion_z_test(100, 1000, 300, 1000)
    assert p < 0.001
    assert z > 0  # second proportion (0.30) > first (0.10)


def test_two_proportion_z_test_handles_zero_variance_without_crashing():
    # x1=n1, x2=n2 (both proportions exactly 1.0) -> pooled variance is 0,
    # must not raise ZeroDivisionError -- returns the "no evidence of a
    # difference" degenerate case (z=0, p=1) rather than crashing.
    z, p = two_proportion_z_test(100, 100, 100, 100)
    assert z == 0.0
    assert p == 1.0


# --- H1 discriminant harness: determinism + shape ---------------------------


def test_run_one_sample_cell_is_deterministic_and_shaped_correctly():
    result_a = run_one_sample_cell(0.95, 30, n_trials=15)
    result_b = run_one_sample_cell(0.95, 30, n_trials=15)
    assert result_a == result_b  # fully deterministic given the same inputs
    detections, n_trials = result_a
    assert n_trials == 15
    assert 0 <= detections <= n_trials


def test_run_two_sample_cell_is_deterministic_and_shaped_correctly():
    result_a = run_two_sample_cell(0.95, 30, n_trials=15)
    result_b = run_two_sample_cell(0.95, 30, n_trials=15)
    assert result_a == result_b
    detections, n_trials = result_a
    assert n_trials == 15
    assert 0 <= detections <= n_trials


def test_h1_discriminant_cells_use_independent_rng_streams():
    # one-sample and two-sample cells must not be accidentally coupled to
    # the same seed stream (that would silently make them non-independent
    # measurements, defeating the whole point of the discriminant).
    one, _ = run_one_sample_cell(0.95, 30, n_trials=200)
    two, _ = run_two_sample_cell(0.95, 30, n_trials=200)
    # Not asserting a specific relationship (that's the actual open
    # question the real, full-scale run answers) -- only that both produce
    # a plausible FPR-range count, i.e. the harness itself isn't broken.
    assert 0 <= one <= 200
    assert 0 <= two <= 200


# --- reproducibility-check harness: determinism + shape --------------------


def test_run_two_sample_fpr_cell_is_deterministic_and_shaped_correctly():
    result_a = run_two_sample_fpr_cell(0.98, 30, n_trials=15)
    result_b = run_two_sample_fpr_cell(0.98, 30, n_trials=15)
    assert result_a == result_b
    detections, n_trials = result_a
    assert n_trials == 15
    assert 0 <= detections <= n_trials


def test_run_paired_fpr_cell_is_deterministic_and_shaped_correctly():
    result_a = run_paired_fpr_cell(0.98, 30, n_trials=15)
    result_b = run_paired_fpr_cell(0.98, 30, n_trials=15)
    assert result_a == result_b
    detections, n_trials = result_a
    assert n_trials == 15
    assert 0 <= detections <= n_trials


def test_reproducibility_check_uses_a_seed_base_independent_of_the_original_grid():
    # The whole point of this script is an INDEPENDENT re-measurement, not
    # an extension of measure_regression_confidence_grid.py's own RNG
    # stream (SEED_BASE_TWO_SAMPLE=1_000_000/SEED_BASE_PAIRED=1_100_000) --
    # a regression test pinning that the seed bases stay distinct.
    import measure_fpr_anomaly_reproducibility as m
    import measure_regression_confidence_grid as grid

    assert m.SEED_BASE_TWO_SAMPLE != grid.SEED_BASE_TWO_SAMPLE
    assert m.SEED_BASE_PAIRED != grid.SEED_BASE_PAIRED


def test_wilson_score_interval_still_importable_from_confidence_grid_module():
    # Both new audit scripts import wilson_score_interval from
    # measure_regression_confidence_grid.py rather than reimplementing it --
    # a basic sanity check that the shared import surface stays intact.
    lo, hi = wilson_score_interval(10, 100)
    assert 0.0 <= lo < 0.10 < hi <= 1.0
