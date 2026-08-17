# AC1 — Does the tracegauge AB1 skew finding affect adk-tracegauge?

**Trigger**: `tracegauge`'s AB1 audit (`token-efficiency-scorer`
`docs/audit/EDIT_RATIO_BOOTSTRAP_COVERAGE.md`, AB1 correction) found that its
original ratio-of-sums-bias hypothesis was wrong, and the real mechanism was
generic percentile/BCa bootstrap undercoverage on right-skewed distributions
at small n — a property of the bootstrap method, not specific to a ratio
estimator. `adk-tracegauge check` bootstraps per-invocation costs, which are
also non-negative and (per this module's own `_regression.py` docstring,
line ~72) acknowledged elsewhere in this codebase to be "a right-skewed,
non-negative distribution." This audit asks whether that acknowledged
property was ever actually present in the data this package's own published
FPR/power grid was measured against.

## 1.2 — What did the published harness actually generate?

**VERIFIED (by direct source read)**: every FPR/power figure ever published
for this package (`reports/confidence_grid_u2.json`, the README table, this
module's own docstring) was generated from **Gaussian** per-invocation cost
draws:

- Two-sample (`measure_regression_alpha_grid.py::_generate_pair`):
  `rng.gauss(BASE_MEAN=0.010, BASE_SD=0.0015)` — CV = 15%.
- Paired (`measure_regression_power.py::generate_case_correlated_pair`):
  per-case level `d_i ~ Uniform(0.004, 0.024)`, then
  `rng.gauss(d_i, WITHIN_CASE_SD=0.0008)` — CV ranges ~3.3% (at the top of
  the case-level range) to ~20% (at the bottom).

Both use `max(0.0001, ...)` floor-clamping, but the nearest case level to
that floor is >=4.8 SD away — floor-clamping contributes no meaningful skew
either. `docs/audit/FPR_ANOMALY.md` section 3.4 independently confirms this:
its H1 discriminant test explicitly used "exactly Gaussian population (zero
skewness, confirmed in 3.1 for both real generators' null case)."

**VERIFIED**: the published harness generated a symmetric, low-variance
distribution. It was never tested against anything resembling the
right-skewed shape this codebase's own docstring already says real
per-invocation USD cost has. **Not comparable to real per-invocation ADK
cost data in shape** — and, separately (found during 1.3 below, not
assumed going in), **not comparable in magnitude either**.

**No real ADK per-invocation cost telemetry is bundled in this repo** to fit
an exact skew/CV parameter to (searched `docs/`, `PLAN.md`, `README.md`,
`examples/` — none found). This is a genuine gap: nothing here confirms
what real per-invocation cost variability actually looks like. The
measurement below uses an explicitly assumed, not measured, magnitude
(CV=0.6) — flagged as such throughout, not disguised as calibrated.

## 1.3 — Re-measurement

`scripts/measure_regression_skew_sensitivity.py` (new). Scope: PAIRED mode
(shipped `--mode auto` default), confidence=0.98 (`DEFAULT_CONFIDENCE`,
shipped), n in {30, 50}, effect_pct in {0.0, 10.0, 25.0}, 5,000 trials/cell
(>= the required 2,000 floor), Wilson 95% CI on every reported rate,
n_boot=1,000 survey convention validated against the real n_boot=10,000
default first (98.7-99.3% verdict agreement at the tested cells — same
discipline as every prior grid in this codebase).

Two generators measured, to separate shape from magnitude (see "harness
self-audit" below for why this control was necessary):

1. **Skewed**: `generate_case_correlated_pair_skewed` — same case-level
   heterogeneity model, but per-invocation noise around each case level is
   lognormal (CV=0.6, skewness ≈ 2.02) instead of Gaussian.
2. **Gaussian control**: `generate_case_correlated_pair_gaussian_cv_matched`
   — identical CV=0.6, but noise stays Gaussian (symmetric).

### Results (detection rate [Wilson 95% CI] (successes/trials))

| n | effect | published (low-CV Gaussian) | Gaussian control (CV=0.6) | skewed lognormal (CV=0.6) |
|---|---|---|---|---|
| 30 | 0% (FPR) | 1.46% [1.16,1.83]% | 1.68% [1.36,2.08]% | 1.48% [1.18,1.85]% |
| 30 | 10% (power) | 99.22% [98.94,99.43]% | 4.18% [3.66,4.77]% | 4.96% [4.39,5.60]% |
| 30 | 25% (power) | n/a (not in published 6-row table) | 14.26% [13.32,15.26]% | 13.96% [13.03,14.95]% |
| 50 | 0% (FPR) | 1.66% [1.34,2.05]% | 1.52% [1.22,1.90]% | 1.74% [1.41,2.14]% |
| 50 | 10% (power) | 100.00% [99.92,100]% | 5.34% [4.75,6.00]% | 5.80% [5.19,6.48]% |
| 50 | 25% (power) | n/a | 19.70% [18.62,20.83]% | 19.14% [18.07,20.25]% |

Raw grid: `reports/skew_sensitivity_ac1.json`.

## Harness self-audit (before trusting the first-pass result)

The first version of this script measured only the skewed lognormal
generator. Result: FPR close to published (no red flag), but power at
n=30/10% collapsed from the published 99.22% to 4.96% — an order-of-magnitude
gap far larger than AB1's own coverage-undercoverage findings in
`tracegauge` (a few percentage points of CI miscoverage, not a 20x power
collapse). Per this project's own standing rule ("audit the harness before
accepting the result" — the exact AB1 precedent this whole AC1 phase is
named after), that gap was too large to accept without checking what
produced it.

Cause found: switching the per-invocation noise from `WITHIN_CASE_SD=0.0008`
(Gaussian, CV ~3-20% depending on case level) to a CV=0.6 lognormal changed
**two things at once** — shape (Gaussian to lognormal) AND magnitude (~10x
variance increase). The script's own first-draft docstring claimed a
"shape-only manipulation" — that claim was wrong, caught only by re-deriving
what the change actually did, not by re-reading the docstring's own words.

The Gaussian-CV-matched control (added after this check) isolates the two:
it changes ONLY magnitude, holding shape symmetric. Its numbers are close to
the skewed lognormal's at every cell (e.g. n=30/10%: 4.18% Gaussian vs 4.96%
skewed; n=30/25%: 14.26% Gaussian vs 13.96% skewed) — **shape does not
explain the collapse; magnitude does.**

## Conclusion — split verdict, not a single yes/no

**FPR (1.4 applies — VERIFIED, holds)**: AB1's specific mechanism
(percentile/BCa bootstrap FPR anti-conservatism under right skew) does
**not** reproduce in `adk-tracegauge`'s paired mode at the shipped
configuration. All three FPR measurements (published low-CV Gaussian,
CV-matched Gaussian control, CV-matched lognormal skew) cluster at
1.46-1.74%, at or below the nominal one-sided 2% target implied by
confidence=0.98 — no inflation, in either the Gaussian-CV-matched or the
skewed condition. This is evidence AGAINST the transfer hypothesis, not
merely absence of evidence for it: the exact mechanism AB1 found was tested
directly, at a magnitude of skew well beyond what the original harness
carried, and the FPR did not move. **The published FPR figures hold under
skew** and no README correction is needed for FPR.

**Power (1.5 applies — stop and report, not silently corrected)**: the
published power table (99.22%/100% at n=30/50, 10% effect) is **not**
robust to a plausible-but-unmeasured increase in per-invocation cost
variance. At CV=0.6 — chosen because it is a commonly cited magnitude for
LLM-cost-like data, but explicitly **not measured against real ADK
telemetry**, none of which exists in this repo — power collapses to
4-6% at 10% effect and 14-20% at 25% effect, for BOTH the Gaussian and
lognormal shape. This is not "the published numbers are wrong" (the CV=0.6
assumption itself is unverified) but it is also not "the published numbers
verifiably hold" (1.4's bar) — it is a genuine, measurement-backed
demonstration that the published power table's real-world usefulness
depends entirely on an assumption about per-invocation cost variance that
was never validated, and that a plausible value for that assumption
produces a materially different, much weaker result.

**Per 1.5: stopping here.** No change has been made to README.md's
published FPR/power table or to `_regression.py`'s `DEFAULT_CONFIDENCE`/
`MIN_N_DEFAULT` — those decisions stay as shipped pending a decision on how
to close the variance-calibration gap. What IS committed: this audit doc,
the measurement script, and the raw grid — evidence, not a correction,
since correcting the power table would require knowing the real CV, which
requires real ADK per-invocation telemetry this repo does not have.

## Open question for GG

Two paths close this gap, neither attempted here (both require a decision,
not just execution):

1. **Get real data.** If real ADK per-invocation cost samples exist or can
   be gathered (from an actual eval run, not synthesized), fit an empirical
   CV/skew and re-run this grid at the real value instead of an assumed one.
2. **Caveat instead of re-measure.** Add an explicit "the published power
   figures assume low per-invocation cost variance (CV ~3-20%); real ADK
   costs may vary more, and this package has not validated that assumption
   against real telemetry" caveat to the README/docstring, without claiming
   a specific corrected number that isn't itself measured against real data.

Recommend (2) as the faster, honest interim step, with (1) queued as
follow-up if/when real per-invocation ADK cost data becomes available —
but this is GG's call, not something to decide unilaterally given the
"outranks new features" instruction.
