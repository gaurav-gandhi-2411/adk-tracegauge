# adk-tracegauge — FPR Anomaly Audit

Investigates the anomaly reported in Phase 7 U2's published grid
(`reports/confidence_grid_u2.json`, `scripts/measure_regression_confidence_grid.py`,
`README.md`'s "Known limitations" table, `_regression.py`'s `DEFAULT_CONFIDENCE`
docstring): **paired mode's measured false-positive rate (FPR) exceeded
two-sample's at 4 of the 6 shared (confidence, n) FPR cells**, most visibly at
`n=50`/`confidence=0.95` — paired 3.70% [2.96%, 4.62%] vs two-sample 3.00%
[2.34%, 3.84%] — above the ~2.5% nominal one-sided expectation. Under
correctly-implemented statistics, pairing removes case-level variance and
should sharpen a test, not inflate its false-alarm rate; this document
investigates whether that expectation was violated by a real defect, or by
something else.

**Verdict, stated up front**: no code defect exists in either
`evaluate_regression`/`evaluate_regression_paired` (`_regression.py`) or their
respective null-data generators. **The published cross-mode ranking
("paired FPR > two-sample FPR") was never actually significance-tested before
being written up as a finding, and does not survive being tested** — a
two-proportion z-test on the original grid's own counts finds no cell
significant (largest z=1.80, p=0.07), and an independent, 2.5x-larger
re-measurement (different seed base) finds the ranking does not reproduce
(largest z=1.29, p=0.20). What IS real, and reproduces cleanly: **both modes**
show percentile-bootstrap FPR anti-conservatism above their own nominal
one-sided alpha at `n=30`/`n=50` — the SAME, already-documented, generic
small-`n` bootstrap phenomenon this codebase already knew about at `n=10`/`n=25`
(see `_regression.py`'s "Anti-conservatism at small n" section), now confirmed
to persist, roughly equally in both modes, at the shipped `min_n=30` and
`n=50` too. This is a 3.5-shaped resolution (a measurement/publication
artifact requiring corrected published figures), not a 3.6-shaped one (no new
real property of the estimator was found beyond what was already documented).

---

## 3.1 — Null-generating process

**Two-sample** (`scripts/measure_regression_alpha_grid.py`'s `_generate_pair`,
imported by `measure_regression_confidence_grid.py` as
`generate_two_sample_pair`):

```python
def _generate_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    effect = effect_pct / 100.0
    baseline = [max(0.0001, rng.gauss(BASE_MEAN, BASE_SD)) for _ in range(n)]
    current = [
        max(0.0001, rng.gauss(BASE_MEAN * (1 + effect), BASE_SD * (1 + effect))) for _ in range(n)
    ]
    return baseline, current
```

At `effect_pct=0.0` (the null): `baseline` and `current` are two **fully
independent** `n`-length i.i.d. draws from the identical
`N(BASE_MEAN=0.010, BASE_SD=0.0015)` distribution — no case structure at all,
which is correct: two-sample mode has no pairing key to preserve or break in
the first place, so an unpaired flat null is the right generator for it.

**Paired** (`scripts/measure_regression_power.py`'s
`generate_case_correlated_pair`, imported by
`measure_regression_confidence_grid.py` as `compute_paired_confidence_grid`'s
generator):

```python
def generate_case_correlated_pair(
    rng: random.Random, n: int, effect_pct: float
) -> tuple[list[float], list[float]]:
    effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
    case_levels = [
        rng.uniform(CASE_CORRELATED_LEVEL_LOW, CASE_CORRELATED_LEVEL_HIGH) for _ in range(n)
    ]
    baseline = [max(0.0001, rng.gauss(d, CASE_CORRELATED_WITHIN_CASE_SD)) for d in case_levels]
    current = [
        max(0.0001, rng.gauss(d + effect_usd, CASE_CORRELATED_WITHIN_CASE_SD)) for d in case_levels
    ]
    return baseline, current
```

At `effect_pct=0.0`: `effect_usd = 0.0`, so `current[i] = max(0.0001,
Gauss(case_levels[i], sd))` — **the SAME `case_levels[i]` used for
`baseline[i]`**, just an independent draw of Gaussian noise around it. This is
the correct null for a paired test: it preserves the exact same case-level
pairing structure (`baseline[i]` and `current[i]` share the same underlying
case level `d_i`) that the alternative (`effect_pct > 0`) generator uses — the
ONLY difference between the null and alternative generators is whether
`effect_usd` is added to `current`'s center, never whether the pairing
structure itself is present. **Finding: no mismatch.** The null generator does
NOT break the case-level pairing the alternative generator preserves; a
paired *test* is being run against genuinely paired null data in both cases.
(Confirmed by direct derivation, not just inspection: since both
`baseline[i]` and `current[i]` are drawn as `Gauss(d_i, sd)` independently
under the null, `delta[i] = current[i] - baseline[i]` is *exactly*
`N(0, sqrt(2)*sd)` for every case, independent of `d_i` — `d_i` cancels
exactly in the subtraction, before any floor-clipping is considered, and
floor-clipping itself is shown negligible in 3.4's design notes below, given
`case_levels` ranges 0.004–0.024 against `sd=0.0008`, ≥4.9 SDs from the 0.0001
floor at every point in that range.)

## 3.2 — Resampling unit

`bootstrap_mean_of_paired_deltas` (`_regression.py`):

```python
rng = random.Random(seed)
n = len(deltas)
means = [0.0] * n_boot
for i in range(n_boot):
    means[i] = statistics.fmean(rng.choices(deltas, k=n))
means.sort()
```

`deltas` is computed ONCE, before any resampling, as
`[c - b for b, c in zip(baseline_costs, current_costs, strict=True)]`
(`evaluate_regression_paired`) — i.e. `deltas[i]` already represents
`current[i] - baseline[i]` for the SAME case `i`. Each bootstrap resample
draws `n` values **from this precomputed delta vector**, `rng.choices(deltas,
k=n)` — resampling an INDEX into `deltas`, never resampling `baseline` and
`current` as two separate vectors. This is the textbook paired-bootstrap
prescription (Efron & Tibshirani ch. 6): pairing collapses "two groups" into
"one sequence of differences" *before* resampling, and each case's own
baseline/current draw always travels together because they were already
subtracted into a single number before the bootstrap loop ever runs.
**Finding: no bug.** `bootstrap_mean_of_paired_deltas` never resamples
baseline/current independently — the concern that it might collapse to an
"independent-resample paired bootstrap" (which would understate the
pairing's variance reduction) does not describe this code.

## 3.3 — Interval construction

Both `bootstrap_diff_of_means` and `bootstrap_mean_of_paired_deltas` use the
IDENTICAL percentile-bootstrap construction — no BCa, no normal
approximation, no other method in either path:

```python
alpha = 1.0 - confidence
ci_lower = _percentile(diffs, 100 * alpha / 2)  # bootstrap_diff_of_means
ci_lower = _percentile(means, 100 * alpha / 2)  # bootstrap_mean_of_paired_deltas
```

The practical-significance floor check is byte-identical between
`evaluate_regression` and `evaluate_regression_paired`:

```python
practically_significant = abs(effect_usd) >= min_effect_usd or (
    effect_pct is not None and abs(effect_pct) >= min_effect_pct
)
is_regression = statistically_significant and practically_significant
```

Same operators (`>=`, `or`, `and`), same order, in both functions — no
strict-vs-non-strict asymmetry, no floor-before-rounding-vs-after asymmetry.
**This is also moot for the specific anomaly under investigation**: the
confidence-grid measurement (`compute_two_sample_confidence_grid`/
`compute_paired_confidence_grid`) forces `min_effect_usd=0.0`,
`min_effect_pct=0.0` for every cell (isolating pure statistical significance,
per note 2 in that script's own docstring) and checks `ci_lower > 0.0`
identically in both grid functions — the practical-significance floor never
enters the FPR numbers under investigation at all. **Finding: no asymmetry,
and not load-bearing for this anomaly either way.**

## 3.4 — Hypothesis and discriminating tests

**H1 (`scripts/measure_fpr_anomaly_h1_discriminant.py`)**: the gap is a real,
generic structural effect of a ONE-SAMPLE bootstrap (paired) vs a TWO-SAMPLE
bootstrap (two-sample) at the SAME total variance budget and `n` — the
two-sample CI's width is built from averaging TWO independent empirical
variance estimates (baseline's own, current's own), while the paired CI's
width relies on only ONE (the deltas' own); since sample variance's own
sampling distribution (chi-squared) is right-skewed, a single-estimate CI
could be systematically too narrow slightly more often than an
averaged-two-estimate CI, elevating one-sample FPR — even on an EXACTLY
Gaussian population (zero skewness, confirmed in 3.1 for both real
generators' null case).

**Design**: strip away case levels and floor clipping; compare, on exactly
Gaussian synthetic data with the SAME total variance budget and `n`, using
the REAL production bootstrap functions (not a reimplementation): `n` iid ~
`N(0, SD_DELTA)` through `bootstrap_mean_of_paired_deltas` (one-sample) vs
`n` iid ~ `N(MU, SD_COMPONENT)` for each of two independent groups through
`bootstrap_diff_of_means` (two-sample), `SD_COMPONENT = SD_DELTA / sqrt(2)`
so `Var(diff of means) = Var(one-sample mean)` exactly.

**Result, executed** (`uv run python scripts/measure_fpr_anomaly_h1_discriminant.py`,
`N_TRIALS=3,000`/cell, `N_BOOT=1,000`, `SD_DELTA=0.00113137`):

| confidence | n | one-sample rate [Wilson 95%] | two-sample rate [Wilson 95%] | nominal | z (1s vs 2s) | p |
|---|---|---|---|---|---|---|
| 0.95 | 30 | 2.900% [2.357,3.563]% | 3.200% [2.628,3.892]% | 2.500% | −0.676 | 0.4992 |
| 0.95 | 50 | 2.867% [2.327,3.527]% | 2.733% [2.208,3.380]% | 2.500% | 0.313 | 0.7543 |
| 0.98 | 30 | 1.667% [1.267,2.190]% | 1.500% [1.123,2.001]% | 1.000% | 0.517 | 0.6051 |
| 0.98 | 50 | 1.600% [1.209,2.115]% | 1.667% [1.267,2.190]% | 1.000% | −0.204 | 0.8386 |
| 0.99 | 30 | 1.133% [0.812,1.579]% | 1.067% [0.757,1.502]% | 0.500% | 0.248 | 0.8045 |
| 0.99 | 50 | 1.333% [0.981,1.810]% | 0.767% [0.511,1.148]% | 0.500% | 2.153 | 0.0313 |

**H1 REFUTED.** 5 of 6 cells show no significant one-sample-vs-two-sample
difference (p ranges 0.20–0.84); the 6th (confidence=0.99, n=50) crosses
p=0.031, but a single crossing out of 6 independent tests is within the
~30% chance of at least one false discovery at alpha=0.05 across 6 tests,
and no other cell replicates its direction or magnitude — not a robust
pattern. Wall-clock: 371.1s, real production bootstrap functions throughout.
**Notably**, both synthetic modes here show clear elevation above nominal
(e.g. 1.067%–1.133% vs 0.500% nominal at confidence=0.99/n=30, for BOTH
one-sample and two-sample) — a first hint that the anti-conservatism is
generic to percentile-bootstrap-at-small-n, not mode-specific, which shaped
H2.

**H2 (`scripts/measure_fpr_anomaly_reproducibility.py`)**: the original
grid's "paired FPR > two-sample FPR at 4/6 cells" finding is dominated by
ordinary sampling noise in a rare-event binomial proportion (as few as 10
successes out of 2,000 trials at some cells) — not a robust, reproducible
mode-specific property. Design: re-measure the REAL null generators (not
synthetic) at the same 6 FPR cells with an independent seed base and 2.5x
the trial count (5,000 vs 2,000).

**Result, executed** (`uv run python scripts/measure_fpr_anomaly_reproducibility.py`,
`N_TRIALS=5,000`/cell, `N_BOOT=1,000`, `SEED_BASE_TWO_SAMPLE=9,000,000`,
`SEED_BASE_PAIRED=9,100,000`):

| confidence | n | two-sample rate [Wilson 95%] | paired rate [Wilson 95%] | nominal | z(2s vs nom) p | z(paired vs nom) p | z(paired vs 2s) p |
|---|---|---|---|---|---|---|---|
| 0.95 | 30 | 2.960% [2.525,3.467]% | 3.200% [2.747,3.725]% | 2.500% | 2.083, 0.0372 | 3.170, 0.0015 | 0.695, 0.4873 |
| 0.95 | 50 | 2.760% [2.341,3.252]% | 3.200% [2.747,3.725]% | 2.500% | 1.178, 0.2390 | 3.170, 0.0015 | 1.294, 0.1957 |
| 0.98 | 30 | 1.340% [1.057,1.698]% | 1.540% [1.234,1.920]% | 1.000% | 2.416, 0.0157 | 3.838, 0.0001 | 0.839, 0.4012 |
| 0.98 | 50 | 1.400% [1.110,1.765]% | 1.400% [1.110,1.765]% | 1.000% | 2.843, 0.0045 | 2.843, 0.0045 | 0.000, 1.0000 |
| 0.99 | 30 | 0.860% [0.639,1.156]% | 1.020% [0.777,1.339]% | 0.500% | 3.609, 0.0003 | 5.213, <0.0001 | 0.829, 0.4071 |
| 0.99 | 50 | 0.860% [0.639,1.156]% | 0.800% [0.588,1.087]% | 0.500% | 3.609, 0.0003 | 3.008, 0.0026 | −0.331, 0.7409 |

**H2 CONFIRMED.** Zero of the 6 paired-vs-two-sample cells reach
significance (largest z=1.294, p=0.196); at `confidence=0.98/n=50` the two
modes' rates are IDENTICAL (1.400% both). Meanwhile both modes independently
and significantly exceed their own nominal alpha at 5-6 of 6 cells each
(e.g. two-sample at confidence=0.99: 0.86% vs 0.5% nominal at BOTH n=30 and
n=50, p<0.001 each) — the same generic small-`n` percentile-bootstrap
anti-conservatism the module already documents, present in both modes at
comparable magnitude, not a paired-specific defect.

**A direct consistency check** was also run comparing the ORIGINAL grid's
own per-cell counts (2,000 trials, `reports/confidence_grid_u2.json`)
against this 5,000-trial independent re-measurement (two-proportion z-test,
same generator/cell, different seed/trial count): every one of the 12
cells (6 two-sample + 6 paired) is consistent within noise (largest
deviation z=1.70, p=0.089, at two-sample/confidence=0.98/n=30) — i.e. no
individual original point estimate was itself an outlier; it is
specifically the PAIRWISE mode-vs-mode gap, computed from two independently
noisy small-count estimates, that flips direction unpredictably run to run
at this trial count.

(The direct two-proportion z-test on the ORIGINAL grid's own published
counts, cited in this document's opening "Verdict" — largest z=1.80,
p=0.072 at confidence=0.98/n=30 — is the same test `two_proportion_z_test`
implements, unit-tested in `tests/test_fpr_anomaly_audit.py` against a
known textbook two-proportion-test value; it required no new measurement,
only applying a significance test to data that was already public.)

## 3.5 — Resolution: measurement-count artifact, harness corrected

This is the "harness artifact" path (3.5), with a specific qualification:
**neither the null-data generators nor `_regression.py`'s production code
had a defect** (3.1–3.3 confirm both are correct) — the artifact is that
`scripts/measure_regression_confidence_grid.py`'s original `N_TRIALS=2,000`,
while meeting Phase 7 U2's own stated floor and sufficient to estimate each
mode's OWN FPR reliably (3.4's consistency check), was demonstrably
insufficient to reliably RANK two modes' FPR against each other at the gap
sizes actually observed (well under 1 percentage point on a ~1–3% base
rate) — and the original grid's writeup asserted that ranking as a finding
("paired FPR is higher... at every measured n") without ever
significance-testing it.

**Fix applied**:
1. `scripts/measure_regression_confidence_grid.py`'s `N_TRIALS` raised
   2,000 → 5,000 (still using the SAME `SEED_BASE_TWO_SAMPLE`/
   `SEED_BASE_PAIRED` as before, so trials 0–1,999 are byte-identical to
   the original U2 run, extending rather than replacing that data) — a
   trial count directly demonstrated (3.4's H2 re-measurement, run with an
   independent seed base) to stabilize the cross-mode comparison, not an
   arbitrary bump.
2. A new `two_proportion_z_test` helper added to
   `measure_regression_confidence_grid.py` itself, and a new "FPR
   cross-mode significance" table printed (and written to
   `reports/confidence_grid_u2.json`'s new `fpr_cross_mode_significance`
   key) on every run — so a future re-run of this script can never again
   publish a cross-mode ranking without the significance test sitting
   right next to it.
3. Full 18-cell/mode grid (all `confidence` × `n` × `effect` cells, not
   only the FPR/`effect=0%` cells) re-run at `N_TRIALS=5,000`,
   `N_BOOT=1,000`, real Wilson 95% CIs — same generators, same
   methodology as the original U2 grid, per this audit's own "reuse the
   exact generator/methodology, don't invent a new one" instruction.

**Corrected full grid** (`uv run python scripts/measure_regression_confidence_grid.py`,
`N_TRIALS=5,000`, wall-clock 2,318.0s — two-sample 1,681.6s + paired 636.3s;
`N_BOOT=1,000` validated first against the real `n_boot=10,000` default at
the two most sensitive cells, both modes: 96.7%–100.0% verdict agreement,
150 trials each):

| mode | confidence | n | FPR (0% effect) | power (10% effect) |
|---|---|---|---|---|
| two-sample | 0.95 | 30 | 3.18% [2.73%, 3.70%] (159/5,000) | 70.40% [69.12%, 71.65%] (3,520/5,000) |
| two-sample | 0.95 | 50 | 3.16% [2.71%, 3.68%] (158/5,000) | 88.24% [87.32%, 89.10%] (4,412/5,000) |
| two-sample | 0.98 | 30 | 1.30% [1.02%, 1.65%] (65/5,000) | 57.46% [56.08%, 58.82%] (2,873/5,000) |
| two-sample | 0.98 | 50 | 1.42% [1.13%, 1.79%] (71/5,000) | 80.38% [79.26%, 81.46%] (4,019/5,000) |
| two-sample | 0.99 | 30 | 0.74% [0.54%, 1.02%] (37/5,000) | 49.30% [47.92%, 50.69%] (2,465/5,000) |
| two-sample | 0.99 | 50 | 0.84% [0.62%, 1.13%] (42/5,000) | 73.84% [72.60%, 75.04%] (3,692/5,000) |
| paired | 0.95 | 30 | 2.98% [2.54%, 3.49%] (149/5,000) | 99.60% [99.38%, 99.74%] (4,980/5,000) |
| paired | 0.95 | 50 | 3.48% [3.01%, 4.02%] (174/5,000) | 100.00% [99.92%, 100%] (5,000/5,000) |
| paired | 0.98 | 30 | 1.46% [1.16%, 1.83%] (73/5,000) | 99.22% [98.94%, 99.43%] (4,961/5,000) |
| paired | 0.98 | 50 | 1.66% [1.34%, 2.05%] (83/5,000) | 100.00% [99.92%, 100%] (5,000/5,000) |
| paired | 0.99 | 30 | 0.86% [0.64%, 1.16%] (43/5,000) | 98.36% [97.97%, 98.68%] (4,918/5,000) |
| paired | 0.99 | 50 | 0.92% [0.69%, 1.22%] (46/5,000) | 100.00% [99.92%, 100%] (5,000/5,000) |

(25%-effect column omitted: ≥99.96% at every cell, both modes, all three
confidence levels — saturated, no decision-relevant information; full data
in `reports/confidence_grid_u2.json`.)

**FPR cross-mode significance** (new `two_proportion_z_test`, printed by
the harness on every run, written to `reports/confidence_grid_u2.json`'s
`fpr_cross_mode_significance` key):

| confidence | n | two-sample | paired | z (paired − two-sample) | p |
|---|---|---|---|---|---|
| 0.95 | 30 | 159/5,000 (3.18%) | 149/5,000 (2.98%) | −0.579 | 0.5627 |
| 0.95 | 50 | 158/5,000 (3.16%) | 174/5,000 (3.48%) | 0.893 | 0.3718 |
| 0.98 | 30 | 65/5,000 (1.30%) | 73/5,000 (1.46%) | 0.686 | 0.4929 |
| 0.98 | 50 | 71/5,000 (1.42%) | 83/5,000 (1.66%) | 0.975 | 0.3298 |
| 0.99 | 30 | 37/5,000 (0.74%) | 43/5,000 (0.86%) | 0.674 | 0.5006 |
| 0.99 | 50 | 42/5,000 (0.84%) | 46/5,000 (0.92%) | 0.428 | 0.6684 |

**Zero of 6 cells significant** (all \|z\| < 1.0, all p > 0.32) — the
corrected, extended grid (same seed base as the original U2 run,
trials 0–1,999 byte-identical) independently reconfirms 3.4's H2 finding:
the original "paired FPR > two-sample FPR at 4/6 cells" narrative does not
hold at 5,000 trials/cell either. Notably, at `confidence=0.95/n=30` the
ranking now FLIPS from the original run (paired 2.98% < two-sample 3.18%)
— exactly the kind of direction instability expected from an underlying
null difference of zero, not evidence of a newly-discovered real effect in
the opposite direction.

**Two discriminating-test scripts committed as permanent, reproducible
artifacts** (matching this codebase's existing `scripts/measure_*.py`
convention — every measurement in this project is re-runnable, not just
narrated): `scripts/measure_fpr_anomaly_h1_discriminant.py` (H1, refuted)
and `scripts/measure_fpr_anomaly_reproducibility.py` (H2, confirmed), both
with `tests/test_fpr_anomaly_audit.py` smoke-testing the harness itself
(determinism, shape, and `two_proportion_z_test` standalone correctness
against a known textbook two-proportion-test value) — same discipline
`tests/test_regression_confidence_grid.py` already established for the
original grid script (full run NOT re-executed on every `pytest`
invocation, only a tiny deterministic slice).

## 3.6 — Real property of the estimator? Not beyond what was already known

No NEW real statistical property of either estimator was found. What DID
reproduce robustly — generic percentile-bootstrap anti-conservatism at
small `n`, present in roughly equal magnitude in BOTH modes — is not new:
`_regression.py`'s own "Anti-conservatism at small n" section already
documents this exact phenomenon at `n=10` (5.0% vs 2.5% nominal) and `n=25`
(3.5% vs 2.5% nominal), and already assessed and rejected two standard
fixes (BCa, studentized bootstrap) for the same underlying reason. This
audit's contribution is confirming the SAME phenomenon persists, at
comparable magnitude in both modes, at `n=30` (`min_n` itself) and `n=50` —
not discovering a new, mode-specific mechanism. **The shipped default
(paired mode, confidence=0.98, measured 1.46% [1.16%, 1.83%] FPR at n=30 in
the corrected 5,000-trial grid, vs 1.40% [0.97%, 2.02%] in the original
2,000-trial run — consistent within noise, z=0.19/p=0.85) does not need
reassessment on the grounds investigated here**: the corrected grid (3.5)
shows paired mode's FPR at that exact cell is statistically
indistinguishable from two-sample's own FPR at the same cell (z=0.69,
p=0.49), and both remain in the same "known, already-documented, small-n
anti-conservatism" regime the shipped default was already chosen against
(Phase 5 S4, Phase 7 U2's own 2.3 re-decision) — no new information here
argues for revisiting `DEFAULT_CONFIDENCE`, `min_n`, or the mode
auto-selection threshold. This is stated as a finding, not implemented as a
change — no default was altered in this audit.

## What remains open

- The already-known, already-documented generic small-`n` bootstrap
  anti-conservatism (both modes, `n=10` through `n=50` now confirmed) is
  NOT fixed by this audit — it was already flagged as a real, honest,
  unresolved limitation before this investigation began (BCa tried and
  found no measurable improvement; studentized bootstrap assessed and
  rejected on stated theoretical grounds), and remains exactly that after
  it.
- Whether a paired-mode-specific, tighter confidence default (decoupled
  from two-sample's shared `DEFAULT_CONFIDENCE`) would be worth adding
  remains a noted-but-not-implemented future option from Phase 7 U2's own
  2.3 entry — untouched by this audit, since this audit found no new
  evidence bearing on that specific question either way.
