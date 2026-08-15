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
from adk_tracegauge.snapshot import Snapshot, SnapshotRecord, resolve_pairing

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


_HISTORICAL_CONFIDENCE = 0.95
"""Phase 5 S4 changed DEFAULT_CONFIDENCE 0.95 -> 0.98 (see _regression.py).
The measurements in this section reproduce a SPECIFIC, already-documented
Phase 3 B4 / Phase 4 R2 historical finding ("does pairing help, quantified"
-- numbers cited in PLAN.md and the module docstrings of _regression.py/
snapshot.py) -- they are not testing "the current shipped default's
behavior" (that's test_regression.py's job). Pinned explicitly to the
confidence level those numbers were actually measured at, so this
reference measurement stays decoupled from -- and unbroken by -- future
changes to DEFAULT_CONFIDENCE. (Confirmed this is a real effect, not just
theoretical: at DEFAULT_CONFIDENCE=0.98, the case-correlated 10%-effect
cell below measures 199/200=0.995, not 200/200=1.000 -- one trial's CI
lower bound sits between the 0.95 and 0.98 thresholds. Real, honest, and
exactly the kind of small power cost documented in _regression.py's
DEFAULT_CONFIDENCE docstring -- not a bug.)"""


def _measure_paired_vs_two_sample(effect_pct: float) -> tuple[float, float]:
    """Returns (two_sample_detection_rate, paired_detection_rate) over
    _N_TRIALS independent trials at n=_N, min_n/min_effect floors disabled
    (isolating statistical detection, same methodology as
    scripts/measure_regression_power.py's grid -- see its notes 1/2).
    Both methods see the IDENTICAL underlying (baseline, current) data on
    every trial -- a fair, paired comparison of the two comparison METHODS,
    not two different random draws. Confidence pinned to
    _HISTORICAL_CONFIDENCE -- see that constant's docstring.
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
            confidence=_HISTORICAL_CONFIDENCE,
            min_n=2,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=_N_BOOT,
            seed=trial,
        )
        paired_result = evaluate_regression_paired(
            baseline,
            current,
            confidence=_HISTORICAL_CONFIDENCE,
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


# --- Phase 4 R2: re-measured through the FULL snapshot/resolve_pairing -----
# pipeline, keyed on eval_case_id (the corrected primary key) instead of
# calling evaluate_regression_paired directly on hand-built lists -- this
# verifies the key-resolution plumbing (Snapshot.costs_by_eval_case_id ->
# pair_costs_by_eval_case_id -> resolve_pairing -> evaluate_regression_paired)
# end to end, not just the underlying bootstrap math (already proven above
# and unchanged -- evaluate_regression_paired itself was not touched by R2).
# The task's own instruction: do not assume the eval-case-id-keyed pipeline
# reproduces B4's session_id-keyed numbers just because the statistics are
# identical -- verify the WHOLE pipeline, including the new key, end to end.


def _snapshots_from_case_correlated_pair(
    baseline_costs: list[float], current_costs: list[float], *, key: str
) -> tuple[Snapshot, Snapshot]:
    """Builds real Snapshot objects with one record per synthetic eval case,
    keyed by ``eval_case_id`` (key="eval_case_id", simulating a `tracegauge
    snapshot --eval-history`-resolved pair) or by ``session_id``
    (key="session_id", simulating a hand-rolled harness with NO
    --eval-history join -- B4's original mechanism, unchanged). Case ids are
    identical between baseline and current (same eval set, same case
    ordering), matching a real paired baseline/current pair."""

    def _record(i: int, cost: float) -> SnapshotRecord:
        case_id = f"case-{i}"
        return SnapshotRecord(
            invocation_id=f"inv-{case_id}",
            cost_usd=cost,
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=0,
            models=[],
            call_count=1,
            eval_case_id=case_id if key == "eval_case_id" else None,
            session_id=case_id if key == "session_id" else None,
        )

    baseline = Snapshot(
        schema_version=2,
        created_at="2026-01-01",
        records=[_record(i, c) for i, c in enumerate(baseline_costs)],
    )
    current = Snapshot(
        schema_version=2,
        created_at="2026-01-01",
        records=[_record(i, c) for i, c in enumerate(current_costs)],
    )
    return baseline, current


def test_resolve_pairing_through_eval_case_id_key_reproduces_the_headline_4_3_result():
    """The corrected-key equivalent of
    test_paired_comparison_detects_a_10pct_case_correlated_regression_far_more_often
    -- same n=25, same case-correlated +10% generator, same deterministic
    seeding -- but every trial's pairing now goes through a real Snapshot
    pair keyed on eval_case_id and resolve_pairing's actual key-selection
    logic, exactly the path `tracegauge check --mode paired` runs when
    `tracegauge snapshot --eval-history` was used for both the baseline and
    current runs (the fix that makes paired mode reachable for the default
    `adk eval` CLI flow -- see snapshot.py's module docstring).

    MEASURED: eval_case_id-keyed = 200/200 = 1.000, matching B4's original
    session_id-keyed 200/200 = 1.000 exactly (as expected -- the bootstrap
    math evaluate_regression_paired runs is identical either way; what
    changed is that the DATA now genuinely flows through resolve_pairing's
    real key-resolution rather than being handed to evaluate_regression_paired
    pre-aligned). Also confirms resolve_pairing's resolved_key is
    "eval_case_id" on every trial, not "session_id" or "none" -- the key
    fallback chain is exercising the intended branch, not accidentally
    falling through.

    Confidence pinned to `_HISTORICAL_CONFIDENCE` (0.95) -- see that
    constant's docstring for why this specific historical measurement stays
    decoupled from Phase 5 S4's DEFAULT_CONFIDENCE change.
    """
    detections = 0
    resolved_keys: set[str] = set()
    for trial in range(_N_TRIALS):
        seed = _CASE_CORRELATED_SEED_BASE + hash((10.0, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline_costs, current_costs = _generate_case_correlated_pair(gen, _N, 10.0)
        baseline, current = _snapshots_from_case_correlated_pair(
            baseline_costs, current_costs, key="eval_case_id"
        )

        paired_baseline, paired_current, matched, resolved_key = resolve_pairing(baseline, current)
        resolved_keys.add(resolved_key)
        assert len(matched) == _N  # every case paired, none dropped

        result = evaluate_regression_paired(
            paired_baseline,
            paired_current,
            confidence=_HISTORICAL_CONFIDENCE,
            min_n=2,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=_N_BOOT,
            seed=trial,
        )
        if result.status == "regression":
            detections += 1

    assert resolved_keys == {"eval_case_id"}
    assert detections / _N_TRIALS == 1.0


def test_resolve_pairing_through_session_id_key_still_reproduces_the_same_result():
    """The B4-original mechanism (hand-rolled harness, NO --eval-history --
    only session_id captured) run through the SAME resolve_pairing pipeline,
    confirming the fallback chain's second branch is equally correct and
    unregressed by the R2 rework -- resolved_key must be "session_id" on
    every trial (eval_case_id is never populated in this scenario), and the
    detection rate must match the eval_case_id-keyed result exactly (same
    underlying data, same statistic, only the key label differs).

    Confidence pinned to `_HISTORICAL_CONFIDENCE` (0.95) -- see that
    constant's docstring.
    """
    detections = 0
    resolved_keys: set[str] = set()
    for trial in range(_N_TRIALS):
        seed = _CASE_CORRELATED_SEED_BASE + hash((10.0, trial)) % 1_000_000
        gen = random.Random(seed)
        baseline_costs, current_costs = _generate_case_correlated_pair(gen, _N, 10.0)
        baseline, current = _snapshots_from_case_correlated_pair(
            baseline_costs, current_costs, key="session_id"
        )

        paired_baseline, paired_current, matched, resolved_key = resolve_pairing(baseline, current)
        resolved_keys.add(resolved_key)
        assert len(matched) == _N

        result = evaluate_regression_paired(
            paired_baseline,
            paired_current,
            confidence=_HISTORICAL_CONFIDENCE,
            min_n=2,
            min_effect_usd=0.0,
            min_effect_pct=0.0,
            n_boot=_N_BOOT,
            seed=trial,
        )
        if result.status == "regression":
            detections += 1

    assert resolved_keys == {"session_id"}
    assert detections / _N_TRIALS == 1.0
