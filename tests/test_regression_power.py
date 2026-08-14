"""tests/test_regression_power.py — Phase 3 B4: permanent regression-protection
for the power-analysis harness itself, plus the paired-vs-two-sample
detection-rate comparison (4.3) that justifies `evaluate_regression_paired`.

The FULL 5x5 power grid (`scripts/measure_regression_power.py`, 5,000 total
simulated `check` calls, ~2 minutes wall-clock) is intentionally NOT run
here on every `pytest tests/` invocation -- see that script's own docstring
for the reproduction command. This file instead:

1. Smoke-tests `compute_power_grid` on a tiny slice (fast, always run) so a
   future refactor that silently breaks the harness's API/determinism is
   caught by the normal test suite, not only by someone remembering to run
   the slow script by hand.
2. Runs the real, permanent 4.3 measurement: at n=25 (a realistic ADK
   eval-set size -- see the B4 session report for the reasoning) with a
   true injected regression, does `evaluate_regression_paired` meaningfully
   out-detect the original `evaluate_regression` when a real per-eval-case
   pairing key exists? This directly answers the work item's own question
   ("does the fix help?") with actual numbers, not a prototype run once and
   discarded.
"""

from __future__ import annotations

import random

from measure_regression_power import compute_power_grid

from adk_tracegauge._regression import evaluate_regression, evaluate_regression_paired

# --- smoke test: the power-grid harness itself does not bitrot -----------


def test_compute_power_grid_is_deterministic_and_shaped_correctly():
    grid_a = compute_power_grid(n_grid=[25], effect_pct_grid=[0.0, 10.0], n_trials=20, n_boot=200)
    grid_b = compute_power_grid(n_grid=[25], effect_pct_grid=[0.0, 10.0], n_trials=20, n_boot=200)

    assert grid_a == grid_b  # fully deterministic given the same inputs
    assert set(grid_a.keys()) == {(25, 0.0), (25, 10.0)}
    for detection_rate in grid_a.values():
        assert 0.0 <= detection_rate <= 1.0


def test_compute_power_grid_detects_a_large_injected_regression_more_often_than_no_effect():
    # A coarse sanity check independent of the exact measured numbers (which
    # live in scripts/measure_regression_power.py's own docstring and the
    # B4 session report): a 50% true regression at n=100 must be detected
    # dramatically more often than a 0% (no) regression at the same n --
    # if this ever fails, the harness itself (not just the gate) is broken.
    grid = compute_power_grid(n_grid=[100], effect_pct_grid=[0.0, 50.0], n_trials=50, n_boot=500)
    assert grid[(100, 50.0)] > grid[(100, 0.0)] + 0.5


# --- 4.3: paired vs. two-sample, real measured detection rates -----------

_N = 25
_N_TRIALS = 200
_N_BOOT = 1_000
_BASE_MEAN = 0.010
_WITHIN_CASE_SD = 0.0008
_CASE_LEVEL_LOW = 0.004
_CASE_LEVEL_HIGH = 0.024
_CASE_CORRELATED_SEED_BASE = 800_000


def _generate_case_correlated_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    """A DELIBERATELY DIFFERENT generator from scripts/measure_regression_power.py's
    flat (Phase-2-matching) one -- see this function's own justification
    below, required per the B4 work item's own instruction to justify any
    generator deviation explicitly.

    Each of ``n`` synthetic eval CASES gets its own fixed per-case cost
    level ``d_i ~ Uniform(0.004, 0.024)`` -- representing real heterogeneity
    across eval cases (different prompts/tool-call trajectories cost
    different amounts, often by several x, independent of any regression).
    A baseline run's cost for case i is ``max(0.0001, Gauss(d_i,
    within_case_sd))``; a "current" run injects an ADDITIVE, per-case-UNIFORM
    dollar bump (``effect_usd = BASE_MEAN * effect_pct/100`` -- e.g. +$0.001
    at effect_pct=10, the same absolute injected effect size as Phase 2's
    fixtures use relative to BASE_MEAN, but applied as a flat add rather
    than a multiplicative scale): ``max(0.0001, Gauss(d_i + effect_usd,
    within_case_sd))``.

    This additive-per-case model is the realistic shape for exactly the
    kind of regression a pairing key is meant to catch -- e.g. a bigger
    system prompt or an added tool-schema definition costs roughly the same
    EXTRA dollars on every case, regardless of that case's own base cost --
    and it is also the shape that makes pairing's mechanism (subtracting
    away the shared d_i term) most legible. A multiplicative case-correlated
    regression would still benefit from pairing (d_i's contribution to
    variance is still substantially reduced, just not fully cancelled), but
    by a smaller margin than measured here -- this is flagged explicitly,
    not left implicit, so the measured numbers below are not read as a
    universal multiplier.

    Reusing scripts/measure_regression_power.py's own FLAT generator (no
    case structure at all) here instead would prove nothing: with no
    between-case variance to remove, pairing and two-sample are
    approximately equivalent BY CONSTRUCTION (verified directly: at this
    same n=25/10%-effect cell under the flat generator, two_sample=0.665
    and paired=0.675 -- statistically indistinguishable, exactly as
    expected when there is no case-level structure to exploit). That
    control measurement is what justifies using a different, case-structured
    generator here rather than reusing the flat one uncritically.
    """
    effect_usd = _BASE_MEAN * (effect_pct / 100.0)
    case_levels = [rng.uniform(_CASE_LEVEL_LOW, _CASE_LEVEL_HIGH) for _ in range(n)]
    baseline = [max(0.0001, rng.gauss(d, _WITHIN_CASE_SD)) for d in case_levels]
    current = [max(0.0001, rng.gauss(d + effect_usd, _WITHIN_CASE_SD)) for d in case_levels]
    return baseline, current


def _measure_paired_vs_two_sample(effect_pct: float) -> tuple[float, float]:
    """Returns (two_sample_detection_rate, paired_detection_rate) over
    _N_TRIALS independent trials at n=_N, min_n/min_effect floors disabled
    (isolating statistical detection, same methodology as
    scripts/measure_regression_power.py's grid -- see its notes 1/2).
    Both methods see the IDENTICAL underlying (baseline, current) data on
    every trial -- a fair, paired comparison of the two comparison METHODS,
    not two different random draws.
    """
    two_sample_detections = 0
    paired_detections = 0
    for trial in range(_N_TRIALS):
        seed = _CASE_CORRELATED_SEED_BASE + hash((effect_pct, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline, current = _generate_case_correlated_pair(gen, _N, effect_pct)

        two_sample_result = evaluate_regression(
            baseline,
            current,
            min_n=2,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=_N_BOOT,
            seed=trial,
        )
        paired_result = evaluate_regression_paired(
            baseline,
            current,
            min_n=2,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=_N_BOOT,
            seed=trial,
        )
        if two_sample_result.status == "regression":
            two_sample_detections += 1
        if paired_result.status == "regression":
            paired_detections += 1

    return two_sample_detections / _N_TRIALS, paired_detections / _N_TRIALS


def test_paired_comparison_detects_a_10pct_case_correlated_regression_far_more_often():
    """The core 4.3 result. n=25, true injected regression = +10% of
    BASE_MEAN ($0.001/case, additive, applied uniformly across cases with
    real case-to-case cost heterogeneity -- see
    _generate_case_correlated_pair's docstring for why this shape).

    MEASURED (this exact deterministic seeding scheme, n_trials=200,
    n_boot=1000): two_sample=0/200=0.000, paired=200/200=1.000. The
    two-sample gate essentially NEVER detects this regression at n=25 when
    real case-to-case cost variance is present (the between-case spread
    swamps a $0.001 shift entirely) -- the paired gate detects it on every
    single trial, because pairing subtracts away each case's own level
    before the effect ever has to compete with that between-case variance.

    This is the single clearest piece of evidence in B4 for why
    `--mode paired` is worth having: at a realistic n and a realistic kind
    of eval-set heterogeneity, it is the difference between a gate that
    cannot see a real regression at all and one that catches it every time.
    """
    two_sample_rate, paired_rate = _measure_paired_vs_two_sample(effect_pct=10.0)

    assert two_sample_rate == 0.0
    assert paired_rate == 1.0
    assert paired_rate - two_sample_rate >= 0.5  # generous, non-tautological margin


def test_paired_comparison_false_positive_rate_is_not_wildly_miscalibrated():
    """The 4.4 re-derivation, same harness, 0%-effect column: with NO true
    regression injected, `evaluate_regression_paired`'s false-positive rate
    must still be in a defensible neighborhood of the nominal ~2.5%
    one-sided expectation -- pairing must not come at the cost of a broken
    false-positive rate.

    MEASURED (this exact deterministic seeding scheme, n_trials=200,
    n_boot=1000): two_sample=8/200=0.040, paired=11/200=0.055. Both sit
    within a generous band of the ~2.5% nominal one-sided expectation at
    n_trials=200 (Phase 2's own 250-trial two-sample FPR measurements were
    2.00%/1.60%, so paired's 5.5% here is somewhat elevated but not
    alarmingly so at this trial count -- flagged honestly in the B4 session
    report as worth a larger confirmatory run before this ships as the
    DEFAULT mode, not silently accepted as "close enough").
    """
    two_sample_rate, paired_rate = _measure_paired_vs_two_sample(effect_pct=0.0)

    assert two_sample_rate <= 0.10
    assert paired_rate <= 0.10
