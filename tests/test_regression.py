"""Unit tests for adk_tracegauge._regression (the bootstrap cost-regression
gate) plus the two synthetic-fixture validations required by Phase 2 W4:

- test_injected_regression_is_detected (4.3a): a known +20% mean-cost
  regression must fire the gate, with the actual measured effect size and
  CI bounds asserted, not merely "the gate fired".
- test_false_positive_rate_under_pure_noise (4.3b): two samples drawn from
  the IDENTICAL distribution (genuinely no regression) run through the gate
  >=200 independent times; the real measured false-positive rate is
  asserted against a documented, non-tautological bound, not merely hand-
  waved as "low". See that test's docstring for the actual measured number
  as of this test's authorship.
"""

from __future__ import annotations

import random
import statistics

import pytest

from adk_tracegauge._regression import (
    ACHIEVED_POWER_TARGET,
    DEFAULT_CONFIDENCE,
    MIN_N_DEFAULT,
    _below_floor_warning,
    _inverse_normal_cdf,
    _normal_cdf,
    _one_sided_alpha,
    _percentile,
    _standard_error_paired,
    _standard_error_two_sample,
    bootstrap_diff_of_means,
    bootstrap_mean_of_paired_deltas,
    evaluate_regression,
    evaluate_regression_paired,
    minimum_detectable_effect_usd,
)

# --- _percentile -------------------------------------------------------


def test_percentile_matches_known_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(values, 0) == 1.0
    assert _percentile(values, 100) == 5.0
    assert _percentile(values, 50) == 3.0


def test_percentile_interpolates_between_points():
    values = [1.0, 2.0, 3.0, 4.0]
    # index = (25/100) * 3 = 0.75 -> between values[0]=1.0 and values[1]=2.0
    assert _percentile(values, 25) == pytest.approx(1.75)


def test_percentile_single_value_returns_it_for_any_q():
    assert _percentile([7.0], 0) == 7.0
    assert _percentile([7.0], 50) == 7.0
    assert _percentile([7.0], 100) == 7.0


def test_percentile_rejects_empty_sequence():
    with pytest.raises(ValueError, match="empty"):
        _percentile([], 50)


# --- bootstrap_diff_of_means --------------------------------------------


def test_bootstrap_ci_centered_near_true_difference_for_well_separated_groups():
    baseline = [1.0] * 50
    current = [2.0] * 50
    ci_lower, ci_upper = bootstrap_diff_of_means(baseline, current, seed=42)
    # No variance in either group -> every resample mean is exactly the
    # group's constant value -> the CI collapses to a point at the true
    # difference (2.0 - 1.0 = 1.0).
    assert ci_lower == pytest.approx(1.0)
    assert ci_upper == pytest.approx(1.0)


def test_bootstrap_ci_is_deterministic_given_a_fixed_seed():
    baseline = [0.01, 0.02, 0.015, 0.018, 0.021] * 10
    current = [0.02, 0.021, 0.019, 0.022, 0.025] * 10
    result_a = bootstrap_diff_of_means(baseline, current, seed=7, n_boot=500)
    result_b = bootstrap_diff_of_means(baseline, current, seed=7, n_boot=500)
    assert result_a == result_b


def test_bootstrap_ci_differs_with_a_different_seed():
    baseline = [0.01, 0.02, 0.015, 0.018, 0.021] * 10
    current = [0.02, 0.021, 0.019, 0.022, 0.025] * 10
    result_a = bootstrap_diff_of_means(baseline, current, seed=1, n_boot=500)
    result_b = bootstrap_diff_of_means(baseline, current, seed=2, n_boot=500)
    assert result_a != result_b


def test_bootstrap_rejects_empty_group():
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_of_means([], [1.0], seed=1)
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_of_means([1.0], [], seed=1)


def test_bootstrap_rejects_out_of_range_confidence():
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_diff_of_means([1.0], [2.0], confidence=1.0)
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_diff_of_means([1.0], [2.0], confidence=0.0)


# --- evaluate_regression: minimum-n refusal ------------------------------


def test_evaluate_regression_refuses_below_min_n():
    baseline = [0.01] * (MIN_N_DEFAULT - 1)
    current = [0.02] * (MIN_N_DEFAULT - 1)

    result = evaluate_regression(baseline, current)

    assert result.status == "insufficient_data"
    assert result.ci_lower is None
    assert result.ci_upper is None
    assert not result.statistically_significant
    assert not result.practically_significant


def test_evaluate_regression_min_n_is_per_group_independently():
    baseline = [0.01] * MIN_N_DEFAULT
    current = [0.02] * (MIN_N_DEFAULT - 1)  # only current is short

    result = evaluate_regression(baseline, current)

    assert result.status == "insufficient_data"


def test_evaluate_regression_min_n_is_configurable():
    baseline = [0.01] * 5
    current = [0.02] * 5

    result = evaluate_regression(baseline, current, min_n=5, min_effect_usd=0.0, min_effect_pct=0.0)

    assert result.status != "insufficient_data"


def test_evaluate_regression_reports_n_ci_and_effect_size_every_run():
    baseline = [0.01] * 40
    current = [0.01] * 40

    result = evaluate_regression(baseline, current)

    # Non-negotiable per the work item: n, CI bounds, and effect size must
    # be present in the result every run, not only on a regression verdict.
    assert result.n_baseline == 40
    assert result.n_current == 40
    assert result.ci_lower is not None
    assert result.ci_upper is not None
    assert result.effect_usd == pytest.approx(0.0)
    report_text = result.report()
    assert "n_baseline=40" in report_text
    assert "CI [" in report_text


# --- evaluate_regression: statistical vs. practical significance --------


def test_no_regression_reported_for_identical_distributions():
    values = [0.01, 0.012, 0.009, 0.011, 0.0105] * 10
    result = evaluate_regression(values, list(values))
    assert result.status == "pass"
    assert not result.statistically_significant


def test_statistically_significant_but_below_practical_floor_does_not_regress():
    # A tiny, consistent per-invocation increase (1e-6 USD) across a huge
    # sample is statistically detectable (near-zero variance -> a tight CI
    # strictly above zero) but must not fail a build on its own.
    baseline = [0.05] * 200
    current = [0.05 + 1e-6] * 200

    result = evaluate_regression(baseline, current, min_effect_usd=0.0001, min_effect_pct=5.0)

    assert result.statistically_significant
    assert not result.practically_significant
    assert result.status == "pass"


def test_clearing_only_the_usd_floor_is_sufficient():
    baseline = [0.05] * 200
    current = [
        0.06
    ] * 200  # +$0.01, well above the $0.0001 floor, but only 20% (below a large pct floor)

    result = evaluate_regression(baseline, current, min_effect_usd=0.0001, min_effect_pct=1000.0)

    assert result.practically_significant
    assert result.status == "regression"


def test_clearing_only_the_pct_floor_is_sufficient():
    baseline = [0.001] * 200
    current = [0.002] * 200  # +100%, well above a 5% floor, but below a huge USD floor

    result = evaluate_regression(baseline, current, min_effect_usd=1000.0, min_effect_pct=5.0)

    assert result.practically_significant
    assert result.status == "regression"


def test_significant_decrease_is_not_a_regression():
    baseline = [0.10] * 200
    current = [0.05] * 200  # cost went DOWN significantly -- not a build failure

    result = evaluate_regression(baseline, current, min_effect_usd=0.0001, min_effect_pct=1.0)

    assert result.effect_usd < 0
    assert not result.statistically_significant  # one-sided: CI lower bound only tests increases
    assert result.status == "pass"


def test_effect_pct_is_none_when_baseline_mean_is_zero():
    baseline = [0.0] * 40
    current = [0.01] * 40
    result = evaluate_regression(baseline, current)
    assert result.effect_pct is None


def test_report_of_insufficient_data_names_the_min_n():
    result = evaluate_regression([0.01] * 5, [0.02] * 5)
    assert "INSUFFICIENT DATA" in result.report()
    assert str(MIN_N_DEFAULT) in result.report()


# --- 4.3(a): synthetic fixture with a KNOWN injected regression ---------


def test_injected_regression_is_detected():
    """A synthetic baseline (mean ~$0.010) and a "current" run drawn from the
    SAME shape but with mean cost inflated exactly 20% (mean ~$0.012) --
    n=80 each, well above the min-n floor.

    Assertions are against the *actual* measured result of this run
    (deterministic given seed=42), not an assumed outcome.

    MEASURED (this exact seeding, fully deterministic): mean_baseline=
    $0.010222, mean_current=$0.011741, effect=+$0.001520 (+14.87%), 95% CI
    [+0.001007, +0.002023] -- CI excludes zero (statistically significant)
    and the effect clears both the default $0.0001/5% practical floors
    (practically significant) -> status="regression", as asserted below.
    """
    rng = random.Random(1234)
    n = 80
    baseline = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(n)]
    current = [max(0.0001, rng.gauss(0.010 * 1.20, 0.0015 * 1.20)) for _ in range(n)]

    result = evaluate_regression(baseline, current, seed=42)

    assert result.status == "regression"
    assert result.statistically_significant
    assert result.practically_significant
    # The measured effect must be a real, materially-sized increase in the
    # neighborhood of the injected +20% (loose bound -- sampling noise on
    # n=80 means it will not be exactly 20%).
    assert result.effect_pct is not None
    assert 8.0 < result.effect_pct < 35.0
    assert result.ci_lower is not None
    assert result.ci_lower > 0.0


# --- 4.3(b): measured false-positive rate over >=200 resamples ----------


def test_false_positive_rate_under_pure_noise():
    """Baseline and "current" are drawn independently from the IDENTICAL
    generator (mean $0.010, sd $0.0015, n=40 each) -- genuinely no
    regression, pure sampling noise. Repeated over 250 independent
    (baseline, current) pairs, each with its own fresh RNG seed for both
    data generation and the bootstrap itself, so this is 250 independent
    trials, not 250 re-reads of one cached result.

    This directly measures the gate's real false-positive rate with
    min_effect_usd=0.0/min_effect_pct=0.0 -- i.e. isolating the STATISTICAL
    test's own false-positive rate (a regression here fires purely on
    ci_lower > 0, with no practical-significance floor able to suppress a
    borderline true statistical false positive). At a one-sided 95% CI
    (alpha/2 = 2.5% on the increase side), the nominal expectation is
    ~2.5% false positives.

    MEASURED (this exact deterministic seeding scheme, n_trials=250):
    5/250 = 2.00% false positives -- in line with the ~2.5% nominal
    one-sided expectation, no evidence of miscalibration. This number is
    reproduced exactly every run (fully deterministic seeding), so if this
    comment and a future run ever disagree, the implementation changed --
    that is itself a signal worth investigating, not just updating this
    comment to match.
    """
    n_trials = 250
    n_per_group = 40
    mean = 0.010
    sd = 0.0015

    false_positives = 0
    for trial in range(n_trials):
        gen = random.Random(90000 + trial)
        baseline = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]
        current = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]

        result = evaluate_regression(
            baseline,
            current,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=2000,
            seed=trial,
        )
        if result.status == "regression":
            false_positives += 1

    fp_rate = false_positives / n_trials

    # Measured at authorship time (n_trials=250, this exact seeding scheme):
    # see the session report for the precise false_positives/n_trials figure
    # actually observed. Bound is generous (3x the nominal one-sided 2.5%
    # expectation) specifically so this test is a real regression check on
    # the checker -- not re-tuned to whatever number happened to come out --
    # while still catching a genuinely broken (e.g. two-sided-when-it-should
    # -be-one-sided, or systematically miscalibrated) implementation.
    assert fp_rate <= 0.075, (
        f"measured false-positive rate {false_positives}/{n_trials} = {fp_rate:.4f} "
        "exceeds the generous upper bound -- investigate before trusting this gate "
        "(see module docstring for the nominal ~2.5% one-sided expectation)"
    )


# --- Phase 3 B4: method field (which comparison a result came from) -----


def test_evaluate_regression_result_method_is_two_sample():
    result = evaluate_regression([0.01] * 40, [0.01] * 40)
    assert result.method == "two_sample"


def test_evaluate_regression_insufficient_data_result_method_is_two_sample():
    result = evaluate_regression([0.01] * 5, [0.01] * 5)
    assert result.method == "two_sample"


def test_report_names_the_method_used():
    result = evaluate_regression([0.01] * 40, [0.01] * 40)
    assert "method=two_sample" in result.report()


# --- bootstrap_mean_of_paired_deltas -------------------------------------


def test_paired_bootstrap_ci_collapses_to_true_mean_for_constant_deltas():
    deltas = [0.5] * 50
    ci_lower, ci_upper = bootstrap_mean_of_paired_deltas(deltas, seed=42)
    assert ci_lower == pytest.approx(0.5)
    assert ci_upper == pytest.approx(0.5)


def test_paired_bootstrap_ci_is_deterministic_given_a_fixed_seed():
    deltas = [0.001, 0.0015, 0.0009, 0.0011, 0.0013] * 10
    result_a = bootstrap_mean_of_paired_deltas(deltas, seed=7, n_boot=500)
    result_b = bootstrap_mean_of_paired_deltas(deltas, seed=7, n_boot=500)
    assert result_a == result_b


def test_paired_bootstrap_rejects_empty_deltas():
    with pytest.raises(ValueError, match="at least one delta"):
        bootstrap_mean_of_paired_deltas([], seed=1)


def test_paired_bootstrap_rejects_out_of_range_confidence():
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_mean_of_paired_deltas([0.1], confidence=1.0)


# --- evaluate_regression_paired: shape/contract --------------------------


def test_evaluate_regression_paired_rejects_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        evaluate_regression_paired([0.01] * 30, [0.01] * 29)


def test_evaluate_regression_paired_result_method_is_paired():
    result = evaluate_regression_paired([0.01] * 30, [0.01] * 30)
    assert result.method == "paired"


def test_evaluate_regression_paired_refuses_below_min_n():
    baseline = [0.01] * (MIN_N_DEFAULT - 1)
    current = [0.02] * (MIN_N_DEFAULT - 1)

    result = evaluate_regression_paired(baseline, current)

    assert result.status == "insufficient_data"
    assert result.ci_lower is None
    assert result.ci_upper is None
    assert result.method == "paired"


def test_evaluate_regression_paired_reports_n_ci_and_effect_size_every_run():
    baseline = [0.01] * 40
    current = [0.01] * 40

    result = evaluate_regression_paired(baseline, current)

    assert result.n_baseline == 40
    assert result.n_current == 40
    assert result.ci_lower is not None
    assert result.ci_upper is not None
    report_text = result.report()
    assert "method=paired" in report_text


def test_evaluate_regression_paired_detects_a_consistent_per_pair_increase():
    # Every pair increases by exactly $0.001 -- zero pair-to-pair variance in
    # the DELTA (even though the underlying baseline/current values below
    # vary a lot pair-to-pair), so the paired bootstrap CI collapses tightly
    # around +0.001 and the gate must fire.
    baseline = [0.01, 0.05, 0.002, 0.03, 0.08] * 10
    current = [b + 0.001 for b in baseline]

    result = evaluate_regression_paired(baseline, current, min_effect_usd=0.0, min_effect_pct=0.0)

    assert result.status == "regression"
    assert result.effect_usd == pytest.approx(0.001)
    assert result.ci_lower is not None
    assert result.ci_lower > 0.0


def test_evaluate_regression_paired_no_regression_for_zero_deltas():
    baseline = [0.01, 0.05, 0.002, 0.03, 0.08] * 10
    current = list(baseline)  # identical -- every delta is exactly 0

    result = evaluate_regression_paired(baseline, current)

    assert result.status == "pass"
    assert not result.statistically_significant


def test_evaluate_regression_paired_significant_decrease_is_not_a_regression():
    baseline = [0.10] * 40
    current = [0.05] * 40  # every pair decreased -- not a build failure

    result = evaluate_regression_paired(
        baseline, current, min_effect_usd=0.0001, min_effect_pct=1.0
    )

    assert result.effect_usd < 0
    assert not result.statistically_significant  # one-sided
    assert result.status == "pass"


def test_evaluate_regression_paired_effect_pct_is_none_when_baseline_mean_is_zero():
    baseline = [0.0] * 40
    current = [0.01] * 40
    result = evaluate_regression_paired(baseline, current)
    assert result.effect_pct is None


def test_evaluate_regression_paired_out_powers_two_sample_when_baseline_and_current_share_case_structure():
    """A minimal, fast (non-simulation) demonstration of WHY pairing helps:
    ten synthetic "eval cases" with wildly different base costs (a
    realistic shape -- see tests/test_regression_power.py's fuller,
    simulation-based version of this same point for the actual measured
    detection-rate comparison). A small, CONSISTENT per-case increase is
    swamped by between-case variance for the unpaired two-sample test but
    not for the paired test, which only ever looks at each case's own
    delta.
    """
    case_levels = [0.001, 0.05, 0.002, 0.08, 0.003, 0.06, 0.0015, 0.09, 0.0025, 0.07] * 4
    baseline = case_levels
    current = [c + 0.0008 for c in case_levels]  # a small, uniform per-case bump

    two_sample = evaluate_regression(baseline, current, min_effect_usd=0.0, min_effect_pct=0.0)
    paired = evaluate_regression_paired(baseline, current, min_effect_usd=0.0, min_effect_pct=0.0)

    assert not two_sample.statistically_significant  # swamped by case-to-case variance
    assert paired.statistically_significant  # the per-case delta is a clean, constant +0.0008
    assert paired.status == "regression"


# --- Phase 4 R4, 4.1: probit/CDF machinery --------------------------------


def test_normal_cdf_matches_known_values():
    assert _normal_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
    assert _normal_cdf(1.959963985) == pytest.approx(0.975, abs=1e-8)
    assert _normal_cdf(-1.959963985) == pytest.approx(0.025, abs=1e-8)


def test_inverse_normal_cdf_matches_known_quantiles():
    # Textbook standard-normal quantiles.
    assert _inverse_normal_cdf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert _inverse_normal_cdf(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert _inverse_normal_cdf(0.95) == pytest.approx(1.644853627, abs=1e-6)
    assert _inverse_normal_cdf(0.80) == pytest.approx(0.841621234, abs=1e-6)
    assert _inverse_normal_cdf(0.025) == pytest.approx(-1.959963985, abs=1e-6)


def test_inverse_normal_cdf_and_normal_cdf_are_exact_inverses():
    # Round-trip: cdf(probit(p)) == p to (near) machine precision, for a
    # spread of p including near the tails where the rational approximation
    # is weakest before the Halley refinement step.
    for p in (0.001, 0.01, 0.025, 0.1, 0.3, 0.5, 0.7, 0.9, 0.975, 0.99, 0.999):
        assert _normal_cdf(_inverse_normal_cdf(p)) == pytest.approx(p, abs=1e-9)


def test_inverse_normal_cdf_rejects_out_of_range():
    with pytest.raises(ValueError, match="0, 1"):
        _inverse_normal_cdf(0.0)
    with pytest.raises(ValueError, match="0, 1"):
        _inverse_normal_cdf(1.0)
    with pytest.raises(ValueError, match="0, 1"):
        _inverse_normal_cdf(-0.1)


def test_one_sided_alpha_is_half_of_the_two_sided_alpha():
    # Load-bearing convention (see module docstring): this module's CI is a
    # two-sided (1-confidence) interval whose LOWER bound is used as a
    # ONE-SIDED test -- so the true one-sided alpha is (1-confidence)/2, not
    # (1-confidence). This matches Phase 2/B4's own measured FPRs (~2.0-2.5%
    # at confidence=0.95), not 5%.
    assert _one_sided_alpha(0.95) == pytest.approx(0.025)
    assert _one_sided_alpha(0.90) == pytest.approx(0.05)


# --- Phase 4 R4, 4.1: standard-error helpers -------------------------------


def test_standard_error_two_sample_returns_none_below_two_samples():
    assert _standard_error_two_sample([0.01], [0.01, 0.02]) is None
    assert _standard_error_two_sample([0.01, 0.02], [0.01]) is None
    assert _standard_error_two_sample([], []) is None


def test_standard_error_two_sample_matches_closed_form():
    baseline = [0.008, 0.010, 0.012, 0.009, 0.011]
    current = [0.011, 0.013, 0.010, 0.012, 0.014]
    expected = (
        statistics.variance(baseline) / len(baseline) + statistics.variance(current) / len(current)
    ) ** 0.5
    assert _standard_error_two_sample(baseline, current) == pytest.approx(expected)


def test_standard_error_paired_returns_none_below_two_deltas():
    assert _standard_error_paired([0.001]) is None
    assert _standard_error_paired([]) is None


def test_standard_error_paired_matches_closed_form():
    deltas = [0.001, 0.0015, 0.0009, 0.0011, 0.0013]
    expected = statistics.stdev(deltas) / (len(deltas) ** 0.5)
    assert _standard_error_paired(deltas) == pytest.approx(expected)


# --- Phase 4 R4, 4.1: minimum_detectable_effect_usd ------------------------


def test_minimum_detectable_effect_usd_is_none_without_a_standard_error():
    assert minimum_detectable_effect_usd(None, confidence=0.95) is None


def test_minimum_detectable_effect_usd_matches_hand_computed_formula():
    # MDE = (z_{1 - alpha} + z_{power}) * SE, alpha = (1-confidence)/2 --
    # see module docstring's "Achieved statistical power" note.
    se = 0.0005
    confidence = 0.95
    power = 0.80
    z_alpha = _inverse_normal_cdf(1.0 - (1.0 - confidence) / 2.0)
    z_power = _inverse_normal_cdf(power)
    expected = (z_alpha + z_power) * se
    assert minimum_detectable_effect_usd(se, confidence=confidence, power=power) == pytest.approx(
        expected
    )


def test_minimum_detectable_effect_usd_scales_linearly_with_standard_error():
    small = minimum_detectable_effect_usd(0.0001, confidence=0.95)
    large = minimum_detectable_effect_usd(0.0002, confidence=0.95)
    assert small is not None and large is not None
    assert large == pytest.approx(small * 2.0)


def test_minimum_detectable_effect_usd_default_power_is_achieved_power_target():
    se = 0.0004
    default_call = minimum_detectable_effect_usd(se, confidence=0.95)
    explicit_call = minimum_detectable_effect_usd(se, confidence=0.95, power=ACHIEVED_POWER_TARGET)
    assert default_call == pytest.approx(explicit_call)


def test_achieved_power_approximation_matches_measured_grid_within_tolerance():
    """Validates the normal-approximation power formula (the same one
    ``minimum_detectable_effect_usd`` inverts) against B4/R2's own
    empirically-MEASURED power grid (``scripts/measure_regression_power.py``,
    generator: mean=$0.010, sd=$0.0015, sd scaling ``sd*(1+effect)`` under a
    true effect -- see that script's own module docstring). This is the
    reproducible, asserted version of the accuracy table documented in
    ``_regression.py``'s "Achieved statistical power" section.

    The predicted-power formula here is the textbook inverse of the MDE
    formula: ``power = Phi(effect/SE - z_alpha)``, using the SAME closed-form
    SE (``sqrt(var_baseline/n + var_current/n)``) the measured grid's
    generator implies, and the SAME one-sided alpha convention
    (``_one_sided_alpha``) as ``minimum_detectable_effect_usd`` itself.

    Tolerance is 0.10 (10 percentage points) -- generous, but not vacuous:
    it is set ABOVE the worst observed deviation (0.079, at n=25/10%) so a
    real regression in either the formula or the underlying constants would
    still be caught, while accepting the genuine, honestly-characterized gap
    between a closed-form normal approximation and an actual bootstrap
    simulation (worst at small/moderate n, near-exact at n>=100 -- see the
    per-cell diffs asserted below).
    """
    base_mean = 0.010
    base_sd = 0.0015
    confidence = 0.95

    def predicted_power(n: int, effect_pct: float) -> float:
        effect = effect_pct / 100.0
        effect_usd = base_mean * effect
        sd_current = base_sd * (1.0 + effect)
        se = ((base_sd**2) / n + (sd_current**2) / n) ** 0.5
        z_alpha = _inverse_normal_cdf(1.0 - _one_sided_alpha(confidence))
        z = effect_usd / se - z_alpha
        return _normal_cdf(z)

    # (n, effect_pct): measured detection rate, from PLAN.md's Phase 3 B4
    # entry / scripts/measure_regression_power.py's own MEASURED GRID.
    measured = {
        (10, 10.0): 0.315,
        (25, 5.0): 0.270,
        (25, 10.0): 0.690,
        (50, 5.0): 0.385,
        (50, 10.0): 0.870,
        (100, 10.0): 0.995,
        (250, 10.0): 1.000,
    }
    tolerance = 0.10

    for (n, effect_pct), measured_rate in measured.items():
        predicted_rate = predicted_power(n, effect_pct)
        diff = abs(predicted_rate - measured_rate)
        assert diff <= tolerance, (
            f"n={n} effect={effect_pct}%: predicted={predicted_rate:.3f} "
            f"measured={measured_rate:.3f} diff={diff:.3f} exceeds tolerance={tolerance} -- "
            "the normal approximation has drifted from the empirically-measured grid; "
            "re-derive the formula or its documented accuracy characterization"
        )

    # The approximation must also be MUCH more accurate at large n (where
    # both the CLT and bootstrap-consistency arguments it leans on are at
    # their strongest) than at the noisiest small-n cell -- a coarse sanity
    # check that the accuracy pattern itself (not just each individual diff)
    # matches what the module docstring claims.
    assert abs(predicted_power(250, 10.0) - measured[(250, 10.0)]) < abs(
        predicted_power(25, 10.0) - measured[(25, 10.0)]
    )


# --- Phase 4 R4, 4.2: below-floor warning ----------------------------------


def test_below_floor_warning_none_when_mde_is_none():
    assert (
        _below_floor_warning(
            min_detectable_effect_usd=None,
            min_effect_usd=0.0001,
            min_effect_pct=5.0,
            mean_baseline=0.01,
        )
        is None
    )


def test_below_floor_warning_fires_when_effective_floor_is_smaller_than_mde():
    warning = _below_floor_warning(
        min_detectable_effect_usd=0.001,  # can only reliably detect >= $0.001
        min_effect_usd=0.0001,  # but configured to "care about" $0.0001
        min_effect_pct=1000.0,  # pct floor disabled in practice (huge)
        mean_baseline=0.01,
    )
    assert warning is not None
    assert "0.001000" in warning  # the MDE value
    assert "0.000100" in warning  # the effective (usd) floor value


def test_below_floor_warning_none_when_floor_already_at_or_above_mde():
    warning = _below_floor_warning(
        min_detectable_effect_usd=0.0001,
        min_effect_usd=0.001,  # configured floor is ABOVE the detectable floor
        min_effect_pct=1000.0,
        mean_baseline=0.01,
    )
    assert warning is None


def test_below_floor_warning_uses_the_easier_to_clear_of_usd_or_pct_floor():
    # pct floor (5% of $0.01 = $0.0005) is smaller/easier-to-clear than the
    # usd floor ($10) -- the OR semantics mean the EFFECTIVE floor is the
    # pct-derived one, and it's still below the $0.001 MDE, so it must warn.
    warning = _below_floor_warning(
        min_detectable_effect_usd=0.001,
        min_effect_usd=10.0,
        min_effect_pct=5.0,
        mean_baseline=0.01,
    )
    assert warning is not None
    assert "0.000500" in warning  # effective floor: 5% of $0.01


def test_below_floor_warning_handles_zero_mean_baseline():
    # mean_baseline == 0 -> pct floor is undefined (treated as infinity, not
    # a crash) -- only the usd floor is compared.
    warning = _below_floor_warning(
        min_detectable_effect_usd=0.001,
        min_effect_usd=0.01,  # above the MDE -> no warning
        min_effect_pct=5.0,
        mean_baseline=0.0,
    )
    assert warning is None


# --- Phase 4 R4: integration -- evaluate_regression/_paired result fields -


def test_evaluate_regression_populates_achieved_power_fields():
    rng = random.Random(55)
    baseline = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(40)]
    current = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(40)]

    result = evaluate_regression(baseline, current)

    assert result.min_detectable_effect_usd is not None
    assert result.min_detectable_effect_usd > 0.0
    assert result.min_detectable_effect_pct is not None
    assert result.power_target == ACHIEVED_POWER_TARGET


def test_evaluate_regression_achieved_power_fields_populated_even_when_insufficient_data():
    # 4.1's own requirement: printed/computed every run, not only once n
    # clears min_n -- as long as there are >=2 samples to estimate variance.
    baseline = [0.008, 0.009, 0.0095, 0.0105, 0.011]
    current = [0.009, 0.0095, 0.010, 0.0105, 0.011]

    result = evaluate_regression(baseline, current)  # n=5 < default min_n=30

    assert result.status == "insufficient_data"
    assert result.min_detectable_effect_usd is not None


def test_evaluate_regression_achieved_power_fields_are_none_below_two_samples():
    result = evaluate_regression([0.01], [0.01, 0.02], min_n=1)
    assert result.min_detectable_effect_usd is None
    assert result.min_detectable_effect_pct is None
    assert result.power_warning is None
    # The report()'s "cannot be estimated" branch (_power_line) is only
    # reachable when min_detectable_effect_usd is None -- covered here.
    assert "cannot be estimated" in result.report()


def test_evaluate_regression_report_always_includes_achieved_power_line():
    for baseline, current in (
        ([0.01] * 40, [0.01] * 40),  # pass
        ([0.01] * 40, [0.02] * 40),  # regression
        ([0.01] * 5, [0.01] * 5),  # insufficient_data
    ):
        report_text = evaluate_regression(baseline, current).report()
        assert "achieved power" in report_text


def test_evaluate_regression_report_warns_when_default_floor_is_below_the_detectable_floor():
    # Reproduces the real example documented in README/examples/03: high
    # relative cost variance at a moderate n means the default 5%/$0.0001
    # floors are smaller than what the test can actually reliably resolve.
    rng = random.Random(1234)
    baseline = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(40)]
    current = [max(0.0001, rng.gauss(0.010 * 1.20, 0.0015 * 1.20)) for _ in range(40)]

    result = evaluate_regression(baseline, current, seed=42)

    assert result.power_warning is not None
    assert "BELOW" in result.power_warning
    assert "WARNING:" in result.report()


def test_evaluate_regression_report_does_not_warn_when_floor_is_generous():
    # A very high configured floor (unlikely to be "smaller" than the
    # achievable detection floor) must not trigger the warning.
    baseline = [0.01] * 40
    current = [0.02] * 40

    result = evaluate_regression(baseline, current, min_effect_usd=1.0, min_effect_pct=1_000_000.0)

    assert result.power_warning is None
    assert "WARNING:" not in result.report()


# --- Phase 9 Q2: underpowered_pass / EXIT_UNDERPOWERED_PASS -----------------


def test_underpowered_pass_true_for_two_sample_pass_with_real_variance_and_tiny_floor():
    # Real variance (not a degenerate [x]*n fixture), NO injected effect
    # (current == baseline), and a floor small enough that even near-zero
    # noise clears it -- power_warning fires AND status stays "pass".
    rng = random.Random(99)
    baseline = [max(0.0001, rng.gauss(0.010, 0.006)) for _ in range(40)]
    current = list(baseline)

    result = evaluate_regression(
        baseline, current, min_effect_usd=1e-9, min_effect_pct=1e-9, seed=42
    )

    assert result.status == "pass"
    assert result.method == "two_sample"
    assert result.power_warning is not None
    assert result.underpowered_pass is True
    assert "UNDERPOWERED PASS" in result.report()
    assert "exit code 4" in result.report()


def test_underpowered_pass_false_when_variance_is_low_enough_for_the_default_floor():
    # Very low CV (~0.5%) -- clears BOTH default floors (the tighter USD
    # one, $0.0001 absolute, and the 5% one), so no warning at all.
    rng = random.Random(99)
    baseline = [max(0.0001, rng.gauss(0.010, 0.00005)) for _ in range(40)]
    current = list(baseline)

    result = evaluate_regression(baseline, current, seed=42)  # default floors

    assert result.status == "pass"
    assert result.power_warning is None
    assert result.underpowered_pass is False
    assert "UNDERPOWERED PASS" not in result.report()


def test_underpowered_pass_false_for_regression_status_even_with_power_warning():
    # A REAL, large injected effect can be both a "regression" verdict AND
    # still trip power_warning (the floor question is orthogonal to whether
    # THIS run's own effect cleared it) -- underpowered_pass must not fire
    # on a non-"pass" status; the regression status is the more important
    # signal and must not be relabeled.
    rng = random.Random(1234)
    baseline = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(40)]
    current = [max(0.0001, rng.gauss(0.010 * 1.20, 0.0015 * 1.20)) for _ in range(40)]

    result = evaluate_regression(baseline, current, seed=42)

    assert result.status == "regression"
    assert result.power_warning is not None
    assert result.underpowered_pass is False


def test_underpowered_pass_true_for_paired_mode_pass_with_real_variance_and_tiny_floor():
    # AP1: mode-agnostic as of this fix -- was FALSE under Q2's original
    # two-sample-only restriction (see git history for the prior version
    # of this test, inverted here). Real hosted-model measurement found
    # the shipped PAIRED default hits exactly this case at n=30 (37.85%
    # power for a 10% regression at real measured within-case CV=0.1307 --
    # docs/audit/AD2_REAL_CV_MEASUREMENT.md) -- the mechanism
    # (power_warning) already fired identically for paired mode before
    # this fix; only the exit-code escalation was being withheld.
    # Real per-pair DELTA variance (zero-mean noise added on top of
    # baseline, not current==baseline exactly -- a degenerate all-zero
    # delta set would give standard_error=0 and never trip the warning).
    rng = random.Random(7)
    baseline = [max(0.0001, rng.gauss(0.010, 0.006)) for _ in range(40)]
    noise = [rng.gauss(0.0, 0.004) for _ in range(40)]
    current = [b + eps for b, eps in zip(baseline, noise, strict=True)]

    result = evaluate_regression_paired(
        baseline, current, min_effect_usd=1e-9, min_effect_pct=1e-9, seed=42
    )

    assert result.status == "pass"
    assert result.method == "paired"
    assert result.power_warning is not None
    assert result.underpowered_pass is True
    assert "UNDERPOWERED PASS" in result.report()
    assert "exit code 4" in result.report()
    assert "paired mode's power" in result.report()


def test_underpowered_pass_false_for_paired_mode_when_variance_is_low_enough_for_the_default_floor():
    # Mirrors test_underpowered_pass_false_when_variance_is_low_enough_
    # for_the_default_floor's two-sample case -- the negative branch for
    # paired mode, so both modes have both branches covered (AP1.4).
    rng = random.Random(99)
    baseline = [max(0.0001, rng.gauss(0.010, 0.00005)) for _ in range(40)]
    noise = [rng.gauss(0.0, 0.00002) for _ in range(40)]
    current = [b + eps for b, eps in zip(baseline, noise, strict=True)]

    result = evaluate_regression_paired(baseline, current, seed=42)  # default floors

    assert result.status == "pass"
    assert result.method == "paired"
    assert result.power_warning is None
    assert result.underpowered_pass is False
    assert "UNDERPOWERED PASS" not in result.report()


def test_underpowered_pass_false_for_paired_regression_status_even_with_power_warning():
    # Mirrors test_underpowered_pass_false_for_regression_status_even_
    # with_power_warning's two-sample case -- a real, large injected
    # per-pair effect can be both "regression" AND still trip
    # power_warning; underpowered_pass must not fire on a non-"pass"
    # status in paired mode either.
    rng = random.Random(1234)
    baseline = [max(0.0001, rng.gauss(0.010, 0.0015)) for _ in range(40)]
    current = [max(0.0001, b * 1.20 + rng.gauss(0.0, 0.0003)) for b in baseline]

    result = evaluate_regression_paired(baseline, current, seed=42)

    assert result.status == "regression"
    assert result.method == "paired"
    assert result.power_warning is not None
    assert result.underpowered_pass is False


def test_evaluate_regression_paired_populates_achieved_power_fields():
    baseline = [0.01, 0.05, 0.002, 0.03, 0.08] * 10
    current = [b + 0.001 for b in baseline]

    result = evaluate_regression_paired(baseline, current)

    assert result.min_detectable_effect_usd is not None
    assert result.min_detectable_effect_usd > 0.0
    assert result.power_target == ACHIEVED_POWER_TARGET


def test_evaluate_regression_paired_report_always_includes_achieved_power_line():
    for baseline, current in (
        ([0.01] * 40, [0.01] * 40),  # pass
        ([0.01] * 40, [0.02] * 40),  # regression
        ([0.01] * 5, [0.01] * 5),  # insufficient_data
    ):
        report_text = evaluate_regression_paired(baseline, current).report()
        assert "achieved power" in report_text


# --- Phase 5 S4, 4.4: shipped default confidence level ---------------------


def test_default_confidence_is_098_per_s4_alpha_grid_decision():
    # Hardcoded-default test, per this work item's own instruction to update
    # (not just add to) any test asserting a shipped default value. Changed
    # from 0.95 -- see DEFAULT_CONFIDENCE's own docstring in _regression.py
    # for the full 90-cell alpha x n x effect grid this value was chosen
    # from.
    assert pytest.approx(0.98) == DEFAULT_CONFIDENCE


def test_default_confidence_corresponds_to_one_sided_alpha_of_one_percent():
    # The whole point of S4's alpha-tuning: DEFAULT_CONFIDENCE=0.98 must
    # actually mean one-sided alpha=0.01, not some other value -- verifies
    # the alpha<->confidence mapping this work item relied on throughout.
    assert _one_sided_alpha(DEFAULT_CONFIDENCE) == pytest.approx(0.01)


# --- Phase 5 S4 (supersedes Phase 4 R4, 4.4): false-positive rate at min_n,
# SHIPPED default config -----------------------------------------------------


def test_false_positive_rate_at_min_n_with_real_default_config():
    """The S4 4.4 measurement, as a fast permanent regression test.

    UNLIKE ``test_false_positive_rate_under_pure_noise`` above (which
    deliberately bypasses the practical-significance floor to isolate pure
    statistical detection, matching B4's own grid methodology), this uses
    the REAL SHIPPED DEFAULT configuration exactly as a user running
    ``adk-tracegauge check`` with no overrides would get it: real
    ``min_n=30`` (``MIN_N_DEFAULT``), real ``confidence=0.98``
    (``DEFAULT_CONFIDENCE`` -- Phase 5 S4 CHANGED this from 0.95, see that
    constant's own docstring for the full alpha x n x effect grid and
    rationale), real ``min_effect_usd=0.0001``/``min_effect_pct=5.0`` floors
    (NOT disabled), real per-invocation-cost generator shape.

    This fast version uses a reduced ``n_boot=2000`` (vs. the real
    default 10,000) purely for test-suite speed -- see the AUTHORITATIVE
    measurement below, which uses the real ``n_boot=10,000`` default and is
    the number that actually appears in the README.

    AUTHORITATIVE MEASUREMENT (this exact generator/config, real
    ``n_boot=10,000``, 500 independent trials, seed base 500_000):
    **13/500 = 2.60%** false positives. Independent adversarial re-check
    (different seed base 777_777, 500 trials): **10/500 = 2.00%**. Combined
    23/1000 = 2.3% -- both runs agree closely, not a seed artifact. This is
    a >45% reduction from the OLD default's real, shipped-config FPR at the
    same n/generator (confidence=0.95: 23/500=4.60% + 21/500=4.20%,
    combined 44/1000=4.4% -- reproduced exactly, real n_boot=10,000, as part
    of this same S4 measurement pass, confirming Phase 4 R4.4's figure was
    not a fluke). See ``DEFAULT_CONFIDENCE``'s own docstring in
    ``_regression.py`` for why confidence=0.98 (not 0.99, which measures
    even lower FPR but costs too much power at n=50 for a 10% effect) was
    chosen.

    **Practical floor's own contribution, measured separately (S4 4.5)**:
    at this exact n/confidence/generator, the STATISTICAL-ONLY FPR (floors
    disabled, isolating the bootstrap test alone) is IDENTICAL to the FULL
    SHIPPED CONFIG FPR above (13/500 and 10/500, both branches, both seed
    bases) -- the default practical floor contributes ZERO additional
    false-positive suppression at this n/variance combination, for the same
    reason R4.4 already found at the old default: the 5%-relative floor
    sits too close to zero (a few sampling standard errors) at `n=30`'s
    variance level to filter out any of the statistically-significant noise
    that slips through. The floor is still a real, independent, correctly-
    AND'd gate (see ``evaluate_regression``'s own logic) -- it simply isn't
    the thing doing the work of suppressing false alarms at THIS particular
    n/variance; see `_below_floor_warning` and the module's "Achieved
    statistical power" section for the mechanism (a floor below the
    achievable-detection floor cannot meaningfully filter anything).

    FAST VERSION (this test, n_boot=2000, 250 trials, seed base 910_000,
    always run): 5/250 = 2.00% -- consistent with (well within sampling
    noise of) the authoritative 500-trial/n_boot=10,000 figure above; the
    bound asserted below is generous specifically so this stays a real
    regression check on the checker without being re-tuned to the exact
    number that happened to come out.
    """
    n_trials = 250
    n_per_group = MIN_N_DEFAULT
    mean = 0.010
    sd = 0.0015

    false_positives = 0
    for trial in range(n_trials):
        gen = random.Random(910_000 + trial)
        baseline = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]
        current = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]

        # Every argument left at its real shipped default EXCEPT n_boot
        # (reduced for test-suite speed only -- see docstring).
        result = evaluate_regression(baseline, current, seed=trial, n_boot=2000)
        if result.status == "regression":
            false_positives += 1

    fp_rate = false_positives / n_trials

    assert fp_rate <= 0.12, (
        f"measured false-positive rate {false_positives}/{n_trials} = {fp_rate:.4f} at "
        f"n={n_per_group} (min_n) with the real shipped default configuration exceeds the "
        "generous upper bound -- investigate before trusting this gate at its default settings "
        "(see this test's docstring for the authoritative 500-trial/n_boot=10,000 measurement)"
    )


def test_practical_floor_contributes_no_extra_fpr_suppression_at_shipped_defaults():
    """S4 4.5: confirms, as a permanent regression test (not just a
    one-off measurement in the session report), that the STATISTICAL-ONLY
    false-positive rate (floors disabled) and the FULL SHIPPED-CONFIG
    false-positive rate (real min_effect_usd/min_effect_pct floors) are
    IDENTICAL at min_n/DEFAULT_CONFIDENCE -- the practical-significance
    floor is a real, independently-AND'd gate (see
    ``evaluate_regression``'s own ``is_regression = statistically_significant
    and practically_significant`` -- unchanged by this work item, confirmed
    by direct re-read), but at THIS n/variance combination it happens to
    contribute ZERO additional suppression of noise-driven false positives,
    because the 5%-relative floor sits too close to zero at n=30's sampling
    variance to filter anything the statistical test itself didn't already
    let through. See ``test_false_positive_rate_at_min_n_with_real_default_config``'s
    docstring for the full 500-trial/n_boot=10,000 measurement this fast
    version reproduces at smaller scale (n_boot=2000, 150 trials).
    """
    n_trials = 150
    n_per_group = MIN_N_DEFAULT
    mean = 0.010
    sd = 0.0015

    fp_statistical_only = 0
    fp_full_config = 0
    for trial in range(n_trials):
        gen = random.Random(920_000 + trial)
        baseline = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]
        current = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]

        stat_only = evaluate_regression(
            baseline, current, min_effect_usd=0.0, min_effect_pct=0.0, seed=trial, n_boot=2000
        )
        full_config = evaluate_regression(baseline, current, seed=trial, n_boot=2000)
        if stat_only.status == "regression":
            fp_statistical_only += 1
        if full_config.status == "regression":
            fp_full_config += 1

    assert fp_statistical_only == fp_full_config, (
        f"statistical-only FPR ({fp_statistical_only}/{n_trials}) differs from full-shipped-"
        f"config FPR ({fp_full_config}/{n_trials}) at n={n_per_group}/DEFAULT_CONFIDENCE -- "
        "the practical floor's own contribution has changed from the S4 4.5 measurement "
        "(previously: zero additional suppression); investigate before trusting the README's "
        "stated 'floor contributes no extra suppression here' claim"
    )


# --- Phase 6 T4: min_n=30 decision re-validated at confidence=0.98 ---------


def test_min_n_default_kept_at_30_not_raised():
    """Phase 6 T4: re-examined whether raising MIN_N_DEFAULT to clear 80%
    power for a 10% effect at the NEW confidence=0.98 default (Phase 5 S4)
    would be worthwhile -- see MIN_N_DEFAULT's own docstring, "Phase 6 T4
    re-validation" section, for the fresh n in {30,35,40,45,50} measurement
    this decision rests on. DECISION: kept at 30 (fresh measurement showed
    even n=50 is only marginally/inconsistently above 80%, not a robust
    fix). Locked-value regression test -- if MIN_N_DEFAULT ever changes,
    every other place documenting "min_n=30" (README, CHANGELOG, this
    module's own docstrings) must be re-audited together, not just this
    constant.
    """
    assert MIN_N_DEFAULT == 30


def test_power_at_min_n_under_shipped_confidence_remains_below_80pct_target():
    """Phase 6 T4: fast, permanent version of the fresh confidence=0.98
    power re-measurement documented in MIN_N_DEFAULT's own docstring.

    Statistical-only (min_n/floors disabled, matching
    scripts/measure_regression_alpha_grid.py's methodology exactly), n=30
    (MIN_N_DEFAULT), true 10% cost regression, confidence=DEFAULT_CONFIDENCE
    (0.98). AUTHORITATIVE measurement (this exact config, 500 trials,
    n_boot=1000, two independent seed bases): 57.2% and 56.6% -- both well
    below the 80% ACHIEVED_POWER_TARGET, confirming min_n=30 genuinely does
    NOT reach reliable detection for a 10% effect at the new default
    either, which is exactly why 4.1/4.2's runtime achieved-power reporting
    (not a raised min_n) is this package's actual answer to that gap.
    Locked in as a regression test so a future bootstrap-methodology change
    that silently shifts this number doesn't go unnoticed.

    FAST VERSION (this test): reduced trials/n_boot for suite speed; the
    asserted upper bound is generous (70%, well above the ~57% measured) so
    this stays a real check on the underlying behavior without being
    re-tuned to the exact number that happened to come out.
    """
    n_trials = 150
    n_boot = 500
    n_per_group = MIN_N_DEFAULT
    mean = 0.010
    sd = 0.0015
    effect = 0.10

    detections = 0
    for trial in range(n_trials):
        gen = random.Random(940_000 + trial)
        baseline = [max(0.0001, gen.gauss(mean, sd)) for _ in range(n_per_group)]
        current = [
            max(0.0001, gen.gauss(mean * (1 + effect), sd * (1 + effect)))
            for _ in range(n_per_group)
        ]
        result = evaluate_regression(
            baseline,
            current,
            confidence=DEFAULT_CONFIDENCE,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=n_boot,
            seed=trial,
        )
        if result.status == "regression":
            detections += 1

    detection_rate = detections / n_trials
    assert detection_rate < 0.70, (
        f"measured detection rate {detections}/{n_trials} = {detection_rate:.4f} at n="
        f"{n_per_group} (min_n) for a true 10% regression, confidence=DEFAULT_CONFIDENCE, is "
        "surprisingly high for this generator/config -- if the gate's power at min_n has "
        "genuinely improved, the min_n=30-vs-raise decision (see MIN_N_DEFAULT's own docstring, "
        "'Phase 6 T4 re-validation') should be revisited with fresh full-scale measurements, "
        "not silently assumed still correct"
    )
