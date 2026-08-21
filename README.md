# adk-tracegauge

A statistically-validated **CI cost-regression gate** for [Google ADK](https://github.com/google/adk-python) agents: snapshot a real per-invocation **USD cost** distribution from an eval run, and fail the build only when a cost increase is both statistically and practically significant. Also registers as a real per-invocation **PASS/FAIL threshold metric** inside `adk eval` itself. Built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. Raw dollars and tokens, no calibrated bands, no fabricated numbers for unknown models.

[![PyPI](https://img.shields.io/pypi/v/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![CI](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

---

## Try it right now — no ADK app, no API key, no network call

```bash
pip install adk-tracegauge
adk-tracegauge quickstart
```

Two commands, no files to create. This runs a deterministic, in-memory demo agent (bundled with the package — nothing is read from your machine) through a real `InMemoryRunner`, twice, with a deliberate cost regression injected into the second run, then fires the real `adk-tracegauge check` gate against it. **Measured live, not estimated: 78.2s wall-clock from a genuine fresh `pip install --user` on Windows** (the install mode that hits the PATH issue below) **to the printed regression verdict.** Same exact output every run — see `examples/` for the full script this reuses.

## Quickstart: the CI cost-regression gate

```bash
pip install adk-tracegauge
adk-tracegauge snapshot --entrypoint my_eval_suite:run_and_return_store --output baseline.json
adk-tracegauge snapshot --entrypoint my_eval_suite:run_and_return_store --output current.json
adk-tracegauge check --baseline baseline.json --current current.json
```

**If `adk-tracegauge` isn't found right after installing** (`CommandNotFoundException` in PowerShell, "not recognized" in cmd), this is a PATH issue, not a broken install — `pip install --user` (the default outcome of `pip install` outside a venv) puts the console script in a per-user directory Windows doesn't add to PATH automatically. Two fixes, either works:
1. Use `python -m adk_tracegauge` in place of `adk-tracegauge` everywhere in this README — always works, since it only needs `python` itself on PATH (available from `adk-tracegauge>=0.3.1`).
2. Add the script directory Windows already installed to (printed as a `WARNING` during `pip install`, typically `%APPDATA%\Python\PythonXYZ\Scripts` on Windows) to your PATH.

Installing into a virtual environment (`python -m venv`/`uv venv` + activate, then `pip install adk-tracegauge`) avoids this entirely, since an activated venv's `Scripts`/`bin` directory is already on PATH.

`my_eval_suite:run_and_return_store` is a zero-argument callable you already have (it runs your ADK eval — `AgentEvaluator.evaluate()` or your own `Runner` harness — with `TraceGaugeUsagePlugin` wired in; see "What this actually is" below). `adk-tracegauge check` runs a percentile bootstrap on the difference in mean cost and exits with a **real, distinguishable exit code**: `0` pass, `1` regression, `3` insufficient data, `4` pass but underpowered (two-sample mode only — see "Known limitations" below; a real, non-zero exit code your CI should distinguish from a hard failure if it treats any non-zero exit as build-failing). Real output, from a genuine +20%-mean injected regression measured fresh this session (`examples/03_ci_regression_gate.py`, both `snapshot` calls plus `check` itself run as real subprocesses, `google-adk==2.6.3`):

```
adk-tracegauge check [method=two_sample]: n_baseline=40 n_current=40 (min_n=30)
  mean_baseline=$0.008583  mean_current=$0.009998
  achieved power: minimum reliably-detectable effect at 80% power, given this run's observed variance/n, is ~$0.000536 (+6.25% of mean baseline) [normal approximation to the bootstrap CI -- see _regression.py module docstring for validated accuracy]
  observed effect: +0.001415 USD (+16.49%), 98% CI [+0.001019, +0.001801] (n_boot=10000, seed=42)
  statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  WARNING: the configured practical-significance floor (effectively $0.000100, from min_effect_usd=$0.000100 OR min_effect_pct=5.00%) is BELOW this run's minimum reliably-detectable effect at 80% power (~$0.000536, given the observed variance and n) -- the statistical test cannot reliably catch a real regression as small as your configured floor at this sample size. A clean/passing result here should NOT be read as strong evidence of no regression at your configured floor -- consider a larger eval set, a lower-variance cost metric, or an explicitly higher floor.
  REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the configured practical-significance floor.
```
```
$ echo $?
1
```

**Every `adk-tracegauge check` run prints its own "achieved power" figure (Phase 4 R4)** — the minimum effect size the bootstrap test could reliably (80% power) detect given THIS run's actual observed variance and `n`, plus (as shown above) an explicit `WARNING` whenever your configured `--min-effect-usd`/`--min-effect-pct` floor is smaller than that achievable floor — i.e. the gate is telling you, with real numbers from your own run, that it cannot reliably catch a regression as small as what you configured it to care about. See "Known limitations" below.

**Measured this session, not estimated:** the 3 `adk-tracegauge`-specific command lines above took **35.3s wall-clock combined** (11.75s + 11.85s + 11.75s, each dominated by cold `google-adk` import overhead, not by the actual comparison — the bootstrap itself runs in well under a second). A full copy-pasteable GitHub Actions workflow lives at [`docs/ci-snippet.md`](docs/ci-snippet.md).

**Why this is the hero path, not the `adk eval` metric below:** `adk eval`'s own process exit code does not reflect PASSED/FAILED (verified live — see below), so it cannot gate a CI job on its own; and `AgentEvaluator.evaluate()`, ADK's pytest-style harness, has a real, source-confirmed polarity bug that can invert pass/fail for a lower-is-better metric like cost (see "Known limitations"). `adk-tracegauge check` is this package's own code, with its own real exit codes, proven to work standalone — that's the actual, statistically-measured differentiator (see "Known limitations" for the honest caveats on detection power at small `n`, and how `--mode paired` fixes them).

## Shipped default, stated plainly

`adk-tracegauge check` defaults to `--mode auto`, and — as of Phase 7 U1 — that default now **prefers a paired comparison**: whenever a stable per-eval-case key resolves (`eval_case_id` first, recovered via `--eval-history`; `session_id` as a fallback) with at least `--min-n` (30) overlapping cases between baseline and current, paired mode runs. **Only when no such key resolves, or fewer than `min_n` cases overlap, does it automatically fall back to an unpaired two-sample comparison** using the full baseline/current cost distributions — never a mix of the matched subset and the rest. The resolved mode and key are printed on every single run, unconditionally, never silently assumed.

**Which harness yields which key — verified live against the published package, both paths, not assumed:**
| Harness | Key that resolves | How |
|---|---|---|
| `adk eval` CLI (this package's documented quickstart) | `eval_case_id` | `adk-tracegauge snapshot --eval-history <path-to-adk-eval's-.evalset_result.json>` |
| Hand-rolled `Runner`/`InMemoryRunner`, `session_id` pinned by hand (`runner.run_async(session_id=...)`), no `adk eval` involved | `session_id` | Live-captured directly, no `--eval-history` needed |

A hand-rolled harness resolving `key=session_id` is the *expected*, correct outcome for that harness — it is not the `adk eval` path, and does not indicate `eval_case_id` resolution failing there. See `examples/04_paired_mode_via_adk_eval_cli.py` (the real `adk eval` CLI path) and `examples/05_hand_rolled_session_id_pairing.py` (the hand-rolled path) for both, runnable end to end.

At the shipped configuration — `confidence=0.98`, `min_n=30` — here is what that default actually detects, measured by Phase 7 U2's grid (`scripts/measure_regression_confidence_grid.py`, Wilson 95% CIs on every cell; full 18+18-cell table in "Known limitations" below and `reports/confidence_grid_u2.json`), corrected to **5,000 trials/cell** during the Phase 8 FPR-anomaly audit (`docs/audit/FPR_ANOMALY.md`) — trials 0–1,999 are byte-identical to the original 2,000-trial run, extended rather than replaced:

**Paired mode — the default, whenever a pairing key resolves:**
- False-positive rate: **1.46% [1.16%, 1.83%] (73/5,000 trials)**

**Two-sample fallback — what you get when no pairing key resolves, stated separately and not blended into the paired numbers above:**
- False-positive rate: **1.30% [1.02%, 1.65%] (65/5,000 trials)**

**On the two FPR figures above**: an earlier (2,000-trial) measurement reported paired mode's FPR as higher than two-sample's here — an audit (`docs/audit/FPR_ANOMALY.md`) found that comparison was never significance-tested and does not hold up when tested (a two-proportion z-test on this exact cell: z=0.69, p=0.49, not significant); see "Known limitations" below for the full corrected grid and investigation.

### Power depends on your own cost variance — there is no single number

A previous version of this README stated one power figure ("99.22% at n=30") as if it applied universally. It doesn't: that figure assumed one specific cost-variance level, and Phase 9 AC1/AD1 (`docs/audit/AC1_SKEW_SENSITIVITY.md`) found power is highly sensitive to that assumption — a plausible, equally-unmeasured alternative assumption put the same n=30 cell at ~5% power, not 99%. **Replacing one assumed number with another isn't a fix — the honest fix is showing power as a function of the one variable that actually determines it: how much your own per-invocation cost varies.**

**What "CV" means, in plain terms**: the coefficient of variation is your per-invocation cost's standard deviation divided by its mean — in short, how much cost varies from one invocation to the next across your eval set. A CV of 0.1 means invocation costs cluster tightly around the mean (low variability, e.g. near-identical prompts hitting a fixed-length response); a CV of 1.0 means costs vary about as much as the mean itself (high variability, e.g. eval cases spanning very different task complexity or output length). Compute your own: `stdev(costs) / mean(costs)` over a snapshot's `cost_usd` values.

**The two modes need a different CV measured against your data, because pairing cancels a different noise source than two-sample sees.** Two-sample mode's power depends on the raw CV of your per-invocation costs (what you'd compute directly from a `snapshot`'s `cost_usd` values — includes both case-to-case cost differences and run-to-run noise). Paired mode cancels out case-to-case cost differences entirely (that's the whole mechanism pairing relies on — see "Shipped default" above), so its power depends instead on the **within-case** CV — how much cost varies for the *same* eval case across independent runs, a narrower, harder-to-observe-directly quantity.

**Paired mode's own within-case noise has two DIFFERENT plausible shapes, not one — and this README shows both, not a pick.** `docs/audit/Q1A_RECONCILIATION.md` traced an apparent 3.5x gap between this package's own originally-published paired grid (99.22%/99.45% at n=30) and a later real measurement (28.45%/32.25%, same n) back to a genuine difference in what each assumes about how within-case cost noise behaves — not a bug in either:

- **Regime A — fixed absolute dollar noise.** The originally-published grid's own generator: every case gets the SAME dollar amount of noise regardless of that case's own cost level. **This approximates an evalset of near-identical cases** — the same prompt run repeatedly, where response-length noise is roughly constant in dollar terms whether the case is cheap or expensive.
- **Regime B — proportional (CV-scaled) noise.** This package's own CV-sweep table (below): noise scales WITH each case's cost level, so a $0.01 case is not expected to have the same absolute dollar noise as a $0.001 case. **This approximates an evalset of genuinely varying task complexity** — a mix of short factual questions and long-form generation, where cost itself varies a lot case to case and so does the noise around it.

**How to tell which regime your evalset is in, in one sentence**: if your eval cases are variations on essentially one task (same prompt shape, similar expected response length), you're closer to Regime A; if your cases span genuinely different task complexity (some short, some long, different tool-call depth), you're closer to Regime B — and real measurement (below) found a genuinely mixed-complexity evalset lands solidly in Regime B, not Regime A, even though its raw CV number technically fell inside Regime A's own quoted "3-20%" band.

**Regime A — fixed absolute dollar noise, power to detect a true 10% cost regression, confidence=0.98, >=2,000 trials/cell, Wilson 95% CIs** (`scripts/measure_absolute_sd_grid.py`, full grid in `reports/absolute_sd_grid.json`; `$0.0008` is the originally-published grid's own constant, reproduced here at 99.00% vs. the original 99.22% — within sampling noise, confirming this table's harness matches the historical one):

| Absolute SD \ n | 30 | 50 | 100 |
|---|---|---|---|
| $0.0002 | 100.00% [99.81%, 100%] | 100.00% [99.81%, 100%] | 100.00% [99.81%, 100%] |
| $0.0004 | 100.00% [99.81%, 100%] | 100.00% [99.81%, 100%] | 100.00% [99.81%, 100%] |
| $0.0008 (original) | 99.00% [98.46%, 99.35%] | 100.00% [99.81%, 100%] | 100.00% [99.81%, 100%] |
| $0.0016 | 55.80% [53.61%, 57.96%] | 78.30% [76.44%, 80.05%] | 97.90% [97.17%, 98.44%] |
| $0.0032 | 15.35% [13.84%, 17.00%] | 24.05% [22.23%, 25.97%] | 46.35% [44.17%, 48.54%] |

**Regime B — proportional CV, power to detect a true 10% cost regression, confidence=0.98, >=2,000 trials/cell, Wilson 95% CIs** (`scripts/measure_power_by_cv_grid.py`, full grid with CIs in `reports/power_by_cv_grid.json`):

**Two-sample (CV = raw per-invocation cost CV — always this regime, two-sample has no absolute-SD analogue since it doesn't use a case-level structure at all):**

| CV \ n | 30 | 50 | 100 |
|---|---|---|---|
| 0.1 | 91.65% [90.36%, 92.78%] | 99.35% [98.89%, 99.62%] | 100.00% [99.81%, 100%] |
| 0.2 | 35.30% [33.24%, 37.42%] | 55.75% [53.56%, 57.91%] | 85.00% [83.37%, 86.50%] |
| 0.4 | 9.85% [8.62%, 11.23%] | 14.05% [12.60%, 15.64%] | 27.85% [25.93%, 29.86%] |
| 0.6 | 5.65% [4.72%, 6.75%] | 7.60% [6.52%, 8.84%] | 14.50% [13.02%, 16.11%] |
| 1.0 | 4.15% [3.36%, 5.12%] | 4.50% [3.68%, 5.50%] | 8.55% [7.40%, 9.86%] |
| 1.5 | 2.50% [1.90%, 3.28%] | 4.55% [3.72%, 5.55%] | 4.90% [4.04%, 5.94%] |
| 2.0 | 3.30% [2.60%, 4.18%] | 3.40% [2.69%, 4.29%] | 4.20% [3.41%, 5.17%] |

**Paired (CV = within-case cost CV across independent runs of the same eval case):**

| CV \ n | 30 | 50 | 100 |
|---|---|---|---|
| 0.1 | 58.35% [56.18%, 60.49%] | 83.20% [81.50%, 84.77%] | 98.05% [97.35%, 98.57%] |
| 0.2 | 18.55% [16.91%, 20.31%] | 27.35% [25.44%, 29.35%] | 49.45% [47.26%, 51.64%] |
| 0.4 | 8.15% [7.03%, 9.43%] | 8.85% [7.68%, 10.17%] | 13.85% [12.41%, 15.43%] |
| 0.6 | 4.20% [3.41%, 5.17%] | 5.05% [4.17%, 6.10%] | 6.65% [5.64%, 7.83%] |
| 1.0 | 2.60% [1.99%, 3.39%] | 3.60% [2.87%, 4.51%] | 5.00% [4.13%, 6.04%] |
| 1.5 | 3.10% [2.43%, 3.95%] | 3.00% [2.34%, 3.84%] | 3.65% [2.91%, 4.56%] |
| 2.0 | 3.10% [2.43%, 3.95%] | 2.30% [1.73%, 3.05%] | 3.90% [3.14%, 4.84%] |

**CV rows 1.5/2.0 added because a real measurement now exceeds the table's original top row (1.0)** — see the real-hosted-model paragraph below. At CV≥1.5, BOTH modes collapse to near-random-chance power (2–5%) regardless of `n` — extending the table doesn't reveal a recoverable regime at high variance, it confirms there isn't one; `n` alone cannot buy back power once variance dominates the effect size this badly.

**Neither regime table replaces the other — read whichever matches your own evalset's shape, per the one-sentence test above.** They are not competing estimates of the same thing; they are honest answers to two different questions ("what if my noise is a constant dollar amount" vs. "what if my noise scales with cost"), and a workload can genuinely sit in either.

**The strongest thing in this whole section: `adk-tracegauge check`'s own "achieved power" line is correct under EITHER regime, with no regime-guessing required.** It is computed directly from THIS run's own observed variance and `n` (`_regression.py`'s `minimum_detectable_effect_usd`, a normal approximation to the bootstrap CI) — never from an assumed constant, an assumed CV, or a choice between Regime A and B. The two tables above exist to explain the SHAPE of the tradeoff and help you sanity-check whether a given `n` is remotely enough before you run anything; your own run's printed line is the one number that actually applies to you, and it doesn't care which regime you're in because it was never computed from either — it's computed from your real data every time.

**A real, measured within-case CV — Regime B, the shipped default's real-world case.** `scripts/measure_within_case_cv_ollama.py` ran the same 36-case evalset TWICE (real repeats of the SAME cases, not two different evalsets) against `ollama_chat/qwen2.5:7b`, and measured **within-case CV = 0.1566** (pooled duplicate-measurement estimator; bias check t-stat=-0.189, not significant — the two runs are exchangeable). Locating this on the Regime B table above (measured directly at n=30 and n=36, not interpolated — `scripts/measure_q1_within_case_power.py`): **power to detect a true 10% cost regression is 28.45% [26.52%, 30.47%] at n=30 (the shipped `min_n`), and 32.25% [30.24%, 34.33%] at n=36 (this evalset's actual size)** — both far below the 80%-power bar, and roughly 1/3 of a previous, unqualified "99.22%" figure this README used to publish (that original figure was Regime A's own $0.0008 point, not wrong for Regime A, just not universal). 25%-effect power stays strong (92.25%/96.35%). Domain of validity: one evalset, one local model, two repeats per case — see `docs/audit/Q1_WITHIN_CASE_CV.md` for the full investigation, including why skewness could not be estimated from two repeats and what sampling temperature Ollama used. Full reconciliation, including a scale-mismatch false start caught and discarded before publication, in `docs/audit/Q1A_RECONCILIATION.md`.

**The same measurement, repeated against a real hosted model, not just local Ollama.** Measured on the identical 36-case evalset against `gemini-3.5-flash-lite` (a real, paid Gemini API call — `scripts/measure_real_cv_gemini.py`/`measure_within_case_cv_gemini.py`, full detail in `docs/audit/AD2_REAL_CV_MEASUREMENT.md`): **across-case CV 1.2326** (governs the two-sample fallback path) and **within-case CV 0.1307** (governs the shipped paired default). One evalset, one real hosted model — not a general claim about ADK cost variance across workloads, the same caveat as the Ollama measurement above.

The two real-model CVs move in OPPOSITE directions relative to Ollama: across-case is 25.4% HIGHER (worse for two-sample), within-case is 16.6% LOWER (better for paired). **Two-sample is not a usable regression gate at the real measured across-case CV, stated plainly, not softened**: 1.2326 exceeds this table's original top row (1.0), where power was already 4.15–8.55%; the newly-added 1.5/2.0 rows above show it only gets worse, not better, at higher n. A workload whose two-sample fallback is actually exercised (no pairing key resolves) at real-world variance this high will not reliably catch a 10% regression at any evalset size in this table — pairing is required, not merely preferred, for such a workload.

**Paired mode's real-world number, computed directly at CV=0.1307 (not interpolated — `scripts/measure_al1_al2_extended_grids.py`), is the actual deliverable:** power to detect a true 10% cost regression is **37.85% [35.75%, 40.00%] at n=30** (the shipped `min_n`) and **43.50% [41.34%, 45.68%] at n=36** (this evalset's size) — meaningfully higher than Ollama's own real-CV figures (28.45%/32.25%) since the real hosted model is more consistent run-to-run, but still well below the 80%-power bar. At a 25% effect, power is strong either way: **98.00% [97.29%, 98.53%] at n=30**, **99.35% [98.89%, 99.62%] at n=36**. **Stated plainly: at n=30, the shipped default does NOT reliably detect a true 10% cost regression, even at the real, favorable, measured hosted-model variance** — it detects one about 2 times in 5, not 4 times in 5. It reliably detects a 25% regression at any evalset size in this table.

Sampling was not deterministic in either hosted measurement (a temperature=0 setup would understate real run-to-run variance and make the within-case figure a floor — this was checked, not assumed): no client-side `generate_content_config` override was set in either script, and Gemini's own listed default for this model (`temperature=1.0`, confirmed via the free `models.list()` metadata endpoint) is non-zero. This is also directly confirmed empirically — 24 of the 36 within-case cost deltas were genuinely nonzero (dominated by the longer-form B/C task tiers); the trivial-recall tier's near-total run-to-run determinism (10/12 cases byte-identical cost) is a property of those specific short-factual-answer prompts having very low output entropy, not a suppressed-sampling artifact.

**No shipped default changed as a result of this table.** `confidence=0.98` and `min_n=30` stay as shipped — this section replaces a misleading single number with an honest, regime-labeled range, not a new tuning decision. See `docs/audit/AC1_SKEW_SENSITIVITY.md`, `docs/audit/AD2_REAL_CV_MEASUREMENT.md`, `docs/audit/Q1_WITHIN_CASE_CV.md`, and `docs/audit/Q1A_RECONCILIATION.md` for the investigation and real (not assumed) CV measurements from actual local-model runs.

## What this gate can and cannot detect

No competitor found in this project's Phase 1 competitive research reports statistical power at all — only pass/fail. This honest breakdown of what the test can and can't actually see, at the sizes real ADK eval sets run at, is the differentiator.

**CV caveat on all three bullets below**: the 25%/5% figures here (unlike the 10% case above) have not been re-swept across CV — they come from the same original, low-assumed-CV generator the "Power depends on your own cost variance" section above found unrepresentative. Treat the DIRECTION as informative (large regressions are easy, small ones are hard, at any CV) but not the exact percentages, which are specific to that one assumed variance level:

- **Large regressions (25%+): reliably detected at any realistic `n`, either mode, AT THE ORIGINAL ASSUMED (LOW) CV.** Both modes saturate to ≥99.95% detection at every measured `n`/confidence combination (Phase 7 U2's grid; full data in `reports/confidence_grid_u2.json`) — likely to stay robust at higher real-world CV too, since the CV-swept 10% table above shows even the harder 10% case still detects reasonably at low `n`/high CV in two-sample mode, but this has not been directly re-measured at 25% effect.
- **Moderate regressions (10%): measured across CV, see "Power depends on your own cost variance" above.** A single "near-ceiling" claim at this effect size was found to hold only under one, unmeasured, assumed variance level — read the CV x `n` table above instead of trusting one number here.
- **Small regressions (5%): not reliably detected at small `n` under the original assumed CV, and CV-sensitivity means this is a floor, not a fixed number.** At the shipped `confidence=0.98`, two-sample at `n=30` (`min_n` itself) detects a true 5% regression only **16.20% [13.23%, 19.69%] (81/500 trials)** of the time (Phase 5 S4's grid) at that original assumed CV, rising to **24.80% [21.22%, 28.77%] (124/500 trials)** at `n=50` — already nowhere near the project's own 80%-power "reliable" bar, and the CV-sweep above shows 10%-effect power only gets worse at higher CV, so a real-world 5%-effect detection rate at realistic cost variance is very likely lower than these figures, not higher. Pairing helps but does not fix this at small `n`: Phase 7 U1's paired-mode grid measured **49.80% [46.71%, 52.89%] (498/1,000 trials)** at `n=25` under the same original assumed CV, still well under 80%. A 5% regression at a realistic ADK eval-set size is a real blind spot of this gate, in both modes — not something the paired-mode default quietly papers over, and probably a worse blind spot than these specific numbers suggest.

This honest framing — stating where the gate is weak, in numbers, rather than only where it's strong — **is the actual value proposition**: a cost gate that silently pass/fails with no power information gives you false confidence exactly where this one tells you, on every run (the "achieved power" line above), that it can't reliably see what you configured it to catch.

## Also: a real PASS/FAIL cost metric inside `adk eval`

Register the metric with a threshold, wire the plugin into your agent, and `adk eval` itself prints a real dollar score and PASSED/FAILED verdict per invocation — useful for inline cost visibility while iterating on an eval set, complementary to (not a replacement for) the CI gate above.

```python
from google.adk.agents.llm_agent import LlmAgent

import adk_tracegauge  # registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin

_usage_plugin = TraceGaugeUsagePlugin()

root_agent = LlmAgent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="...",
    after_model_callback=_usage_plugin.after_model_callback,  # <- the only wiring adk eval needs
)
```

```json
// test_config.json — the threshold this run must stay under, per invocation
{"criteria": {"adk_tracegauge_cost_usd": 0.05}}
```

```bash
adk eval my_agent_module my_eval_set.json --config_file_path test_config.json --print_detailed_results
```

Below is the actual, unedited output of the two runs in `examples/01_minimal_cost_gate.py`, re-run fresh this session (same fixture, once with a threshold above the real cost and once below it):

```
Overall Eval Status: PASSED
Metric: adk_tracegauge_cost_usd, Status: PASSED, Score: 2.8, Threshold: 5.0
```

```
Overall Eval Status: FAILED
Metric: adk_tracegauge_cost_usd, Status: FAILED, Score: 2.8, Threshold: 1.0
```

That's it — no hand-rolled `Runner`, no private ADK internals, no `EvaluationGenerator` call. **4 lines of adk-tracegauge-specific Python code** (`import adk_tracegauge`, `from adk_tracegauge import TraceGaugeUsagePlugin`, `_usage_plugin = TraceGaugeUsagePlugin()`, and the `after_model_callback=` wiring) **plus 1 line of threshold config**; the full two-run proof above took **31.4s wall-clock** re-measured fresh this session (`google-adk==2.6.3`, cold `uv`/ADK import overhead included; Phase 2 W5's original measurement was 31.6s — consistent). No API key, no live network call, no paid usage — the example's model is a deterministic fake double so the number reproduces exactly on every run; swap in a real `model="gemini-2.5-flash"` string, or a `LiteLlm`-wrapped local Ollama model, to price a real call the same way.

**One real thing worth knowing before you rely on this path for anything CI-shaped:** `adk eval`'s own *process exit code* does not reflect PASSED/FAILED — verified live, it's `0` in both runs above, regardless of the printed verdict. The real result lives in `adk eval`'s stdout table and the persisted `eval_history/*.evalset_result.json`, not in `$?`. Use this path for inline visibility during eval iteration; use `adk-tracegauge check` (above) for CI gating.

## Examples

Three runnable, independently-verified scripts under [`examples/`](examples/) — all three re-run fresh this session, byte-identical to their documented output (deterministic seeds throughout):

1. [`03_ci_regression_gate.py`](examples/03_ci_regression_gate.py) — the CI gate above, end to end (`adk-tracegauge snapshot` + `adk-tracegauge check` as real subprocesses). 53.4s (includes 3 separate cold `google-adk` import subprocesses via the demo wrapper itself, not just the 3 CLI calls timed standalone above).
2. [`01_minimal_cost_gate.py`](examples/01_minimal_cost_gate.py) — the `adk eval` metric quickstart above, as a standalone script. 31.4s.
3. [`02_subagent_rollup.py`](examples/02_subagent_rollup.py) — a real two-agent `AgentTool` delegation, showing the parent+child dollar rollup (`$0.565` combined, verified against the price table by hand). 14.0s.

Each has a header comment stating exactly how to run it and what output to expect.

## What this actually is

A `TraceGaugeUsagePlugin` that captures real per-call token usage during inference (via `BasePlugin.after_model_callback`, the only place ADK exposes `usage_metadata`+`model_version` together), plus a `CostEfficiencyEvaluator` that turns captured usage into a priced, real `PASSED`/`FAILED` `PerInvocationResult` against a required max-USD-per-invocation threshold, and `adk-tracegauge snapshot`/`adk-tracegauge check` (the `_cli.py` console entry point) which turn a populated `UsageStore` into a versioned JSON snapshot and a bootstrap-CI regression verdict between two snapshots. Usage capture requires either the `after_model_callback` wiring above (works with `adk eval`/`AgentEvaluator` directly) or a hand-rolled `App`+plugin harness (below — needed only for full sub-agent cost rollup or calling `evaluate_invocations()` yourself, outside `adk eval`); either way, `adk-tracegauge snapshot`'s `--entrypoint` calls whatever function you write to drive that capture and reads the resulting `UsageStore`.

### `DEFAULT_USAGE_STORE`

`TraceGaugeUsagePlugin()` and `CostEfficiencyEvaluator(...)` both default to sharing one process-wide `UsageStore` singleton, exported as `adk_tracegauge.DEFAULT_USAGE_STORE`, because ADK's `MetricEvaluatorRegistry` only ever instantiates a registered evaluator as `EvaluatorClass(eval_metric=eval_metric)` — there is no channel for `adk eval`/`AgentEvaluator` to hand it a custom store at construction time (see `_store.py`'s module docstring). This is why the quickstart above needs no explicit store wiring at all: the plugin writes to the default store, the registry-constructed evaluator reads from the same default store, automatically. It's also what `adk-tracegauge snapshot --entrypoint`'s "returns nothing, just populates the default store as a side effect" pattern relies on (see `docs/ci-snippet.md`).

Construct your own `UsageStore()` and pass `store=` explicitly to both the plugin and the evaluator (or `snapshot.build_snapshot(store=...)`) when you need isolation instead — e.g. running two agents' evals concurrently in the same process without their usage data mixing, or in a test that must not leak state into other tests (every test in this repo's own suite does this). See `examples/02_subagent_rollup.py` and `examples/03_ci_regression_gate.py` for real, working examples of the explicit-store pattern.

### Sub-agent delegation (`AgentTool`)

`AgentTool` (agent-as-a-tool delegation) builds a brand-new `Runner` internally, so a delegated sub-agent's real model calls land under a different `invocation_id` than the parent's. `TraceGaugeUsagePlugin` implements `before_run_callback`/`after_run_callback`, which fire once each around every `Runner.run_async()` call and bracket that invocation's whole lifetime — because `AgentTool.run_async` reuses the *same plugin instances* from the parent Runner by default (`include_plugins=True`), the plugin directly observes the real parent/child nesting (a `contextvars.ContextVar`-backed stack, safe under concurrent sibling invocations). `CostEfficiencyEvaluator` sums the parent's own calls plus every recorded descendant's calls (recursively, so nested delegation aggregates too) into one total. See `examples/02_subagent_rollup.py` for a real, run-and-verified two-agent proof: root ($0.525 across two turns) + delegated sub-agent ($0.04) = **$0.565 combined**, not just the root's own $0.525.

**This requires the hand-rolled `App`+plugin harness, not the `after_model_callback` quickstart above** — `before_run_callback`/`after_run_callback` are plugin-lifecycle hooks, invoked only through a Runner's `PluginManager`; a bare `after_model_callback` bypasses that lifecycle entirely, capturing individual model calls but never correlating delegated sub-agent calls back to the parent.

```python
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.runners import InMemoryRunner
from adk_tracegauge import TraceGaugeUsagePlugin

root_agent = LlmAgent(name="my_agent", model="gemini-2.5-flash", instruction="...", tools=[...])
app = App(name="my_app", root_agent=root_agent, plugins=[TraceGaugeUsagePlugin()])
runner = InMemoryRunner(app=app)
```

Drive it yourself (session → `runner.run_async()` → collect `Event`s), then convert those events into `Invocation` objects via `adk_tracegauge._compat.convert_events_to_eval_invocations` — a version-guarded wrapper (see "Compatibility risk" below) around the same internal `LocalEvalService` uses — and score them directly:

```python
from adk_tracegauge._compat import convert_events_to_eval_invocations
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator, CostThresholdCriterion
from google.adk.evaluation.eval_metrics import EvalMetric

invocations = convert_events_to_eval_invocations(events)
evaluator = CostEfficiencyEvaluator(
    eval_metric=EvalMetric(
        metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=0.05)
    )
)
result = evaluator.evaluate_invocations(invocations)
for pir in result.per_invocation_results:
    print(pir.score, pir.eval_status, pir.rubric_scores[0].rationale)
```

What you trade away versus `adk eval`/`AgentEvaluator`: every other built-in metric, persistence to `eval_history/`, `num_runs` repetition, and parallelism across eval cases. See `examples/02_subagent_rollup.py` for the complete, runnable version.

**What sub-agent rollup doesn't cover:** if a delegation pattern doesn't share the plugin instance with the parent (`AgentTool(..., include_plugins=False)`, or any sub-Runner construction this package doesn't know about), there's no lifecycle signal to observe the nesting from, and that sub-portion's cost is invisible to this package — the same as any other "plugin never wired in" gap, not a new failure mode.

### Scoping the gate to one agent (`check --agent`)

Every `CapturedCall` now records which ADK agent made it — `agent_name`, sourced from `callback_context.agent_name` inside `after_model_callback` (the one hook proven to fire through every integration path this package supports, including `adk eval`). `adk-tracegauge snapshot` writes this out as a per-invocation `cost_by_agent: dict[str, float]` field, and `adk-tracegauge check --agent <name>` scopes the whole regression gate to just that agent's own cost — in both `--mode two-sample` and `--mode paired`/`auto`.

`<name>` matches the agent's own `name=` (e.g. `LlmAgent(name="capital_finder", ...)`). For the common `AgentTool`-delegation case this is the *delegated* sub-agent's name, not the parent's — see `examples/02_subagent_rollup.py`'s `capital_finder` agent.

Real output, from the published `adk-tracegauge==0.4.0` artifact, a real two-agent `AgentTool` run, and a genuine regression injected into just the sub-agent's cost:

```
adk-tracegauge check: mode=two-sample [agent=capital_finder]
adk-tracegauge check [method=two_sample]: n_baseline=35 n_current=35 (min_n=30)
  mean_baseline=$0.040000  mean_current=$0.120000
  achieved power: minimum reliably-detectable effect at 80% power, given this run's observed variance/n, is ~$0.000000 (+0.00% of mean baseline) [normal approximation to the bootstrap CI -- see _regression.py module docstring for validated accuracy]
  observed effect: +0.080000 USD (+200.00%), 98% CI [+0.080000, +0.080000] (n_boot=10000, seed=42)
  statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the configured practical-significance floor.
```

The root agent's own cost was unaffected — scoping the gate to it separately correctly passes, same baseline/current files:

```
adk-tracegauge check: mode=two-sample [agent=root_agent]
adk-tracegauge check [method=two_sample]: n_baseline=35 n_current=35 (min_n=30)
  mean_baseline=$0.925000  mean_current=$0.925000
  observed effect: +0.000000 USD (+0.00%), 98% CI [+0.000000, +0.000000] (n_boot=10000, seed=42)
  statistically_significant=False practically_significant=False (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  PASS: no regression clearing both the statistical and practical bars.
```

**Snapshot format, backward compatibility (schema_version 2→3):** `cost_by_agent` is additive — a snapshot written by `adk-tracegauge<0.4.0` (`schema_version` 1 or 2) still reads correctly under `0.4.0`; `cost_by_agent` just defaults to `{}` for every record in an old file, the same pattern this package has used for every prior schema bump. `check --agent` against such a file doesn't crash or fabricate a comparison — every record reports zero cost for any agent name, which correctly resolves to `insufficient_data` (exit code `3`), not a wrong verdict. Real output, same published `0.4.0` artifact, against a real `schema_version=2` file with no `cost_by_agent` data at all:

```
adk-tracegauge check: mode=two-sample [agent=root_agent]
adk-tracegauge check [method=two_sample]: n_baseline=0 n_current=0 (min_n=30)
  mean_baseline=$0.000000  mean_current=$0.000000
  achieved power: cannot be estimated this run (fewer than 2 samples in at least one group -- no variance estimate available).
  INSUFFICIENT DATA: each group needs >= 30 invocations for a statistically meaningful bootstrap CI (see adk_tracegauge._regression module docstring for the n>=30 rationale) -- refusing to emit a verdict.
```

If you're mid-comparison across the 0.3.x → 0.4.0 upgrade: `adk-tracegauge check` (unscoped) works identically across old and new snapshots — nothing changes there. Re-run `adk-tracegauge snapshot` on `0.4.0` for both baseline and current once you want `--agent` to actually work; this is a one-time re-capture, not a required migration for anyone not using `--agent`.

## What it reports, and what it deliberately doesn't

- **`score`**: raw cost in USD for the invocation, summed across every real model call within it (tool loops and sub-agent delegation can mean more than one model call per invocation). Not normalized, not calibrated, not a 0–1 quality score.
- **`rationale`**: a per-call breakdown — model, fresh/cached/output token counts, and their individual dollar costs, plus `price_as_of=<date>` so the number's provenance travels with it.
- **No calibrated efficiency bands.** tracegauge's own token-economy axis compares your numbers against a baseline built from 75 Claude Code sessions. That baseline is not used here, on purpose — applying a Claude-Code-derived baseline to ADK agent behavior would be an unvalidated transfer. This package reports raw counts and dollars only; set your own thresholds for what "too expensive" means for your agent, and let `adk-tracegauge check`'s bootstrap test decide what counts as a real regression rather than eyeballing a delta.
- **No trajectory-quality judging.** Out of scope — CC-specific tooling, unrelated to the cost story.

## Pricing: Gemini, Claude, GPT, and local models

`tracegauge`'s bundled price table covers Claude models only (its own domain — Claude Code sessions). This package ships and owns its own multi-provider price table (`src/adk_tracegauge/data/gemini_prices.json` — historically Gemini-only, hence the name; kept as-is rather than renamed, see `_pricing.py`'s module docstring), covering:

- **Gemini** (ADK's native backend): `gemini-2.5-pro` (+ long-context tier), `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash` (deprecated, kept for historical sessions), `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview` (+ long-context tier).
- **Claude and GPT**, reached through ADK's `LiteLlm` integration (`model="anthropic/claude-opus-5"`, `model="openai/gpt-5.1"`, etc.): `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.1`, `gpt-5`. Deliberately **not** the GPT-4/o-series family — their cache-read discount (0.25x–0.5x) diverges from every other entry's 0.1x, and this table has exactly one global cache-multiplier for the whole table with no per-model override; adding them would silently under-price cached calls by 2.5x–5x.
- **Local/self-hosted models** (Ollama, vLLM — `ollama_chat/...`, `ollama/...`, `vllm/...`) resolve to a real, explicit zero-cost table entry — `cost_usd=0.000000`, trivially PASSED against any positive threshold, with a rationale line stating "local model, zero marginal cost, asserted via ADK_TRACEGAUGE_ASSUME_LOCAL" — **but only after an explicit opt-in.** Ollama Cloud is a real paid product routed through the *identical* `ollama_chat/`/`ollama/` LiteLlm prefix as local Ollama, and only the `api_base`/host (not visible anywhere adk-tracegauge captures usage — confirmed by reading google-adk's `models/lite_llm.py` and `models/llm_response.py` directly) tells the two apart. Set `ADK_TRACEGAUGE_ASSUME_LOCAL=1` (asserts every recognized local prefix) or `ADK_TRACEGAUGE_ASSUME_LOCAL=vllm/` (comma-separated, to assert only specific prefixes) before running your eval. Without it, a local-prefixed call reports `NOT_EVALUATED` with an actionable message naming the exact remedy — never a silent, possibly-wrong $0.00.
- **A custom-price extension mechanism**: set `ADK_TRACEGAUGE_PRICE_TABLE` to the path of a JSON file with the same schema (mirrors tracegauge's own `TES_PRICE_TABLE` pattern) to add or override entries — e.g. a model behind a paid gateway, or a Bedrock/Vertex AI/Azure-routed Claude/GPT model whose pricing differs from the first-party rate (deliberately not auto-resolved, since it can diverge).
- **Promotional/introductory pricing expires automatically, not silently.** An entry can carry `promo_until` (ISO date) and a published `standard_rate` — once `promo_until` passes, the resolver switches to `standard_rate` on its own, no manual table edit required, and the rationale states plainly whether the promo is still active (with its expiry date) or has already ended. `gemini-3.6-flash`/`gemini-3.7-flash` currently carry this (promotional through 2026-12-31, standard rate $1.50/$7.50 after). If a promotional entry's post-promo rate isn't published anywhere yet, adk-tracegauge warns loudly starting 14 days before expiry rather than silently freezing at a rate that may no longer apply — see `.github/workflows/price-freshness.yml`, which fails CI on the same 14-day window.

**An invocation whose model isn't in the table is never priced with a fallback rate.** `score` reports `None` with a rationale/warning naming exactly which model string didn't resolve and every model this package knows how to price — a cost number for the wrong model is worse than no number.

Every entry carries its own `source_url` and `fetched_on` date, re-verified 2026-08-14 against each model's live published rate. **Prices change without notice** — verify against the source before relying on a number for a real budget decision. Two independent freshness guards: a per-entry `is_stale` check (past `STALE_THRESHOLD_DAYS=90`, warns loudly but still reports the number) at use time, and `.github/workflows/price-freshness.yml` running weekly in CI regardless of whether anyone's pushed a commit. See `_pricing.py`'s module docstring for the full detail (long-context tiering, cache-read discount verification, thinking-token billing, and the server-side built-in-tool tokens this package deliberately refuses to price rather than guess at).

## Compatibility risk

Registration uses `google.adk.evaluation.metric_evaluator_registry`, which google-adk marks `@experimental`. This package pins `google-adk[eval]>=2.6.0,<2.8.0` accordingly, re-validated on each bump — see `CHANGELOG.md`. If the registry API breaks in a future release, registration happens at import time as a side effect, so the failure mode is a loud, immediate error on `import adk_tracegauge`, not a silent no-op.

**Python 3.14 is supported and verified, not just admitted by an open-ended `requires-python`.** `requires-python = ">=3.10"` carries no upper bound, so nothing stops a 3.14 install by accident — this package explicitly tested that case rather than leaving it untested-but-technically-allowed: the full test suite and all `examples/` scripts pass clean on Python 3.14.4 in an isolated venv, no code changes required. `Programming Language :: Python :: 3.14` is a real classifier, in CI's test matrix, not aspirational.

The hand-rolled sub-agent-rollup harness additionally depends on `EvaluationGenerator.convert_events_to_eval_invocations` — a non-public ADK internal with no `@experimental` marker at all (no stated breakage discipline whatsoever). **This package's own primary paths (`adk-tracegauge check`, and `after_model_callback` + `adk eval`) never call this function** — confirmed by grep, nothing under `src/adk_tracegauge/` touches it outside `_compat.py`; only the optional sub-agent-rollup harness pattern does. Because it's still needed for that one path, it's wrapped behind `adk_tracegauge._compat.convert_events_to_eval_invocations`, which runs a version check against this package's known-tested `google-adk` range and raises a clear, actionable `RuntimeError` — naming the installed version and exactly which integration path is affected — instead of a bare, unexplained `AttributeError`/`ImportError` if the internal has moved. See `_compat.py`'s module docstring and `tests/test_compat.py` for the version-guard tests, including a simulated unsupported-version case.

A scheduled CI job (`.github/workflows/pypi-canary.yml`) installs the *latest* `google-adk[eval]` release (ignoring the pin) and runs the full test suite weekly, so a break surfaces on a schedule rather than via a user bug report.

## Troubleshooting

Real, live-triggered errors and their fixes — see [`docs/troubleshooting.md`](docs/troubleshooting.md) for the full text and context:

- **Wrong `google-adk` version installed** (outside the `>=2.6.0,<2.8.0` pin) → a loud `ModuleNotFoundError`/`RuntimeError` at import time, not a silent wrong answer.
- **Unknown/unresolvable model** → `score=None` plus an actionable warning naming every model this package can price.
- **Missing threshold** → a `ValueError` at construction time; this package never falls back to a permissive always-PASSED default.
- **A local model (Ollama/vLLM) reports `NOT_EVALUATED` instead of `$0.00`** (Phase 3 B1) → expected, fail-closed behavior since Ollama Cloud (paid) shares the same prefix as local Ollama — set `ADK_TRACEGAUGE_ASSUME_LOCAL` to opt in.
- **`adk-tracegauge check` refuses to run at all (`exit code 3`)** at a smaller eval-set size than expected → this is `--min-n`'s refusal, not a bug; see "Known limitations" below for the measured detection-power reason `min_n=30` exists and why `--mode paired` may still work below it.

## Known limitations

These are real, current, and worth knowing before you rely on this package — not hidden, just not the first thing you read.

- **Plain two-sample comparison does not reliably detect a realistic-magnitude cost regression at a realistic ADK eval-set size — measured, not assumed (Phase 3 B4).** At `n=25` (a realistic ADK eval-set size — this repo's own `examples/03_ci_regression_gate.py` uses `n=40`, deliberately just above `min_n=30`, because real ADK eval cases can involve real/expensive model calls, so teams keep eval sets to tens of cases, not hundreds), **the two-sample method detects a true 10% cost regression only 51.40% of the time (257/500 trials, Wilson 95% CI [47.03%, 55.75%]) at the shipped `confidence=0.98`** (Phase 5 S4's own 90-cell grid), **and refuses to run at all below `n=30`'s own `min_n` floor.** This is exactly why **`adk-tracegauge check`'s `auto` mode (the default) PREFERS a paired comparison, not two-sample (Phase 7 U1)**: whenever a stable per-eval-case key resolves with enough overlap, `--mode paired` (`evaluate_regression_paired`) is used instead, and it is dramatically more sensitive at the same `n` whenever real per-case cost variance exists — a real, fresh, dedicated power grid (`scripts/measure_paired_power_grid.py`, `n` ∈ {10, 25, 50, 100} × effect ∈ {0, 5, 10, 25, 50}%, 1,000 trials/cell, confidence=0.98, 20,000 simulated `check` calls) measured **97.80% detection at `n=25`/10%-effect (978/1,000 trials, Wilson 95% CI [96.69%, 98.54%])** versus two-sample's 51.40% [47.03%, 55.75%] on the same `n`/confidence — see `PLAN.md`'s Phase 7 U1 entry for the full 20-cell grid laid out against the two-sample grid. The pairing key is resolved automatically: `eval_case_id` (Phase 4 R2's primary key, recovered via `adk-tracegauge snapshot --eval-history <path-to-adk-eval's-own-.evalset_result.json>` — works with the default `adk eval` CLI flow, no `.evalset.json` changes needed) first, then `session_id` (if a hand-rolled harness pins `runner.run_async(session_id=...)` itself) as a fallback, then two-sample if neither resolves enough overlap — the resolved mode and key are always printed, never silently assumed. **The trade-off, stated honestly, not hidden by making paired the default:** the SAME dedicated grid found paired mode's own false-positive rate is *higher*, not lower, than two-sample's at every measured `n` (e.g. 4.10% [3.04%, 5.51%] vs. 2.20% [1.23%, 3.90%] at `n=10`, 41/1,000 vs. 11/500; 2.40% [1.62%, 3.55%] vs. 1.20% [0.55%, 2.59%] at `n=25`, 24/1,000 vs. 6/500) — paired is more POWERFUL at a given `n`, not more RELIABLE, which is why `--mode auto`'s threshold for preferring paired stays at the same `min_n=30` bar two-sample itself requires, not a lower one (see `_paired_mode_viable`'s docstring in `_cli.py`). `--mode two-sample` (ignoring pairing entirely) and explicit `--mode paired` (fails loud, not silently, if too few keys overlap) remain available for any caller who wants either method by name. **A dedicated, higher-rigor (2,000 trials/cell, Wilson CIs on every cell) re-measurement of BOTH modes at `n=30`/`n=50` — the sizes closest to the shipped `min_n` floor — was done Phase 7 U2; see two bullets below for the full head-to-head numbers.**
- **The above limitation is now surfaced at RUNTIME, every `adk-tracegauge check` run, not only in this doc (Phase 4 R4).** Every run prints an `achieved power` line — the minimum effect size the bootstrap test could reliably (80% power, the same bar B4 used above) detect given THIS run's own observed variance and `n` — and an explicit `WARNING` whenever your configured `--min-effect-usd`/`--min-effect-pct` floor is smaller than that achievable floor (see the Quickstart output above for a real example: at `n=40`, `BASE_SD≈$0.0015`/mean≈$0.0086, the achievable floor is ~$0.000536/6.25%, ABOVE the default `$0.0001` floor, so the WARNING fires). The achieved-power figure is a normal-approximation to the bootstrap CI (bootstrap power has no closed form) — validated against B4/R2's own measured grid at 7 points, accurate to within 2–8 percentage points, worst at `n=25` (see `_regression.py`'s "Achieved statistical power" section for the full accuracy table and derivation). **As of `0.5.0`, the same condition that fires the WARNING line above also sets a distinct process exit code, `4` (`EXIT_UNDERPOWERED_PASS`), for two-sample mode specifically** — `status="pass"` AND this run's own observed variance/`n` means the configured practical-significance floor could not reliably (80% power) be detected. This is a real behavior change: **a CI config that treats any non-zero exit code as a build failure (not specifically checking for `1`) may see a previously-`0`-exiting, real-variance two-sample run now exit `4` and fail the build**, even with no regression found and no workload change — intentional (a "pass" at low power was misleading by omission), not a bug, but worth checking your own CI config against. Paired mode never returns exit `4` (see `RegressionCheckResult.underpowered_pass`'s docstring in `_regression.py` for why this is deliberately scoped to two-sample only); full exit-code table in `_cli.py`'s module docstring. **`min_n=30` was explicitly re-examined (4.3), not left unchanged by default** — real measurement at n∈{30,35,40,45} (10% effect, B4's generator, 200 trials/cell) showed 71.50% [64.88%, 77.30%] (143/200), 79.00% [72.84%, 84.07%] (158/200), 77.50% [71.23%, 82.74%] (155/200), 83.00% [77.18%, 87.57%] (166/200) detection, i.e. `n=30` genuinely doesn't clear 80% for this scenario either — but **kept at 30 anyway**: no single `min_n` generically solves "80% power for the regression size YOU care about" (that depends on your own cost variance and threshold, which this package cannot know in advance — B4's own grid shows even `n=100` only clears 64.50% [57.65%, 70.80%] (129/200 trials) for a 5% effect), so raising it would just trade real signal on legitimate 30–44-invocation eval sets for a false sense of a "fixed" problem. The runtime achieved-power/warning mechanism above is the actually-general fix. **(Superseded by Phase 5 S4, next bullet) False-positive rate at `n=30` under the ORIGINAL 0.95-confidence default measured at 500 trials: 4.60% [3.08%, 6.81%] (23/500); independent re-check, different seed, 500 trials: 4.20% [2.76%, 6.34%] (21/500)** — both well above the ~2.5% nominal expectation, because at this variance level the 5%-relative practical floor is only ~1.3 sampling standard errors from zero at `n=30` and doesn't meaningfully suppress noise-driven false positives on its own. **A BCa (bias-corrected/accelerated) bootstrap was implemented as an experiment and empirically measured (4.5)**: no measurable improvement (percentile vs. BCa FPR: 6.00% [3.83%, 9.28%] (18/300) vs. 5.33% [3.31%, 8.49%] (16/300) at `n=10`, 3.00% [1.59%, 5.60%] (9/300) vs. 3.33% [1.82%, 6.03%] (10/300) at `n=25`) — expected, since BCa's corrections target bias/skew in the bootstrap distribution, near-zero for a near-symmetric mean statistic on this project's cost data; NOT shipped. A studentized bootstrap was assessed but not built or tested — it needs a per-resample SE estimate that is known to be unstable at `n<20-30`, exactly the regime it would need to help in; see `_regression.py`'s "Anti-conservatism at small n" section for the full reasoning. This remains a real, honest, unresolved limitation at small `n`.
- **The shipped default `--confidence` was RETUNED, Phase 5 S4, specifically because a ~4.4% false-positive rate is not acceptable for a tool whose entire value proposition is being a trustworthy CI gate** — a gate that cries wolf roughly 1 run in 23 trains users to ignore or disable it, a real product-credibility failure, not only a statistics one. A full one-sided-alpha × `n` × true-effect grid was measured (90 cells: alpha ∈ {0.025, 0.01, 0.005} ⟷ `--confidence` ∈ {0.95, 0.98, 0.99} via `confidence = 1 - 2·alpha`; `n` ∈ {10, 25, 30, 50, 100, 250}; effect ∈ {0%, 5%, 10%, 25%, 50%}; ≥500 trials/cell, `scripts/measure_regression_alpha_grid.py`) — see `PLAN.md`'s Phase 5 S4 entry for the full 90-cell tables. **Default changed `0.95` → `0.98`** (one-sided alpha 0.025 → 0.01): real shipped-configuration FPR at `n=30` (real floors, real `n_boot=10,000`, 500 trials × 2 seeds) drops from **4.40% [3.29%, 5.86%] combined (44/1,000)** to **2.30% [1.54%, 3.43%] combined (23/1,000)** — a >45% real reduction, landing within sampling noise of the ~2% target this project set (a defensible margin below the originally-intended 2.5% nominal figure). **The cost**: detection power for a realistic 10% true regression at `n=50` drops from 91.20% [88.39%, 93.38%] (456/500, old default) to 83.40% [79.89%, 86.40%] (417/500, new default) — still clears the project's own 80%-power "reliable detection" bar (`ACHIEVED_POWER_TARGET`), which is exactly why `0.98`, not the even-tighter `0.99` (FPR 1.60% [0.99%, 2.58%] combined (16/1,000), but `n=50`/10%-effect power falls to 76.20% [72.28%, 79.72%] (381/500), BELOW the 80% bar — a real collapse, and why `0.99` was rejected), was chosen. At `n=30` (`min_n` itself), 10%-effect power drops from 72.80% [68.74%, 76.52%] (364/500) to 58.40% [54.03%, 62.64%] (292/500) — already below 80% at the OLD default too, so this is not a newly-introduced weakness, just a number worth knowing. 25%/50%-effect detection stays at 100% under every alpha tested — the power cost is concentrated entirely in the 5–10%-effect range. **The practical-significance floor's own, independent contribution was also measured (4.5)**: at `n=30`/the new default, the false-positive rate with the practical floor DISABLED (pure statistical test) is IDENTICAL to the real, floor-enabled rate (13/500, Wilson 95% CI [1.53%, 4.40%], and 10/500, [1.09%, 3.64%], both branches, both seeds) — the floor is still a real, independently-AND'd gate (confirmed by re-reading `evaluate_regression`'s `is_regression = statistically_significant and practically_significant`, unchanged), it simply isn't the thing suppressing false alarms at this particular `n`/variance combination; see `_regression.py`'s `DEFAULT_CONFIDENCE` docstring for the complete numbers and reasoning.
- **At the SHIPPED MINIMUM `n=30`, under the SHIPPED default `--confidence 0.98` — the two headline numbers, same configuration, stated together: false-positive rate 2.30% [1.54%, 3.43%] combined (23/1,000, real floors, real `n_boot=10,000` — the bullet above), and detection power for a true 10% cost regression 58.40% [54.03%, 62.64%] (292/500, statistical ceiling, floors disabled, same `n`/confidence — S4's own 90-cell grid), i.e. well below the 80% "reliable" bar.** A clean `check` result at `n=30` should be read as "no cost increase that both cleared a ~2%-FPR statistical bar and the practical floor was found" — NOT as "regressions this size were reliably ruled out"; the achieved-power line printed on every run (previous bullet) says which of the two your own run actually got. **Phase 6 T4 re-examined whether `min_n` should be RAISED instead, now that S4 changed the default confidence** (the original 4.3 min_n-vs-raise decision, cited two bullets up, was measured at the OLD `confidence=0.95` and needed re-validation, not an assumption it still held): re-measured n ∈ {30, 35, 40, 45, 50} at confidence=0.98 for a 10% effect, 500 trials/cell, two independent seed bases at n=30/45/50 — 56.60% [52.22%, 60.88%] (283/500) / 57.20% [52.82%, 61.47%] (286/500) (n=30, confirms the 58.40% grid figure), 64.40% [60.11%, 68.47%] (322/500) (n=35), 68.80% [64.61%, 72.70%] (344/500) (n=40), 72.80% [68.74%, 76.52%] (364/500) / 77.20% [73.32%, 80.66%] (386/500) (n=45), **79.60% [75.85%, 82.90%] (398/500) / 81.00% [77.33%, 84.20%] (405/500) (n=50, two independent measurements)** vs. S4's own single-measurement 83.40% [79.89%, 86.40%] (417/500) for that cell — three independent 500-trial measurements of `n=50` averaging ~81.3%, i.e. `n=50` sits right AT the 80% line, not robustly above it, once measured more than once. **DECISION: `min_n` stays at 30, not raised** — no n up to 50 reliably clears 80% for a 10% effect either, so raising the floor would definitely refuse every real 30–49-invocation eval set while only maybe buying reliable detection, a bad trade given this package's own honesty-over-usability precedent (Phase 5 S1's "no silent guess," the achieved-power reporting itself). The runtime achieved-power/floor-warning mechanism (4.1/4.2 above) remains the general fix — it reports the truth for YOUR run's actual variance and `n`, rather than this package implying a fixed `n` clears a reliability bar it provably does not, at any confidence level tested. See `_regression.py`'s `MIN_N_DEFAULT` docstring ("Phase 6 T4 re-validation") and `PLAN.md`'s Phase 6 T4 entry for the full measurement.
- **Phase 7 U2 — re-measured the deciding cells (confidence ∈ {0.95, 0.98, 0.99} × `n` ∈ {30, 50} × true effect ∈ {0%, 10%, 25%}) with real Wilson score CIs on every cell, for BOTH modes side by side, once paired mode became the DEFAULT `--mode auto` preference (Phase 7 U1) — `scripts/measure_regression_confidence_grid.py`.** **Corrected to 5,000 trials/cell during the Phase 8 FPR-anomaly audit** (`docs/audit/FPR_ANOMALY.md`; originally 2,000 trials/cell, 902.8s wall-clock — trials 0–1,999 are byte-identical to that original run, extended to 5,000 rather than replaced, 2,318.0s wall-clock for the extension). This refines, not overturns, the power numbers: two-sample `n=50`/10%-effect/`confidence=0.98` lands at **80.38% [79.26%, 81.46%] (4,019/5,000)**, consistent with Phase 6 T4's independent finding that S4's original single-run 83.40% reading was on the high side of noise. FULL grid (statistical-only, floors disabled, same convention as S4):

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

  (25%-effect column omitted: ≥99.96% at every cell, both modes, all three confidence levels — saturated, no decision-relevant information. Full 18+18-cell raw data, including the 25% column and the FPR cross-mode significance table below, in `reports/confidence_grid_u2.json`.) **Re-decision (2.3): `DEFAULT_CONFIDENCE` STAYS at 0.98** — the value does not change, but the reasoning is now paired-mode-aware rather than two-sample-only. Paired mode's power for a 10% effect is already near-ceiling at 0.98 and barely moves at 0.99 (99.22%→98.36% at n=30; 100.00%→100.00% at n=50) — there is no real headroom to buy by tightening further on the now-default path. Two-sample's power, by contrast, drops sharply over the same tightening (57.46%→49.30% at n=30; 80.38%→73.84% at n=50) and crosses BELOW the 80%-power bar at n=50 — reproducing the exact criterion-2 failure that got 0.99 rejected by S4 in the first place, on a path (two-sample) that remains real and live whenever no pairing key resolves. Since this package ships one `DEFAULT_CONFIDENCE` shared by both modes, tightening it to 0.99 would optimize for the path that needs it least at the direct expense of the path that needs it most — so the shared constant stays put. See `_regression.py`'s `DEFAULT_CONFIDENCE` docstring ("Phase 7 U2, 2.3") for the full reasoning, including a noted-but-not-implemented future option (a paired-mode-specific, tighter confidence default).
- **Note on paired-mode FPR (Phase 8 audit, `docs/audit/FPR_ANOMALY.md`): the ORIGINAL 2,000-trial version of the table above showed paired mode's rate exceeding two-sample's at 4 of the 6 shared cells — that comparison was published without ever being significance-tested, and does not hold up when tested.** A two-proportion z-test on the original grid's own counts found NO cell significant (largest z=1.80, p=0.07 at confidence=0.98/n=30); the 5,000-trial corrected grid above confirms this directly — the ranking does not reproduce at any of the 6 cells (largest z=0.97, p=0.33 at confidence=0.98/n=50; full z/p per cell in `reports/confidence_grid_u2.json`'s `fpr_cross_mode_significance` key), and at `confidence=0.95/n=30` the ranking actually flips (paired 2.98% < two-sample 3.18%). A second, independent 5,000-trial re-measurement with a wholly different seed base (`scripts/measure_fpr_anomaly_reproducibility.py`) reaches the same conclusion (largest z=1.29, p=0.20). No code defect was found in either mode's bootstrap implementation (`_regression.py`'s resampling loops, read in full) or null-data generator. Both modes DO independently show significant elevation above their OWN nominal one-sided alpha at most cells — real, but the SAME generic small-`n` percentile-bootstrap anti-conservatism this doc already documents above (BCa/studentized-bootstrap discussion), present at comparable magnitude in both modes, not a paired-specific defect. A structural one-sample-vs-two-sample hypothesis (paired's CI relying on one empirical variance estimate vs two-sample's averaging two) was tested directly on matched-total-variance synthetic Gaussian data using the real production bootstrap functions and refuted (5/6 cells not significant, `scripts/measure_fpr_anomaly_h1_discriminant.py`). No shipped default changed as a result of this audit.
- **CV caveat on every power figure in this section (Phase 9 AD1, added retroactively — does not overturn any decision above): all `n`/confidence/`min_n` decisions documented in this "Known limitations" section were made using ONE assumed per-invocation cost variance level (the original generator's, never independently measured against real ADK data), the same one AD1/AD2 found the published headline power figure was not robust to.** FPR figures throughout this section are unaffected (AC1/AD1 measured FPR directly under realistic-magnitude variance and skew and found it holds — `docs/audit/AC1_SKEW_SENSITIVITY.md`). Power figures — every percentage above tied to a specific `n`/effect/confidence cell — are NOT independently re-verified at other variance levels; read them as "what this generator measured," not as a claim that generalizes to your own workload's actual cost variance. See "Power depends on your own cost variance" above for the CV-swept 10%-effect table, and `docs/audit/AD2_REAL_CV_MEASUREMENT.md` for a real (if narrow-domain) measured CV. No `DEFAULT_CONFIDENCE`/`MIN_N_DEFAULT` decision recorded above is reopened by this caveat — those stay as decided pending real-data-driven re-evaluation, not as an unstated assumption.
- **`AgentEvaluator.evaluate()`'s own pytest-style pass/fail exit is directionally unreliable for this metric, at any threshold.** `agent_evaluator.py::_process_metrics_and_get_failures` (google-adk 2.6.3, lines ~713-719) recomputes PASSED/FAILED itself from raw scores and the *deprecated* `EvalMetric.threshold` scalar via `mean(scores) >= threshold` — hardcoded higher-is-better, ignoring this evaluator's own correct `eval_status` entirely. `adk eval`/`LocalEvalService` are **unaffected** — they read this evaluator's real `eval_status` directly, always correctly. Trust `adk eval`/`LocalEvalService`, `adk-tracegauge check`, or this evaluator's own `eval_status` (call `evaluate_invocations()` directly), for real pass/fail — never `AgentEvaluator.evaluate()`'s own assert/no-assert outcome for this metric. See `evaluator.py`'s module docstring for the full source-confirmed detail, and `tests/test_agent_evaluator_integration.py` for the permanent regression test documenting this.
  **As of Phase 3 B3, this is also a real runtime `warnings.warn`** (not only documentation) — it fires when this metric is actually evaluated under a real `AgentEvaluator.evaluate()` call, naming this exact behavior and the installed `google-adk` version. Detection uses a `contextvars.ContextVar` set for the duration of the call (installed as an `adk_tracegauge` import side effect), not a call-stack check — a stack walk was tried first and empirically fails, because `LocalEvalService.evaluate()` forks each eval case into its own `asyncio.Task`, which erases the physical call stack back to whichever caller awaited it, identically for `AgentEvaluator.evaluate()` and `adk eval`. **Known gap:** the very first `AgentEvaluator.evaluate()` call in a process won't trigger the warning if `adk_tracegauge` is imported for the first time as a side effect of *that same call* loading your agent module (the "Also" quickstart's own pattern) — the wrap installs a moment too late for a call already in progress. Workaround: `import adk_tracegauge` explicitly at the top of your eval driver script or `conftest.py`, ahead of any `AgentEvaluator.evaluate()` call — every call after the wrap is installed is detected correctly. See `evaluator.py`'s `_install_agent_evaluator_marker` docstring for the full mechanism and `tests/test_agent_evaluator_integration.py` for a subprocess-based regression test proving both the detection and the gap are real.
- **`adk eval`'s own process exit code doesn't reflect PASSED/FAILED** (see "Also" section above) — this is exactly why `adk-tracegauge check`, not `adk eval`, is this README's hero path; use its exit code for CI gating, not `adk eval`'s.
- **`is_local_model()`'s Ollama Cloud gap requires an explicit opt-in, or a local model reports `NOT_EVALUATED`, not `$0.00`.** (Phase 3 B1.) Ollama Cloud is a real paid product sharing the identical `ollama_chat/`/`ollama/` LiteLlm prefix as local Ollama, and google-adk's `LlmResponse` schema carries no host/endpoint field to distinguish them — confirmed by reading `models/lite_llm.py` and `models/llm_response.py` directly. Set `ADK_TRACEGAUGE_ASSUME_LOCAL` (see "Pricing" above) to opt in explicitly; without it, this is fail-closed, not a silent $0.00.
- **Pricing scope is Gemini + Claude + GPT (current-generation) + local models only.** Bedrock/Vertex AI/Azure-routed Claude/GPT, older GPT-4/o-series models, and any other vendor are not built in — register a custom price via `ADK_TRACEGAUGE_PRICE_TABLE` or open an issue.
- **Standard (interactive/online) tier pricing only** — no Batch API support (ADK's live-agent plugin path never observes a Batch API call at all, confirmed by source grep).
- **Text/image/video input rate only** — audio input is priced at the text rate for every model, which under-charges audio-heavy invocations (documented per-model in each price entry's `note`).
- **Streaming behavior is documentation-corroborated, not independently confirmed against a live API call** (no Gemini API key was available when this was implemented) — see `_adapter.py`'s module docstring for the monotonicity check that makes this gap largely moot: a stream whose reported totals ever decrease, or that never reaches a final chunk, is refused pricing rather than trusted under a possibly-wrong assumption.
- **No calibrated efficiency bands, no trajectory-quality judging, no OTel span export** — see "What it reports" above and Roadmap/Phase 3 notes in `PLAN.md`.

## How PASSED/FAILED is computed (and why not ADK's built-in `>=`)

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better, and there is no lower-is-better/inverted-metric convention anywhere in `google.adk.evaluation` (checked directly against the source). So `CostEfficiencyEvaluator` computes PASSED/FAILED itself: `PASSED` when `cost <= threshold`, `FAILED` when `cost > threshold` (`threshold` from `CostThresholdCriterion`, preferred, or the deprecated `EvalMetric.threshold`). A `ValueError` is raised at construction time if neither is set — this package never falls back to a permissive always-PASSED default. An invocation whose cost couldn't be verified at all (no usage captured, unresolved model, a streaming anomaly, an unpriced token category) reports `NOT_EVALUATED`, not a fabricated pass/fail. `adk-tracegauge check`'s own PASS/regression/insufficient-data verdict is a separate, statistical question — see "Quickstart" above and `_regression.py`'s module docstring.

## Relationship to tracegauge

Through Phase 3, this package depended on `tracegauge`'s `tes.cost` module (`compute_session_cost` and its digest types) as a library. **As of Phase 4 R5, it no longer does** — an audit found that arithmetic (~55 lines total) plus two internal-only dataclasses were the *only* things this package ever used from `tracegauge` anywhere in `src/` (nothing from its actual differentiators: the token-economy/trajectory/waste axes, the CLI, the dashboard), so the dollar-cost computation was ported in-house (`src/adk_tracegauge/_cost.py`, behavior-preserving — proven via the full test suite plus hand-computed spot checks) and the `tracegauge` PyPI dependency was removed from `pyproject.toml` entirely.

The ported code originates from `tracegauge`'s `tes/cost.py`/`tes/_digest.py` (Copyright Gaurav Gandhi, same author as this package, [token-efficiency-scorer](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)), which that repository dual-licenses AGPL-3.0-only OR Apache-2.0 at the licensor's option — this package's copy exercises the Apache-2.0 option, consistent with adk-tracegauge's own license, and the copy carries its own attribution note (see `_cost.py`'s module docstring). One finding from this audit worth recording: the dual-license grant is confirmed present (an SPDX header in both files) as of `tracegauge==0.10.1` and the current upstream source, but genuinely *absent* as a per-file header in `tracegauge==0.10.0` — the older of the two versions previously admitted by this package's own pin — though the upstream repository's README license note covers that release too, at the repository level.

## What this is not

Not a general ADK observability/tracing tool — it has no span export, no trace viewer, no OTel integration (see `traceAI-google-adk` or ADK's own native OTel support for that). Not a statistics/confidence-interval layer for ADK *evaluation quality* results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain); the bootstrap-CI machinery here is specifically about *cost* regression, not eval-score regression. Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them. Not a guaranteed-sensitive regression detector at any eval-set size — see "Known limitations" above for the measured, honest power caveats and the `--mode paired` mitigation.

## License

[Apache-2.0](LICENSE).
