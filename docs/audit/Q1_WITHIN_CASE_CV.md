# Q1 — within-case CV, measured (paired mode's actual governing quantity)

**Trigger**: AD2 measured across-case cost CV (0.983) by running each of 36
evalset cases ONCE. But paired mode — the shipped default — cancels
across-case heterogeneity; its power depends on WITHIN-case CV (cost
variability for repeated runs of the *same* case), which AD2 explicitly
flagged as unmeasured. This closes that gap.

## 1.2 — Method

`scripts/measure_within_case_cv_ollama.py` ran the identical 36-case
evalset (`reports/ad2_evalset.json`) TWICE against the same local model
(`ollama_chat/qwen2.5:7b`), through the shipped `TraceGaugeUsagePlugin`/
`UsageStore`/`snapshot` pipeline (same synthetic price override as AD2).
Each case matched across both runs by `case_id` (distinct session_ids,
`-runA`/`-runB` suffixes stripped before grouping — no session-service
collision). Pooled within-case standard deviation estimated via the
standard duplicate-measurement formula: `Var(Delta_i) = 2*sigma^2` for
independent draws, so `sigma^2 = mean(Delta_i^2) / 2`.

**Bias check, run first, not assumed**: `mean(delta) = mean(runB - runA) =
-$0.000001`, t-stat = -0.189 — not significant. runA and runB are
exchangeable (no detectable systematic drift, e.g. Ollama warm-up/caching
effects between passes), which the pooled-variance estimator's validity
depends on.

**Result**: pooled within-case sd = $0.000029, mean cost = $0.000182,
**within-case CV = 0.1566**.

**Skewness NOT reported** — see the script's own docstring: the
difference of two iid draws (A-B) is symmetric around zero by
construction, regardless of the underlying distribution's own skewness.
Two repeats per case give a real variance estimate but no meaningful
third-moment (skewness) estimate; that would need >=3 repeats.

## 1.3 — Where this lands on the power grid, measured directly (not interpolated)

`scripts/measure_q1_within_case_power.py` re-used AD1's own paired-mode
generator/methodology (`generate_paired_pair_cv`, `bootstrap_mean_of_
paired_deltas`, confidence=0.98, n_boot=1,000 validated against 10,000 at
98% agreement) at the ACTUAL measured CV (0.1566) and the ACTUAL n values
that matter (n=30, the shipped `min_n`; n=36, this evalset's real size) —
not a linear interpolation between AD1's {0.1, 0.2, 0.4, 0.6, 1.0} grid
points, an actual measurement.

| n | effect | power [Wilson 95% CI] |
|---|---|---|
| 30 | 10% | **28.45% [26.52%, 30.47%]** (569/2,000) |
| 36 | 10% | **32.25% [30.24%, 34.33%]** (645/2,000) |
| 30 | 25% | 92.25% [91.00%, 93.34%] (1,845/2,000) |
| 36 | 25% | 96.35% [95.44%, 97.09%] (1,927/2,000) |

Raw data: `reports/q1_within_case_cv.json`, `reports/q1_within_case_power.json`.

**Sanity check**: this measured point (CV=0.1566 → 28.45% power at n=30)
sits between AD1's own grid points CV=0.1 (58.35%) and CV=0.2 (18.55%) —
consistent with a monotonically decreasing, steep power curve in this
region, not an outlier or a measurement artifact.

## 1.4 — Sampling settings

No `generate_content_config` override anywhere in this pipeline
(`LlmAgent.generate_content_config` field defaults to `None`, confirmed by
reading the field default directly) and `ollama show qwen2.5:7b
--modelfile` carries zero `PARAMETER` lines (confirmed live) — so Ollama's
own server default applies: **temperature=0.8**, a real, non-zero sampling
temperature. This is NOT a deterministic/greedy decode, so this
measurement does not have the specific failure mode Q1.4 warned about
(a temperature=0 setup would have suppressed real sampling noise and
understated variance). It may still not match a real hosted model's own
default temperature/top_p — a separate, already-flagged (AD2.3)
representativeness question this does not resolve.

## 1.6 — STOP-worthy finding, reported plainly

**At the shipped `min_n=30`, paired mode — the DEFAULT mode whenever a
pairing key resolves — detects a true 10% cost regression only 28.45% of
the time at this evalset's real, measured within-case CV.** At the actual
evalset size (n=36) it is 32.25%. Both are far below the project's own
80%-power "reliable" bar, and both are roughly 1/3 of the previously
published, unqualified "99.22% at n=30" figure.

This directly contradicts the currently-LIVE published README (the fix is
in unmerged PR #23/#24 — see `AUTONOMOUS_RUN.md`'s STOP report) — a real
S2 condition, not a hypothetical one. **Domain of validity, stated
plainly**: one evalset (36 cases), one local model (`qwen2.5:7b`), two
repeats per case. Not a general claim about real per-invocation ADK cost
variance — but it is a real, mechanism-consistent, sanity-checked number
for the one case actually measured, and the shipped default's own
flagship power claim does not survive contact with it.
