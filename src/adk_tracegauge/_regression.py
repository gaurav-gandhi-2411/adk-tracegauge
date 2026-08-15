"""adk_tracegauge/_regression.py — Bootstrap-CI cost regression detection.

Core statistics for ``adk-tracegauge check`` (see ``_cli.py``). Given two
per-invocation cost distributions (a saved baseline, a current run), decides
whether the current run's mean cost has REGRESSED (gotten significantly and
meaningfully more expensive) relative to the baseline.

Deliberately stdlib-only (``random``, ``statistics``, ``math``) rather than
numpy/scipy. numpy/scipy ARE already transitive dependencies of this
package today (pulled in by google-adk[eval]'s scikit-learn/pandas chain --
confirmed via ``uv.lock``), so using them directly would have worked. Chose
stdlib anyway: this package's own positioning is "a small focused tool"
(see PLAN.md), and depending on an *undeclared* transitive dependency for
its core differentiator is fragile -- if google-adk ever drops or changes
its [eval] extra's own dependency chain, numpy/scipy could silently
disappear without this package's own pyproject.toml ever having claimed
them. A percentile-bootstrap over a few hundred/thousand resamples of a
few dozen-to-few-hundred floats is not remotely performance-sensitive
enough to need numpy's vectorization to begin with.

Methodology: percentile bootstrap on the difference in means
(``mean(current) - mean(baseline)``), one-sided (only an INCREASE counts as
a regression -- a significant cost *decrease* is not a build failure).
``n_boot`` resamples are drawn independently for baseline and current
(each resample redrawn WITH replacement at its own original sample size --
the standard bootstrap prescription, not resampling the pooled data), the
resample mean difference is recorded each time, and the reported CI is the
``confidence``-level central interval of that resampled distribution
(e.g. the [2.5th, 97.5th] percentile for confidence=0.95). A regression
fires only when BOTH bars clear (see ``evaluate_regression``):

1. **Statistical significance**: the bootstrap CI's lower bound is > 0 --
   i.e. the resampled distribution of cost increases is inconsistent with
   "no real difference, just sampling noise" at the configured confidence
   level.
2. **Practical significance**: the observed effect size (in absolute USD
   and/or relative percent) clears a configurable floor
   (``min_effect_usd``/``min_effect_pct``, OR'd together -- either clearing
   its own floor is enough). This exists specifically so a
   statistically-significant-but-trivial delta (e.g. a $0.0001 mean cost
   increase that becomes "significant" purely because the sample is huge)
   does not fail a build on its own -- statistical significance answers
   "is this real?", practical significance answers "do we care?", and a
   regression gate needs both, not either alone.

A verdict is only meaningful with a reasonably-sized sample of each group.
Below ``min_n`` (default 30) per group, ``evaluate_regression`` refuses to
compute a bootstrap CI at all and reports ``status="insufficient_data"``
instead of a numerically well-formed but statistically unreliable verdict.
n>=30 per group is the textbook rule-of-thumb threshold at which the CLT
gives a reasonably well-behaved (roughly normal) sampling distribution for
a mean, which is also the same regime in which a percentile-bootstrap CI
starts to behave well (see Efron & Tibshirani, *An Introduction to the
Bootstrap*, 1993) -- below it, both the normal approximation implicit in
"trust this interval" and the bootstrap's own resampling coverage become
unreliable, especially for a right-skewed, non-negative distribution like
per-invocation USD cost.

Phase 3 B4 note: this ``min_n=30`` figure was cited by Phase 2 honestly as a
rule of thumb, not independently derived from this project's own data --
and Phase 2 never measured this gate's statistical POWER (the probability
of actually detecting a real regression) at realistic sample sizes, only
its false-positive rate at a single n=40. B4's power-grid measurement
(``scripts/measure_regression_power.py``) found the unpaired two-sample
test above does *not* reliably (>=80% detection) catch a true 10% cost
regression until n is much larger than typical ADK eval-set sizes -- see
that script's own docstring and the B4 session report for the full grid and
the honest verdict. ``evaluate_regression_paired`` below is B4's
implemented response: a paired bootstrap over per-eval-case cost deltas,
which is substantially more powerful than the unpaired test at the same n
whenever a real pairing key is available (see ``snapshot.py``'s
``session_id`` field and ``_cli.py``'s ``--mode paired``).

Phase 4 R4 note: B4's power grid is a one-time, on-demand OFFLINE
simulation against a synthetic generator -- useful for characterizing the
gate in general, but silent about whether any ONE PARTICULAR ``check`` run's
own actual sample (its real observed variance, its real n) is itself
well- or under-powered. Every call to ``evaluate_regression``/
``evaluate_regression_paired`` now ALSO computes, from THIS run's own
observed data, the minimum effect size a verdict at this n/variance could
reliably (80% power, matching B4's own "reliable" bar) detect at all --
see the "Achieved statistical power" section below -- and prints it on
EVERY run (pass, fail, or insufficient_data), plus an explicit warning
when the caller's configured practical-significance floor is smaller than
that achievable floor (i.e. the caller is nominally asking to catch
smaller regressions than this run's own statistics can actually resolve).
This makes the B4 power limitation visible at runtime, per-run, using real
numbers from the real sample -- not only in documentation a user might
never read.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MIN_N_DEFAULT = 30
"""Minimum sample size per group before a bootstrap verdict is trusted --
see module docstring for the n>=30 justification (CLT/bootstrap-stability
rule of thumb, not a guess).

Phase 4 R4, 4.3: explicitly RE-EXAMINED, not left unchanged by default.
Measured (200 trials/cell, n_boot=1000, B4's exact generator/methodology,
isolated statistical detection with min_effect floors disabled -- same
convention as B4's own grid) the two-sample gate's detection rate for a true
10% cost regression at n in {30, 35, 40, 45}: 71.5%, 79.0%, 77.5%, 83.0% --
noisy (non-monotonic between 35 and 40 at this trial count, consistent with
B4's own note that this harness has real trial-count noise) but clearly
NOT reliably clearing the >=80% bar until somewhere around n=40-45 FOR THIS
SPECIFIC (BASE_SD=$0.0015, 10%-effect) scenario.

DECISION: kept at 30, not raised. Reasoning: min_n's actual statistical job
is bootstrap/CLT-validity (the CI's own coverage behaving well), a property
of the ESTIMATOR, independent of any particular effect size -- that
justification is untouched by the above measurement. "Does the gate reach
80% power for a 10% regression" is a DIFFERENT question, and it has no
single correct answer at the package level: power depends jointly on n,
the CALLER's own real cost variance, and the regression magnitude THAT
CALLER cares about -- none of which this package can know in advance. The
measurement above already shows this concretely: even n=45 only marginally
clears 80% for THIS one synthetic variance/effect combination; a caller
with higher cost variance, or one who cares about a 5% regression rather
than 10%, would need a much larger n for the same 80% bar (B4's own grid:
n=100 clears only 64.5% for a 5% effect) -- there is no single new min_n
that generically fixes this, only one optimized for one arbitrary scenario.
Raising min_n to any such value would also have a real, direct cost: it
would make the gate categorically refuse (exit 3) on real ADK eval sets in
the 30-44 range that are otherwise legitimate to compare, trading a
marginal detection-confidence gain for the loss of ANY signal in a size
range that is realistic for ADK eval sets (see README/`examples/
03_ci_regression_gate.py`, n=40). 4.1/4.2 (this same work item, see
``minimum_detectable_effect_usd``/``_below_floor_warning`` below) are the
actually-general fix: instead of gatekeeping on one fixed n chosen for one
assumed scenario, they compute the REAL achievable detection floor from
THIS run's own actual data every time and warn explicitly when it exceeds
the caller's own configured floor -- correctly adapting to whatever
variance/effect-size-of-interest a given caller actually has, which a
static min_n cannot. min_n=30 remains legitimately justified for the
narrower CLT-validity role it was always defined for (n=10's own elevated
FPR in B4's grid, 5.0% vs the ~2.5% nominal expectation, is real evidence
FOR keeping SOME floor in the 20s-30s range -- just not evidence that the
floor must chase 80%-power-for-a-10%-regression specifically).

Phase 6 T4 re-validation: the above measurement (71.5%/79.0%/77.5%/83.0% at
n in {30,35,40,45}) was taken at confidence=0.95, the default AT THE TIME --
Phase 5 S4 has since changed ``DEFAULT_CONFIDENCE`` to 0.98, which lowers
power at every n (tighter alpha always costs power, see ``DEFAULT_CONFIDENCE``'s
own docstring), so the n=30-44-range decision above needed re-examination
against the NEW default rather than being assumed to still hold. Re-measured
(same generator/methodology, statistical-only/floors-disabled, 500
trials/cell, n_boot=1000, confidence=0.98, TWO independent seed bases at
n=30/45/50 as a cross-check) for a true 10% regression:

    n     trial 1   trial 2   (S4's own 90-cell grid, same n/effect/confidence)
    30    57.2%     56.6%     58.4% (statistical-only, matches within noise)
    35    64.4%     --        --
    40    68.8%     --        --
    45    77.2%     72.8%     --
    50    79.6%     81.0%     83.4%

CONCLUSION UNCHANGED, evidence now stronger: no integer n in {30,35,40,45}
comes close to 80% at confidence=0.98 either (worst-to-best: 56.6% to
77.2%). n=50 -- the value Phase 5's S4 grid alone might suggest as "clears
80%" (83.4%) -- is REVEALED as only marginally/inconsistently at the
boundary once measured independently twice more: 79.6% and 81.0%, both
within ~2 percentage points of 83.4% (three independent 500-trial
measurements averaging ~81.3%, well inside a single 500-trial run's own
~1.8-point binomial standard error). Raising min_n to 50 would therefore
NOT reliably buy 80% power for a 10% effect -- it would buy a coin-flip's
worth of "maybe just above, maybe just below 80%" -- while definitely
refusing every real 30-49-invocation eval set outright. DECISION: min_n
stays 30. The runtime achieved-power/minimum-detectable-effect reporting
(4.1/4.2 below, unconditionally printed every run since Phase 4 R4) remains
the correct, general fix: it tells a caller running at n=30 their REAL
achieved power and detectable floor for THEIR OWN data, rather than this
package silently implying a fixed n clears some blanket reliability bar it
provably does not, at either confidence level. See ``PLAN.md``'s Phase 6 T4
entry and ``tests/test_regression.py``'s
``test_power_at_min_n_under_shipped_confidence_remains_below_80pct_target``
for the reproducible, asserted version of this measurement.

Phase 7 U1: paired mode became the DEFAULT `--mode auto` preference (see
``_cli.py``'s module docstring and ``_paired_mode_viable``), which raised
the question of whether ITS OWN auto-selection/refusal threshold should be
lower than two-sample's -- kept identical, backed by a full dedicated
paired-mode power grid (``scripts/measure_paired_power_grid.py``, n in
{10, 25, 50, 100} x effect in {0, 5, 10, 25, 50}%, 1,000 trials/cell,
confidence=0.98, the case-correlated generator, 20,000 simulated `check`
calls). The 0%-effect (false-positive rate) column at every measured n is
HIGHER for paired than the two-sample grid's own FPR at the same n and
confidence (Phase 5 S4's 90-cell grid): n=10 4.1% (paired) vs 2.2%
(two-sample); n=25 2.4% vs 1.2%; n=50 1.6% vs 1.6% (only point of parity);
n=100 1.2% vs 0.4% -- confirming, across the FULL n range this time (not
just Phase 4 R2's single n=25 data point), that paired mode is more
POWERFUL at a given n (dramatically so -- e.g. 97.8% vs 51.4% detection at
n=25/10%-effect) but not more RELIABLE (lower FPR) at small n, which is
exactly why `_paired_mode_viable` keeps the SAME `min_n` bar rather than a
lower one for paired mode specifically."""

DEFAULT_CONFIDENCE = 0.98
"""Phase 5 S4: CHANGED from 0.95, after measuring (not guessing) that
confidence=0.95's real, shipped-configuration false-positive rate at
`min_n=30` is ~3.93-4.4% (Phase 4 R4.4, re-confirmed this item with real
`n_boot=10000`: 23/500=4.60% and 21/500=4.20%, combined 44/1000=4.4%) --
for a CI gate whose entire value proposition is being trustworthy, a ~1-in-
23-to-25 false alarm rate on every clean run is not acceptable (see the S4
session report's 4.1 assessment: a gate that cries wolf this often trains
users to ignore or disable it, a product-credibility failure, not only a
statistics one).

Chosen via a full one-sided-alpha x n x true-effect grid
(``scripts/measure_regression_alpha_grid.py``, 90 cells, alpha in
{0.025, 0.01, 0.005} <-> confidence in {0.95, 0.98, 0.99} via
``confidence = 1 - 2*alpha`` -- see ``_one_sided_alpha``), n in
{10, 25, 30, 50, 100, 250}, true effect in {0%, 5%, 10%, 25%, 50%}, 500
trials/cell. Two competing goals, both measured explicitly, not eyeballed:

1. **FPR at `min_n=30`, real shipped config** (real floors, real
   `n_boot=10000`, 500 trials x 2 independent seed bases): confidence=0.95
   (old default) 4.4% combined; confidence=0.98 (NEW default) **2.3%
   combined** (13/500=2.60%, 10/500=2.00%); confidence=0.99 1.6% combined
   (9/500=1.80%, 7/500=1.40%). Target: at or under ~2% (a defensible
   correction below the originally-INTENDED nominal 2.5%, for safety
   margin -- see the S4 report). 0.98 lands within sampling noise of that
   target (combined-1000-trial standard error ~0.9 points at 2 SE) and is
   a >45% real reduction from the old default; 0.99 clears it with more
   margin but at a real, stated power cost (next point).
2. **Power for a 10% true regression at n=50 must not collapse** --
   floor set at 80%, reusing this SAME module's own established
   ``ACHIEVED_POWER_TARGET`` "reliable detection" convention (Phase 4 R4)
   rather than inventing a new number. Measured (alpha grid, n_boot=1000):
   confidence=0.95: 91.2%; confidence=0.98: **83.4%** (clears 80%);
   confidence=0.99: 76.2% (does NOT clear 80% -- a real collapse by this
   project's own definition of "reliable").

confidence=0.99 was REJECTED specifically because it fails criterion 2,
even though it does best on criterion 1 -- tightening alpha always trades
FPR for power, and 0.99 spends too much power for a marginal further FPR
gain over 0.98. confidence=0.98 (one-sided alpha=0.01) is the point that
satisfies both stated constraints. See the S4 session report / PLAN.md's
Phase 5 S4 entry for the full 90-cell grid and the 4.3 power-cost
extraction at n=30/n=50 for 10/25/50% effects.

Phase 7 U2, 2.3 -- RE-DECIDED after paired mode became the DEFAULT
`--mode auto` preference (U1): this choice was made using ONLY two-sample
data, before paired-by-default existed. Once paired is the primary path
for most real runs, does its own different FPR/power profile change the
optimal alpha tradeoff? Re-measured BOTH modes side by side on the SAME
(confidence, n, effect) grid, at higher rigor than S4's original decision
(2,000 trials/cell vs S4's 500, real Wilson-score CIs on every detection
rate, not bare point estimates) -- confidence in {0.95, 0.98, 0.99} x n in
{30, 50} x true effect in {0%, 10%, 25%}, statistical-only/floors-disabled
(same convention as S4), `scripts/measure_regression_confidence_grid.py`,
72,000 total simulated bootstrap evaluations, real wall-clock 902.8s.

    TWO-SAMPLE (fallback path -- used when no pairing key resolves):
    confidence  n=30 FPR              n=30 pow(10%)         n=50 FPR              n=50 pow(10%)
    0.95        2.75% [2.12,3.56]%    72.05% [70.04,73.97]% 3.00% [2.34,3.84]%    88.40% [86.92,89.73]%
    0.98        0.85% [0.53,1.36]%    57.80% [55.62,59.95]% 1.20% [0.81,1.78]%    81.25% [79.48,82.90]%
    0.99        0.50% [0.27,0.92]%    49.10% [46.91,51.29]% 0.65% [0.38,1.11]%    74.20% [72.24,76.07]%

    PAIRED (DEFAULT path whenever a pairing key resolves, U1):
    confidence  n=30 FPR              n=30 pow(10%)         n=50 FPR              n=50 pow(10%)
    0.95        2.55% [1.94,3.34]%    99.85% [99.56,99.95]% 3.70% [2.96,4.62]%    100.0% [99.81,100]%
    0.98        1.40% [0.97,2.02]%    99.45% [99.02,99.69]% 1.80% [1.30,2.48]%    100.0% [99.81,100]%
    0.99        0.90% [0.57,1.42]%    98.80% [98.22,99.19]% 1.10% [0.73,1.66]%    100.0% [99.81,100]%

(25%-effect column omitted from both tables above: >=99.95% at every cell,
both modes, all three confidence levels -- effectively saturated, carries
no decision-relevant information here; see the full 18+18-cell grid in
`reports/confidence_grid_u2.json` and PLAN.md's Phase 7 U2 entry.)

Cross-check against S4's original 500-trial numbers: every cell above is
within (or immediately adjacent to) S4's own 500-trial measurement's
sampling noise -- e.g. two-sample n=50/10%-effect/confidence=0.98 was
83.4% in S4's single run, now measured at 81.25% [79.48,82.90]% at 2,000
trials, matching Phase 6 T4's independent finding that the 83.4% reading
was itself on the high side of noise (T4's own re-measurements landed at
79.6%/81.0%, averaging ~81.3% -- this grid's 81.25% point estimate lands
almost exactly on that average). The re-measurement did not overturn any
prior finding; it tightened them.

**The decisive new fact, not visible from two-sample data alone**: paired
mode's power for a 10% effect is already NEAR-CEILING at confidence=0.98
(99.45% at n=30, 100.0% at n=50) and stays there even at confidence=0.99
(98.80% at n=30, 100.0% at n=50) -- tightening confidence all the way to
0.99 costs paired mode less than 1 percentage point of power at n=30 and
literally nothing at n=50, because pairing's variance cancellation already
puts the true effect many standard errors from zero at this sample size.
Two-sample's profile is the opposite: the SAME tightening (0.98 -> 0.99)
costs a real 8.70-point drop at n=30 (57.80% -> 49.10%) and, more
importantly, drops n=50's power BELOW the project's own 80%-power
"reliable detection" bar (81.25% -> 74.20%) -- reproducing, with a tighter
CI, the exact criterion-2 failure that got confidence=0.99 rejected by S4
in the first place.

**DECISION: confidence stays at 0.98 -- the value does NOT change, but the
reasoning now rests on BOTH modes, not just two-sample.** Two independent
arguments, each sufficient alone: (1) there is no real headroom to buy by
tightening further on the paired (now-default) path -- it is already
essentially saturated at 0.98, so 0.99's FPR improvement there (1.40% ->
0.90% at n=30) would be bought for almost nothing, but also cannot be
used to justify a broader tightening; (2) tightening WOULD cost real,
meaningful power on the two-sample path, which remains a live, real path
(every run with no resolvable pairing key, insufficient overlap, or an
explicit `--mode two-sample`) -- and that cost would push exactly the
metric (n=50/10%-effect power) that S4's own criterion 2 was built to
protect below its own 80% bar. Raising the shared confidence to 0.99 would
therefore optimize for the path that needs it least (paired, already >99%
power at 0.98) at the direct expense of the path that needs it most
(two-sample, already only marginally-to-not reliable). Since this package
ships ONE `DEFAULT_CONFIDENCE` shared by both `evaluate_regression` and
`evaluate_regression_paired` (no per-mode confidence parameter exists
today), the asymmetric evidence argues for leaving the shared constant
where it already sits, not moving it toward either mode's individual
optimum. A genuinely BETTER long-term design -- a paired-mode-specific,
tighter confidence default, decoupled from two-sample's -- is a real,
honest architectural option this grid surfaces (paired mode's FPR could
likely go to 0.99 or beyond with near-zero power cost) but is a NEW
capability (a second, mode-specific default), not a re-tuning of the
existing single constant this work item was scoped to decide on; noted as
a candidate for a future work item, not implemented here. See
`scripts/measure_regression_confidence_grid.py` and PLAN.md's Phase 7 U2
entry for the full 18+18-cell grid, both Wilson CIs, and this reasoning in
full."""
DEFAULT_MIN_EFFECT_USD = 0.0001
"""A tenth of a cent per invocation. Below this, an "increase" is not worth
failing a build over even if statistically real -- see module docstring's
practical-significance discussion."""
DEFAULT_MIN_EFFECT_PCT = 5.0
"""5% relative increase in mean per-invocation cost. OR'd with
DEFAULT_MIN_EFFECT_USD (see evaluate_regression) -- either floor clearing is
enough, since a small percentage on a large base cost and a large percentage
on a tiny base cost are both real signals a single fixed dollar floor alone
would miss in one direction or the other."""
DEFAULT_N_BOOT = 10_000
DEFAULT_SEED = 42
"""Hardcoded default per this project's determinism convention (rule 40) --
the same two input distributions always produce the exact same CI and
verdict, run to run, unless the caller explicitly asks for a different seed."""

ACHIEVED_POWER_TARGET = 0.80
"""The detection-rate bar Phase 4 R4's "achieved power" figure is computed
against -- reuses B4's own "reliable" convention EXACTLY (Phase 3 B4:
"reliability bar set at >=80% detection... a standard, defensible
power-analysis convention"), not re-derived or redefined here."""

RegressionStatus = Literal["pass", "regression", "insufficient_data"]
RegressionMethod = Literal["two_sample", "paired"]
"""Which statistic ``RegressionCheckResult`` was computed with --
``"two_sample"`` (``evaluate_regression``, the original Phase 2 W4 method:
two independent samples) or ``"paired"`` (``evaluate_regression_paired``,
Phase 3 B4: per-key cost deltas bootstrapped directly -- see module
docstring)."""


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (matches numpy.percentile's default
    'linear' method) over an already-sorted sequence. ``q`` in [0, 100].
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("cannot take a percentile of an empty sequence")
    if n == 1:
        return sorted_values[0]
    if q <= 0:
        return sorted_values[0]
    if q >= 100:
        return sorted_values[-1]
    idx = (q / 100) * (n - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return sorted_values[int(idx)]
    frac = idx - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * frac


# --- Anti-conservatism at small n: BCa/studentized assessment (Phase 4 R4, 4.5) --
#
# B4's grid measured the percentile bootstrap's own false-positive rate as
# ANTI-conservative at small n -- 5.0% at n=10, 3.5% at n=25, vs a ~2.5%
# nominal one-sided expectation (elevated, not merely noisy: B4's own note
# ties this to "small-sample bootstrap CI coverage is known to degrade
# there"). This section assesses whether a BCa (bias-corrected and
# accelerated) or studentized bootstrap would fix it, per this work item's
# own instruction to engage with the statistics for real rather than
# hand-wave a "known limitation."
#
# BCa was IMPLEMENTED (as a throwaway experiment, not shipped -- see the R4
# session report for the script) and EMPIRICALLY MEASURED against the exact
# same generator/methodology as B4's grid, 300 trials/cell, n_boot=1000:
#
#     n     percentile FPR   BCa FPR
#     10    6.00% (18/300)   5.33% (16/300)
#     25    3.00% (9/300)    3.33% (10/300)
#
# NO measurable improvement -- the two methods are statistically
# indistinguishable at this trial count (BCa is marginally BETTER at n=10 and
# marginally WORSE at n=25, both well within sampling noise for 300 trials).
# This matches the theoretical expectation, not just a lucky/unlucky
# measurement: BCa's two corrections (z0, the bias-correction; a, the
# acceleration/skewness constant) are specifically aimed at bootstrap
# distributions that are BIASED or SKEWED relative to the true parameter --
# which matters most for statistics like medians, ratios, or correlation
# coefficients. A (difference of) SAMPLE MEANS under this project's own
# generator (`max(0.0001, Gauss(mean, sd))`, mean=$0.010 sitting ~6.7 standard
# deviations above the $0.0001 floor -- i.e. the floor essentially never
# binds, so this is very close to plain unclipped Gaussian data) is already
# close to unbiased and symmetric at these sample sizes, so BCa's z0 and a
# correction terms are themselves close to zero, and its adjusted CI ends up
# nearly identical to the plain percentile CI. The small-n anti-conservatism
# B4 measured is better explained as a GENERIC small-sample bootstrap
# coverage phenomenon (present for percentile, BCa, or otherwise, at n this
# small) than as a bias/skewness problem specifically -- BCa targets the
# latter, not the former, which is exactly why it did not move the number.
#
# Studentized (bootstrap-t) was NOT implemented or empirically tested --
# assessed as NOT worth the attempt, for a stated, checkable reason (not
# "seemed hard"): it requires an estimate of the standard error WITHIN each
# individual resample (to studentize that resample's statistic), typically
# via a nested/double bootstrap or a delta-method variance estimate computed
# from the resample's own ~10-25 points. At this sample size, a resample
# drawn with replacement can easily contain many duplicate/near-duplicate
# values purely by chance, producing a spuriously tiny within-resample
# variance estimate and hence an extreme, unstable t-statistic for that
# resample -- a well-documented weakness of the studentized bootstrap at
# small n (Efron & Tibshirani themselves flag this exact instability), and
# the reason it is not generally recommended below roughly n=20-30 in the
# first place -- i.e. exactly the regime this project needs it to help in.
# Building a nested bootstrap (an inner bootstrap loop inside the outer one,
# to estimate each resample's own SE) would also meaningfully violate the
# module's stdlib-only performance assumption (B4's own note: "not remotely
# performance-sensitive enough to need numpy's vectorization" -- true for a
# single flat bootstrap, false for one nested inside another, an
# order-of-magnitude-plus slowdown for the same n_boot).
#
# CONCLUSION: NEITHER fix is implemented. BCa was tried and empirically
# shown not to help; studentized has a clear, stated theoretical reason to
# expect it would make small-n behavior WORSE (more unstable), not better,
# and was judged not worth empirically building out a nested bootstrap to
# confirm what the literature already predicts. This remains a real, honest
# limitation of the gate at small n -- not fixed this phase, and not
# expected to be cheaply fixable via either of these two standard bootstrap
# refinements. A genuinely different approach (e.g. a parametric/normal-
# theory CI as a small-n fallback, at the cost of a distributional
# assumption this package has otherwise avoided) is the more promising
# direction if this is revisited -- noted as a real Phase 5 candidate, not
# promised.


# --- Achieved statistical power (Phase 4 R4) --------------------------------
#
# The bootstrap tests above have no closed-form power formula (unlike a
# t-test). What follows is a PRINCIPLED APPROXIMATION, stated plainly as
# such, not an exact calculation: it treats the percentile-bootstrap CI as
# asymptotically equivalent to a normal-theory Wald CI centered at the
# observed difference/delta mean with standard error ``SE`` -- the same
# CLT-convergence argument the module docstring's own ``min_n=30``
# justification already leans on (a percentile-bootstrap CI's coverage
# converges to the normal-theory CI's as n grows). Under that approximation,
# the minimum detectable effect (MDE) at one-sided significance level
# ``alpha`` and power ``power`` is the textbook formula:
#
#     MDE = (z_{1-alpha} + z_{power}) * SE
#
# where ``z_p`` is the standard normal quantile (probit) function.
#
# This module's bootstrap CI is a TWO-SIDED (1-confidence) percentile
# interval whose LOWER bound is then used as a ONE-SIDED test (see
# ``evaluate_regression``'s ``statistically_significant = ci_lower > 0.0``)
# -- so the test's TRUE one-sided alpha is ``(1 - confidence) / 2``, NOT
# ``1 - confidence``. This is not a guess: Phase 2/B4's own measured
# false-positive rates (~2.0-2.5% at confidence=0.95, large n) match
# ``(1 - 0.95) / 2 = 0.025`` exactly, not ``1 - 0.95 = 0.05`` -- confirmed
# against real prior measurements, not assumed (see ``_one_sided_alpha``).
#
# ACCURACY, validated against B4/R2's own empirically-measured grid
# (``scripts/measure_regression_power.py``'s MEASURED GRID; generator:
# BASE_MEAN=$0.010, BASE_SD=$0.0015, sd scaling ``sd * (1 + effect)`` under a
# true effect -- the exact generator the grid was measured against).
# Predicted (this approximation) vs. measured detection rate:
#
#     n     effect%   predicted   measured   |diff|
#     10    10%       0.294       0.315      0.021
#     25    5%        0.209       0.270      0.061
#     25    10%       0.611       0.690      0.079
#     50    5%        0.369       0.385      0.016
#     50    10%       0.887       0.870      0.017
#     100   10%       0.994       0.995      0.001
#     250   10%       ~1.000      1.000      0.000
#
# See ``tests/test_regression.py``'s
# ``test_achieved_power_approximation_matches_measured_grid_within_tolerance``
# for the reproducible, asserted version of this table (same formula, same
# hardcoded measured figures, real tolerance check). The approximation is
# good to within ~2-8 percentage points across this range -- worst at n=25
# (where it UNDER-predicts by 6-8 points, i.e. it is CONSERVATIVE there, not
# overconfident) and excellent (<2 points) at n=50 and above. This is well
# within what the approximation actually needs to do here: give an honestly-
# labeled "you probably can't reliably detect anything smaller than about
# $X" figure from THIS run's own data, not a precision statistical claim --
# it is never presented as exact.


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erf`` (exact closed form, stdlib --
    no approximation needed in this direction)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inverse_normal_cdf(p: float) -> float:
    """Standard normal quantile function (probit), pure stdlib.

    Uses Peter Acklam's rational approximation (accurate to ~1.15e-9 over
    the full (0, 1) domain), followed by one step of Halley's-method
    refinement against the EXACT ``_normal_cdf`` above -- cheap (one extra
    ``erf`` call) and pushes the result to effectively machine precision
    (~1e-15), removing any meaningful residual error from the rational
    approximation alone. No numpy/scipy dependency needed for this (see
    module docstring's stdlib-only rationale) -- ``scipy.stats.norm.ppf``
    would otherwise be the obvious tool.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"_inverse_normal_cdf requires p in (0, 1), got {p!r}")

    # Rational approximation coefficients (Acklam).
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    # One Halley's-method refinement step against the exact CDF.
    e = _normal_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


def _one_sided_alpha(confidence: float) -> float:
    """See the "Achieved statistical power" note above for why this is
    ``(1 - confidence) / 2``, not ``1 - confidence``."""
    return (1.0 - confidence) / 2.0


def _standard_error_two_sample(baseline: Sequence[float], current: Sequence[float]) -> float | None:
    """Wald-approximation standard error of ``mean(current) - mean(baseline)``
    from the OBSERVED sample variance of each group (``Var(mean) = s^2 / n``
    for each independent group; variances add for the difference of two
    independent means) -- the closed-form counterpart of what
    ``bootstrap_diff_of_means`` estimates by resampling, used here for the
    power approximation. Returns ``None`` if either group has fewer than 2
    observations (sample variance is undefined for n<2)."""
    if len(baseline) < 2 or len(current) < 2:
        return None
    return math.sqrt(
        statistics.variance(baseline) / len(baseline) + statistics.variance(current) / len(current)
    )


def _standard_error_paired(deltas: Sequence[float]) -> float | None:
    """Wald-approximation standard error of ``mean(deltas)`` -- the
    one-sample analogue of ``_standard_error_two_sample``, for
    ``evaluate_regression_paired``. Returns ``None`` if fewer than 2 deltas
    (sample variance undefined)."""
    if len(deltas) < 2:
        return None
    return statistics.stdev(deltas) / math.sqrt(len(deltas))


def minimum_detectable_effect_usd(
    standard_error: float | None,
    *,
    confidence: float,
    power: float = ACHIEVED_POWER_TARGET,
) -> float | None:
    """The minimum true effect (in USD) this run's bootstrap test could
    reliably (``power``, default ``ACHIEVED_POWER_TARGET``=80%) detect,
    GIVEN the observed ``standard_error`` -- see the "Achieved statistical
    power" note above for the formula, its derivation, and its validated
    accuracy. Returns ``None`` when ``standard_error`` is ``None`` (couldn't
    be estimated -- fewer than 2 samples in a group)."""
    if standard_error is None:
        return None
    z_alpha = _inverse_normal_cdf(1.0 - _one_sided_alpha(confidence))
    z_power = _inverse_normal_cdf(power)
    return (z_alpha + z_power) * standard_error


def _below_floor_warning(
    *,
    min_detectable_effect_usd: float | None,
    min_effect_usd: float,
    min_effect_pct: float,
    mean_baseline: float,
) -> str | None:
    """Phase 4 R4, 4.2: if the caller's configured practical-significance
    floor is below the minimum reliably-detectable effect (4.1), the
    statistical test itself cannot reliably catch a real regression as
    small as what the caller configured as "worth caring about" -- a
    passing/clean verdict at this n/variance should not be read as strong
    evidence of no regression at that floor.

    ``min_effect_usd``/``min_effect_pct`` are OR'd in the real gate (either
    clearing its own floor is enough for practical significance -- see
    ``evaluate_regression``'s own docstring), so the EFFECTIVE configured
    floor is whichever of the two is EASIER to clear, expressed on a common
    USD basis (``min_effect_pct`` converted via ``mean_baseline``).

    Returns ``None`` when ``min_detectable_effect_usd`` is ``None`` (no
    estimate available) or the effective floor already meets/exceeds it (no
    warning needed).
    """
    if min_detectable_effect_usd is None:
        return None
    pct_floor_usd = (min_effect_pct / 100.0) * mean_baseline if mean_baseline > 0.0 else math.inf
    effective_floor_usd = min(min_effect_usd, pct_floor_usd)
    if effective_floor_usd >= min_detectable_effect_usd:
        return None
    return (
        f"the configured practical-significance floor (effectively ${effective_floor_usd:.6f}, "
        f"from min_effect_usd=${min_effect_usd:.6f} OR min_effect_pct={min_effect_pct:.2f}%) is "
        f"BELOW this run's minimum reliably-detectable effect at "
        f"{int(ACHIEVED_POWER_TARGET * 100)}% power (~${min_detectable_effect_usd:.6f}, given the "
        "observed variance and n) -- the statistical test cannot reliably catch a real "
        "regression as small as your configured floor at this sample size. A clean/passing "
        "result here should NOT be read as strong evidence of no regression at your configured "
        "floor -- consider a larger eval set, a lower-variance cost metric, or an explicitly "
        "higher floor."
    )


def bootstrap_diff_of_means(
    baseline: Sequence[float],
    current: Sequence[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float]:
    """Percentile-bootstrap CI on ``mean(current) - mean(baseline)``.

    Returns ``(ci_lower, ci_upper)``. Each of the ``n_boot`` resamples draws
    ``len(baseline)`` values with replacement from ``baseline`` and
    ``len(current)`` values with replacement from ``current`` independently
    (the standard two-sample bootstrap -- never resampling from a pooled
    combination of the two groups, which would assume the null hypothesis
    of "no difference" rather than testing for one).

    Raises ``ValueError`` if either group is empty or ``confidence`` is not
    in (0, 1) -- callers (``evaluate_regression``) are expected to have
    already enforced the minimum-n gate before calling this.
    """
    if not baseline or not current:
        raise ValueError("bootstrap_diff_of_means requires at least one value in each group")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")

    rng = random.Random(seed)
    n_b = len(baseline)
    n_c = len(current)
    diffs = [0.0] * n_boot
    for i in range(n_boot):
        b_mean = statistics.fmean(rng.choices(baseline, k=n_b))
        c_mean = statistics.fmean(rng.choices(current, k=n_c))
        diffs[i] = c_mean - b_mean
    diffs.sort()

    alpha = 1.0 - confidence
    ci_lower = _percentile(diffs, 100 * alpha / 2)
    ci_upper = _percentile(diffs, 100 * (1 - alpha / 2))
    return ci_lower, ci_upper


@dataclass(frozen=True)
class RegressionCheckResult:
    """The full result of one ``evaluate_regression`` call -- every field
    that must be reported in the CLI's output every run (n, CI bounds,
    observed effect size), not only on failure.
    """

    status: RegressionStatus
    n_baseline: int
    n_current: int
    mean_baseline: float
    mean_current: float
    effect_usd: float
    effect_pct: float | None
    """None when mean_baseline is exactly 0.0 (percent-of-zero is undefined,
    not silently reported as 0% or infinity)."""
    ci_lower: float | None
    ci_upper: float | None
    """Both None when status == "insufficient_data" -- no bootstrap was run."""
    confidence: float
    min_n: int
    min_effect_usd: float
    min_effect_pct: float
    n_boot: int
    seed: int
    statistically_significant: bool
    practically_significant: bool
    method: RegressionMethod = "two_sample"
    """Defaults to "two_sample" so every pre-Phase-3-B4 call site (both in
    this codebase and any external caller constructing/consuming this
    dataclass) keeps working unchanged -- only evaluate_regression_paired
    sets "paired"."""
    min_detectable_effect_usd: float | None = None
    """Phase 4 R4, 4.1: the minimum effect this run's bootstrap test could
    reliably (``power_target``) detect, given THIS run's observed variance
    and n -- see the module's "Achieved statistical power" note. ``None``
    when it could not be estimated (fewer than 2 samples in a group)."""
    min_detectable_effect_pct: float | None = None
    """Same as ``min_detectable_effect_usd``, expressed as a percentage of
    ``mean_baseline`` -- ``None`` if that value is ``None`` OR
    ``mean_baseline == 0.0`` (percent-of-zero undefined, same convention as
    ``effect_pct``)."""
    power_target: float = ACHIEVED_POWER_TARGET
    """The power level ``min_detectable_effect_usd``/``_pct`` were computed
    at -- always ``ACHIEVED_POWER_TARGET`` (80%) today, but carried as a
    field (not just a module constant) so a future caller-configurable
    power target doesn't require a schema change."""
    power_warning: str | None = None
    """Phase 4 R4, 4.2: populated when the configured practical-significance
    floor is below ``min_detectable_effect_usd`` -- see
    ``_below_floor_warning``. ``None`` when no warning applies."""

    def _power_line(self) -> str:
        """The "achieved power" line -- printed on EVERY run (pass, fail,
        or insufficient_data), per 4.1's own requirement, not only on
        failure."""
        if self.min_detectable_effect_usd is None:
            return (
                "  achieved power: cannot be estimated this run (fewer than 2 samples in at "
                "least one group -- no variance estimate available)."
            )
        pct_str = (
            f" ({self.min_detectable_effect_pct:+.2f}% of mean baseline)"
            if self.min_detectable_effect_pct is not None
            else ""
        )
        return (
            f"  achieved power: minimum reliably-detectable effect at "
            f"{int(self.power_target * 100)}% power, given this run's observed variance/n, is "
            f"~${self.min_detectable_effect_usd:.6f}{pct_str} "
            "[normal approximation to the bootstrap CI -- see _regression.py module docstring "
            "for validated accuracy]"
        )

    def report(self) -> str:
        """Human-readable CLI output. Always includes n, CI bounds (when
        computed), the observed effect size, and (Phase 4 R4) the achieved
        power / minimum-detectable-effect figure -- per this work item's
        statistical-honesty requirement, not only on a failing verdict.
        """
        lines = [
            f"adk-tracegauge check [method={self.method}]: n_baseline={self.n_baseline} "
            f"n_current={self.n_current} (min_n={self.min_n})",
            f"  mean_baseline=${self.mean_baseline:.6f}  mean_current=${self.mean_current:.6f}",
            self._power_line(),
        ]
        if self.status == "insufficient_data":
            lines.append(
                f"  INSUFFICIENT DATA: each group needs >= {self.min_n} invocations for a "
                "statistically meaningful bootstrap CI (see adk_tracegauge._regression "
                "module docstring for the n>=30 rationale) -- refusing to emit a verdict."
            )
            return "\n".join(lines)

        effect_pct_str = f"{self.effect_pct:+.2f}%" if self.effect_pct is not None else "n/a"
        lines.append(
            f"  observed effect: {self.effect_usd:+.6f} USD ({effect_pct_str}), "
            f"{int(self.confidence * 100)}% CI [{self.ci_lower:+.6f}, {self.ci_upper:+.6f}] "
            f"(n_boot={self.n_boot}, seed={self.seed})"
        )
        lines.append(
            f"  statistically_significant={self.statistically_significant} "
            f"practically_significant={self.practically_significant} "
            f"(floors: min_effect_usd={self.min_effect_usd:.6f} OR "
            f"min_effect_pct={self.min_effect_pct:.2f}%)"
        )
        if self.power_warning:
            lines.append(f"  WARNING: {self.power_warning}")
        if self.status == "regression":
            lines.append(
                "  REGRESSION: cost increased significantly (CI excludes zero) AND "
                "the increase clears the configured practical-significance floor."
            )
        else:
            lines.append("  PASS: no regression clearing both the statistical and practical bars.")
        return "\n".join(lines)


def evaluate_regression(
    baseline_costs: Sequence[float],
    current_costs: Sequence[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    min_effect_usd: float = DEFAULT_MIN_EFFECT_USD,
    min_effect_pct: float = DEFAULT_MIN_EFFECT_PCT,
    min_n: int = MIN_N_DEFAULT,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> RegressionCheckResult:
    """Runs the full regression gate over two per-invocation cost samples.

    A regression fires (``status="regression"``) only when the increase is
    BOTH statistically significant (bootstrap CI lower bound > 0) AND
    practically significant (clears ``min_effect_usd`` OR ``min_effect_pct``)
    -- see module docstring. Below ``min_n`` in either group, refuses to run
    the bootstrap at all and reports ``status="insufficient_data"``.
    """
    n_baseline = len(baseline_costs)
    n_current = len(current_costs)
    mean_baseline = statistics.fmean(baseline_costs) if baseline_costs else 0.0
    mean_current = statistics.fmean(current_costs) if current_costs else 0.0
    effect_usd = mean_current - mean_baseline
    effect_pct = (effect_usd / mean_baseline * 100.0) if mean_baseline != 0.0 else None

    # Phase 4 R4, 4.1/4.2: computed from THIS run's own observed sample,
    # regardless of status -- see the module's "Achieved statistical power"
    # note. Uses n<2-tolerant helpers so this never raises even when
    # insufficient_data would otherwise short-circuit below.
    standard_error = _standard_error_two_sample(baseline_costs, current_costs)
    min_detectable_effect_usd = minimum_detectable_effect_usd(standard_error, confidence=confidence)
    min_detectable_effect_pct = (
        min_detectable_effect_usd / mean_baseline * 100.0
        if min_detectable_effect_usd is not None and mean_baseline != 0.0
        else None
    )
    power_warning = _below_floor_warning(
        min_detectable_effect_usd=min_detectable_effect_usd,
        min_effect_usd=min_effect_usd,
        min_effect_pct=min_effect_pct,
        mean_baseline=mean_baseline,
    )

    if n_baseline < min_n or n_current < min_n:
        return RegressionCheckResult(
            status="insufficient_data",
            n_baseline=n_baseline,
            n_current=n_current,
            mean_baseline=mean_baseline,
            mean_current=mean_current,
            effect_usd=effect_usd,
            effect_pct=effect_pct,
            ci_lower=None,
            ci_upper=None,
            confidence=confidence,
            min_n=min_n,
            min_effect_usd=min_effect_usd,
            min_effect_pct=min_effect_pct,
            n_boot=n_boot,
            seed=seed,
            statistically_significant=False,
            practically_significant=False,
            min_detectable_effect_usd=min_detectable_effect_usd,
            min_detectable_effect_pct=min_detectable_effect_pct,
            power_warning=power_warning,
        )

    ci_lower, ci_upper = bootstrap_diff_of_means(
        baseline_costs, current_costs, confidence=confidence, n_boot=n_boot, seed=seed
    )
    statistically_significant = ci_lower > 0.0
    practically_significant = abs(effect_usd) >= min_effect_usd or (
        effect_pct is not None and abs(effect_pct) >= min_effect_pct
    )
    is_regression = statistically_significant and practically_significant

    return RegressionCheckResult(
        status="regression" if is_regression else "pass",
        n_baseline=n_baseline,
        n_current=n_current,
        mean_baseline=mean_baseline,
        mean_current=mean_current,
        effect_usd=effect_usd,
        effect_pct=effect_pct,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        confidence=confidence,
        min_n=min_n,
        min_effect_usd=min_effect_usd,
        min_effect_pct=min_effect_pct,
        n_boot=n_boot,
        seed=seed,
        statistically_significant=statistically_significant,
        practically_significant=practically_significant,
        method="two_sample",
        min_detectable_effect_usd=min_detectable_effect_usd,
        min_detectable_effect_pct=min_detectable_effect_pct,
        power_warning=power_warning,
    )


def bootstrap_mean_of_paired_deltas(
    deltas: Sequence[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float]:
    """Percentile-bootstrap CI on ``mean(deltas)``, for an already-PAIRED
    sample of per-key cost differences (``current[key] - baseline[key]``,
    one delta per matched key -- see ``evaluate_regression_paired``).

    This is a DIFFERENT bootstrap from ``bootstrap_diff_of_means``: there is
    only ONE sequence here (the deltas), and each resample draws ``len(deltas)``
    deltas with replacement and records their mean -- never two independent
    resamples of a baseline/current pair. This is the standard paired
    bootstrap prescription (Efron & Tibshirani, ch. 6): pairing collapses
    "two groups" into "one sequence of differences" *before* any resampling
    happens, which is exactly why it removes between-key (e.g.
    between-eval-case) variance that the unpaired two-sample bootstrap in
    ``bootstrap_diff_of_means`` cannot -- see the module docstring's Phase 3
    B4 note and the power-grid comparison in
    ``scripts/measure_regression_power.py``.

    Raises ``ValueError`` if ``deltas`` is empty or ``confidence`` is not in
    (0, 1) -- callers (``evaluate_regression_paired``) are expected to have
    already enforced the minimum-n gate before calling this.
    """
    if not deltas:
        raise ValueError("bootstrap_mean_of_paired_deltas requires at least one delta")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")

    rng = random.Random(seed)
    n = len(deltas)
    means = [0.0] * n_boot
    for i in range(n_boot):
        means[i] = statistics.fmean(rng.choices(deltas, k=n))
    means.sort()

    alpha = 1.0 - confidence
    ci_lower = _percentile(means, 100 * alpha / 2)
    ci_upper = _percentile(means, 100 * (1 - alpha / 2))
    return ci_lower, ci_upper


def evaluate_regression_paired(
    baseline_costs: Sequence[float],
    current_costs: Sequence[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    min_effect_usd: float = DEFAULT_MIN_EFFECT_USD,
    min_effect_pct: float = DEFAULT_MIN_EFFECT_PCT,
    min_n: int = MIN_N_DEFAULT,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> RegressionCheckResult:
    """Paired-comparison variant of ``evaluate_regression`` (Phase 3 B4).

    ``baseline_costs[i]`` and ``current_costs[i]`` MUST already be aligned --
    i.e. index ``i`` in both sequences refers to the SAME underlying eval
    case/key in both runs. ``_cli.py``'s ``--mode paired`` builds this
    alignment from ``SnapshotRecord.session_id`` (present only when the
    caller's own eval harness pinned a stable, caller-chosen ``session_id``
    per eval case -- see ``snapshot.py`` and ``_plugin.py``'s
    ``record_session``); this function itself does no key-matching and
    trusts its caller entirely, raising ``ValueError`` on a length mismatch
    since a silent zip-truncation would quietly mispair a real key's cost
    against an unrelated one.

    Statistically: computes ``delta[i] = current_costs[i] - baseline_costs[i]``
    for every ``i``, then runs ``bootstrap_mean_of_paired_deltas`` on the
    deltas directly (a ONE-sample bootstrap on the mean delta, not the
    TWO-sample bootstrap ``evaluate_regression`` runs) -- this is the whole
    reason a valid pairing is more powerful: per-eval-case variance (some
    cases are just inherently pricier than others) cancels out in the
    subtraction *before* any resampling, instead of contributing noise to
    two separately-resampled group means. Same significance framework
    otherwise: statistically significant iff the CI lower bound on the mean
    delta is > 0; practically significant iff the observed mean delta clears
    ``min_effect_usd`` OR ``min_effect_pct`` (percent relative to the mean
    baseline cost, same denominator convention as ``evaluate_regression``);
    a regression fires only when both hold. Below ``min_n`` pairs, refuses to
    run the bootstrap and reports ``status="insufficient_data"``, exactly as
    ``evaluate_regression`` does below ``min_n`` per group.
    """
    if len(baseline_costs) != len(current_costs):
        raise ValueError(
            "evaluate_regression_paired requires baseline_costs and current_costs to be the "
            f"same length (already aligned by key) -- got {len(baseline_costs)} vs "
            f"{len(current_costs)}. Build the aligned pair lists from a shared key "
            "(e.g. SnapshotRecord.session_id) before calling this function."
        )

    n_pairs = len(baseline_costs)
    mean_baseline = statistics.fmean(baseline_costs) if baseline_costs else 0.0
    mean_current = statistics.fmean(current_costs) if current_costs else 0.0
    deltas = [c - b for b, c in zip(baseline_costs, current_costs, strict=True)]
    effect_usd = statistics.fmean(deltas) if deltas else 0.0
    effect_pct = (effect_usd / mean_baseline * 100.0) if mean_baseline != 0.0 else None

    # Phase 4 R4, 4.1/4.2: see the identical note in evaluate_regression --
    # the paired analogue uses the one-sample SE of the deltas directly.
    standard_error = _standard_error_paired(deltas)
    min_detectable_effect_usd = minimum_detectable_effect_usd(standard_error, confidence=confidence)
    min_detectable_effect_pct = (
        min_detectable_effect_usd / mean_baseline * 100.0
        if min_detectable_effect_usd is not None and mean_baseline != 0.0
        else None
    )
    power_warning = _below_floor_warning(
        min_detectable_effect_usd=min_detectable_effect_usd,
        min_effect_usd=min_effect_usd,
        min_effect_pct=min_effect_pct,
        mean_baseline=mean_baseline,
    )

    if n_pairs < min_n:
        return RegressionCheckResult(
            status="insufficient_data",
            n_baseline=n_pairs,
            n_current=n_pairs,
            mean_baseline=mean_baseline,
            mean_current=mean_current,
            effect_usd=effect_usd,
            effect_pct=effect_pct,
            ci_lower=None,
            ci_upper=None,
            confidence=confidence,
            min_n=min_n,
            min_effect_usd=min_effect_usd,
            min_effect_pct=min_effect_pct,
            n_boot=n_boot,
            seed=seed,
            statistically_significant=False,
            practically_significant=False,
            method="paired",
            min_detectable_effect_usd=min_detectable_effect_usd,
            min_detectable_effect_pct=min_detectable_effect_pct,
            power_warning=power_warning,
        )

    ci_lower, ci_upper = bootstrap_mean_of_paired_deltas(
        deltas, confidence=confidence, n_boot=n_boot, seed=seed
    )
    statistically_significant = ci_lower > 0.0
    practically_significant = abs(effect_usd) >= min_effect_usd or (
        effect_pct is not None and abs(effect_pct) >= min_effect_pct
    )
    is_regression = statistically_significant and practically_significant

    return RegressionCheckResult(
        status="regression" if is_regression else "pass",
        n_baseline=n_pairs,
        n_current=n_pairs,
        mean_baseline=mean_baseline,
        mean_current=mean_current,
        effect_usd=effect_usd,
        effect_pct=effect_pct,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        confidence=confidence,
        min_n=min_n,
        min_effect_usd=min_effect_usd,
        min_effect_pct=min_effect_pct,
        n_boot=n_boot,
        seed=seed,
        statistically_significant=statistically_significant,
        practically_significant=practically_significant,
        method="paired",
        min_detectable_effect_usd=min_detectable_effect_usd,
        min_detectable_effect_pct=min_detectable_effect_pct,
        power_warning=power_warning,
    )


__all__ = [
    "ACHIEVED_POWER_TARGET",
    "DEFAULT_CONFIDENCE",
    "DEFAULT_MIN_EFFECT_PCT",
    "DEFAULT_MIN_EFFECT_USD",
    "DEFAULT_N_BOOT",
    "DEFAULT_SEED",
    "MIN_N_DEFAULT",
    "RegressionCheckResult",
    "RegressionMethod",
    "RegressionStatus",
    "bootstrap_diff_of_means",
    "bootstrap_mean_of_paired_deltas",
    "evaluate_regression",
    "evaluate_regression_paired",
    "minimum_detectable_effect_usd",
]
