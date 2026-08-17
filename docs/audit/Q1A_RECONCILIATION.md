# Q1a — reconciling the two paired-mode harnesses

**Trigger**: Q1's real measurement (28.45%/32.25% power at n=30/36, 10%
effect) sits ~3.5x below the published grid's 99.22%/99.45% at the same
n/effect, both nominally in a "3-20%" within-case CV band. GG asked
whether this is an effect-model difference (report both, don't pick one),
a CV-definition difference, or a bug in one harness — and blocked the
README correction until resolved.

## 1a.2 — Effect model: IDENTICAL, not the source of the gap

Quoted directly from both generators:

```python
# original (generate_case_correlated_pair, measure_regression_power.py)
effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
current = [max(0.0001, rng.gauss(d + effect_usd, CASE_CORRELATED_WITHIN_CASE_SD))
           for d in case_levels]

# mine (generate_paired_pair_cv, measure_power_by_cv_grid.py)
effect_usd = CASE_CORRELATED_BASE_MEAN * (effect_pct / 100.0)
current = [max(0.0001, rng.gauss(d + effect_usd, cv * (d + effect_usd)))
           for d in case_levels]
```

Both add the identical flat `effect_usd` to every case's mean — a
case-correlated regression (Q1a.6: "a model swap or a per-call tool
addition moves every case together"), never independently per case. This
axis is not where the harnesses diverge.

## 1a.3 — CV definition: this is where they diverge, and a false start

The original has no explicit CV parameter — it uses a FIXED ABSOLUTE
dollar SD (`CASE_CORRELATED_WITHIN_CASE_SD = 0.0008`), implying a CV that
VARIES 3.3%–20% depending on case level (`0.0008/case_level`, case_level
in [0.004, 0.024]) — this is Q1a.1's "3-20% band." Mine uses an explicit
`cv` parameter PROPORTIONAL to each case's own level (`cv * d`).

**First attempt at reconciling these (not published, corrected before
landing anywhere) was itself wrong**: plugged Q1's real measured dollar SD
($0.0000285, from an evalset with mean cost ~$0.000182) directly into a
constant-absolute-SD generator whose case levels are Uniform(0.004,
0.024) — a completely different dollar scale (~$0.014 mean, ~78x larger).
Result: 100% power at every cell. This number is an ARTIFACT of the scale
mismatch — the same $0.0000285 that is 15.66% of Q1's real mean cost
becomes a negligible ~0.2% of the synthetic case levels' mean once
transplanted — not a real finding about the gate's power. Caught before
being reported anywhere, by checking it against a scale-invariant
re-derivation (below), same discipline as every other self-audit in this
investigation (AB1, AC1).

## 1a.4 — Phase 3 B4 cross-check

`tests/test_regression_power.py`'s 200/200 (n=25, 10% effect) result
calls `generate_case_correlated_pair` directly — the same constant-
absolute-SD generator as the published grid. No third generator variant
exists anywhere in this codebase's history; the proportional-CV generator
is new to this session's own AD1 CV-sweep work.

## 1a.7 — the correct reconciliation: scale-invariant, apples-to-apples

CV is dimensionless and portable across dollar scales; raw dollar SDs are
not. `scripts/measure_q1a_reconciliation.py` compares BOTH cases inside
the SAME already-validated proportional-CV generator
(`generate_paired_pair_cv`) — no raw dollar values, no scale mismatch:

1. **The original's own implied average CV**: `0.0008 / mean(0.004,
   0.024) = 0.0008 / 0.014 ≈ 0.0571`.
2. **Q1's real measured within-case CV**: `0.1566`.

| CV | n | effect | power [Wilson 95% CI] |
|---|---|---|---|
| 0.0571 (original's own implied avg) | 30 | 10% | 96.90% [96.05%, 97.57%] |
| 0.0571 | 36 | 10% | 98.20% [97.52%, 98.70%] |
| 0.0571 | 30 | 25% | 100.00% |
| 0.1566 (Q1's real measured) | 30 | 10% | **28.00% [26.08%, 30.01%]** |
| 0.1566 | 36 | 10% | **34.00% [31.96%, 36.10%]** |
| 0.1566 | 30 | 25% | 91.05% [89.72%, 92.22%] |
| 0.1566 | 36 | 25% | 95.45% [94.45%, 96.28%] |

n_boot=1,000 validated against 10,000 first (97-100% agreement). Raw
data: `reports/q1a_reconciliation.json`.

**At the original's own implied average CV, the proportional-CV model
reproduces ~97-98% power — consistent with (not identical to, but the
same order as) the published 99.22%/99.45%.** The residual few points of
difference is expected: "average implied CV" is an approximation of a
structurally different (constant-absolute-SD) model, not an exact
restatement of it — the two models distribute noise across cases
differently even at the same nominal average. Close enough to confirm the
harnesses are NOT in conflict once compared on level ground.

**At Q1's real measured CV, power is 28.00%/34.00%** — matching Q1's
original measurement (28.45%/32.25%) within sampling noise (different
seeds/trial counts, same underlying quantity).

## Conclusion

**Neither original harness is wrong.** The apparent "3.5x gap, one must
be broken" framing (Q1a.1) does not survive a scale-correct comparison:
the published grid's own noise assumption corresponds to an average CV of
~5.7%; Q1's real measurement found this evalset/model's actual within-case
CV is ~15.7% — nearly 3x noisier than what the shipped default's own
validation grid assumed. That gap is a REAL FACT about real vs. assumed
variance, not a bug in the measurement of either.

**Per 1a.7**: the thing that needed fixing was my own first-draft
reconciliation attempt (the scale-mismatched 100% figure), which is
corrected here and never published. **Q1's original 28.45%/32.25% figures
stand, now independently validated** rather than retracted — the STOP
condition raised for those numbers was justified and remains so.

**No README correction needed as a result of 1a** — the numbers already
in the README (from Q1, before this reconciliation) were already the
scale-correct ones. This document adds the reconciliation as supporting
evidence, not a number change.
