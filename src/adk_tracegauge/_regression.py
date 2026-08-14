"""adk_tracegauge/_regression.py — Bootstrap-CI cost regression detection.

Core statistics for ``tracegauge check`` (see ``_cli.py``). Given two
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
rule of thumb, not a guess)."""

DEFAULT_CONFIDENCE = 0.95
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

RegressionStatus = Literal["pass", "regression", "insufficient_data"]


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

    def report(self) -> str:
        """Human-readable CLI output. Always includes n, CI bounds (when
        computed), and the observed effect size -- per this work item's
        statistical-honesty requirement, not only on a failing verdict.
        """
        lines = [
            f"tracegauge check: n_baseline={self.n_baseline} n_current={self.n_current} "
            f"(min_n={self.min_n})",
            f"  mean_baseline=${self.mean_baseline:.6f}  mean_current=${self.mean_current:.6f}",
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
    )


__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_MIN_EFFECT_PCT",
    "DEFAULT_MIN_EFFECT_USD",
    "DEFAULT_N_BOOT",
    "DEFAULT_SEED",
    "MIN_N_DEFAULT",
    "RegressionCheckResult",
    "RegressionStatus",
    "bootstrap_diff_of_means",
    "evaluate_regression",
]
