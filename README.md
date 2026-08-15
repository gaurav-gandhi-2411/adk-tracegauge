# adk-tracegauge

A statistically-validated **CI cost-regression gate** for [Google ADK](https://github.com/google/adk-python) agents: snapshot a real per-invocation **USD cost** distribution from an eval run, and fail the build only when a cost increase is both statistically and practically significant. Also registers as a real per-invocation **PASS/FAIL threshold metric** inside `adk eval` itself. Built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. Raw dollars and tokens, no calibrated bands, no fabricated numbers for unknown models.

[![PyPI](https://img.shields.io/pypi/v/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![CI](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

---

## Quickstart: the CI cost-regression gate

```bash
pip install adk-tracegauge
tracegauge snapshot --entrypoint my_eval_suite:run_and_return_store --output baseline.json
tracegauge snapshot --entrypoint my_eval_suite:run_and_return_store --output current.json
tracegauge check --baseline baseline.json --current current.json
```

`my_eval_suite:run_and_return_store` is a zero-argument callable you already have (it runs your ADK eval — `AgentEvaluator.evaluate()` or your own `Runner` harness — with `TraceGaugeUsagePlugin` wired in; see "What this actually is" below). `tracegauge check` runs a percentile bootstrap on the difference in mean cost and exits with a **real, distinguishable exit code**: `0` pass, `1` regression, `3` insufficient data. Real output, from a genuine +20%-mean injected regression measured fresh this session (`examples/03_ci_regression_gate.py`, both `snapshot` calls plus `check` itself run as real subprocesses, `google-adk==2.6.3`):

```
tracegauge check [method=two_sample]: n_baseline=40 n_current=40 (min_n=30)
  mean_baseline=$0.008583  mean_current=$0.009998
  achieved power: minimum reliably-detectable effect at 80% power, given this run's observed variance/n, is ~$0.000474 (+5.53% of mean baseline) [normal approximation to the bootstrap CI -- see _regression.py module docstring for validated accuracy]
  observed effect: +0.001415 USD (+16.49%), 95% CI [+0.001085, +0.001744] (n_boot=10000, seed=42)
  statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  WARNING: the configured practical-significance floor (effectively $0.000100, from min_effect_usd=$0.000100 OR min_effect_pct=5.00%) is BELOW this run's minimum reliably-detectable effect at 80% power (~$0.000474, given the observed variance and n) -- the statistical test cannot reliably catch a real regression as small as your configured floor at this sample size. A clean/passing result here should NOT be read as strong evidence of no regression at your configured floor -- consider a larger eval set, a lower-variance cost metric, or an explicitly higher floor.
  REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the configured practical-significance floor.
```
```
$ echo $?
1
```

**Every `tracegauge check` run prints its own "achieved power" figure (Phase 4 R4)** — the minimum effect size the bootstrap test could reliably (80% power) detect given THIS run's actual observed variance and `n`, plus (as shown above) an explicit `WARNING` whenever your configured `--min-effect-usd`/`--min-effect-pct` floor is smaller than that achievable floor — i.e. the gate is telling you, with real numbers from your own run, that it cannot reliably catch a regression as small as what you configured it to care about. See "Known limitations" below.

**Measured this session, not estimated:** the 3 `tracegauge`-specific command lines above took **35.3s wall-clock combined** (11.75s + 11.85s + 11.75s, each dominated by cold `google-adk` import overhead, not by the actual comparison — the bootstrap itself runs in well under a second). A full copy-pasteable GitHub Actions workflow lives at [`docs/ci-snippet.md`](docs/ci-snippet.md).

**Why this is the hero path, not the `adk eval` metric below:** `adk eval`'s own process exit code does not reflect PASSED/FAILED (verified live — see below), so it cannot gate a CI job on its own; and `AgentEvaluator.evaluate()`, ADK's pytest-style harness, has a real, source-confirmed polarity bug that can invert pass/fail for a lower-is-better metric like cost (see "Known limitations"). `tracegauge check` is this package's own code, with its own real exit codes, proven to work standalone — that's the actual, statistically-measured differentiator (see "Known limitations" for the honest caveats on detection power at small `n`, and how `--mode paired` fixes them).

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

**One real thing worth knowing before you rely on this path for anything CI-shaped:** `adk eval`'s own *process exit code* does not reflect PASSED/FAILED — verified live, it's `0` in both runs above, regardless of the printed verdict. The real result lives in `adk eval`'s stdout table and the persisted `eval_history/*.evalset_result.json`, not in `$?`. Use this path for inline visibility during eval iteration; use `tracegauge check` (above) for CI gating.

## Examples

Three runnable, independently-verified scripts under [`examples/`](examples/) — all three re-run fresh this session, byte-identical to their documented output (deterministic seeds throughout):

1. [`03_ci_regression_gate.py`](examples/03_ci_regression_gate.py) — the CI gate above, end to end (`tracegauge snapshot` + `tracegauge check` as real subprocesses). 53.4s (includes 3 separate cold `google-adk` import subprocesses via the demo wrapper itself, not just the 3 CLI calls timed standalone above).
2. [`01_minimal_cost_gate.py`](examples/01_minimal_cost_gate.py) — the `adk eval` metric quickstart above, as a standalone script. 31.4s.
3. [`02_subagent_rollup.py`](examples/02_subagent_rollup.py) — a real two-agent `AgentTool` delegation, showing the parent+child dollar rollup (`$0.565` combined, verified against the price table by hand). 14.0s.

Each has a header comment stating exactly how to run it and what output to expect.

## What this actually is

A `TraceGaugeUsagePlugin` that captures real per-call token usage during inference (via `BasePlugin.after_model_callback`, the only place ADK exposes `usage_metadata`+`model_version` together), plus a `CostEfficiencyEvaluator` that turns captured usage into a priced, real `PASSED`/`FAILED` `PerInvocationResult` against a required max-USD-per-invocation threshold, and `tracegauge snapshot`/`tracegauge check` (the `_cli.py` console entry point) which turn a populated `UsageStore` into a versioned JSON snapshot and a bootstrap-CI regression verdict between two snapshots. Usage capture requires either the `after_model_callback` wiring above (works with `adk eval`/`AgentEvaluator` directly) or a hand-rolled `App`+plugin harness (below — needed only for full sub-agent cost rollup or calling `evaluate_invocations()` yourself, outside `adk eval`); either way, `tracegauge snapshot`'s `--entrypoint` calls whatever function you write to drive that capture and reads the resulting `UsageStore`.

### `DEFAULT_USAGE_STORE`

`TraceGaugeUsagePlugin()` and `CostEfficiencyEvaluator(...)` both default to sharing one process-wide `UsageStore` singleton, exported as `adk_tracegauge.DEFAULT_USAGE_STORE`, because ADK's `MetricEvaluatorRegistry` only ever instantiates a registered evaluator as `EvaluatorClass(eval_metric=eval_metric)` — there is no channel for `adk eval`/`AgentEvaluator` to hand it a custom store at construction time (see `_store.py`'s module docstring). This is why the quickstart above needs no explicit store wiring at all: the plugin writes to the default store, the registry-constructed evaluator reads from the same default store, automatically. It's also what `tracegauge snapshot --entrypoint`'s "returns nothing, just populates the default store as a side effect" pattern relies on (see `docs/ci-snippet.md`).

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

## What it reports, and what it deliberately doesn't

- **`score`**: raw cost in USD for the invocation, summed across every real model call within it (tool loops and sub-agent delegation can mean more than one model call per invocation). Not normalized, not calibrated, not a 0–1 quality score.
- **`rationale`**: a per-call breakdown — model, fresh/cached/output token counts, and their individual dollar costs, plus `price_as_of=<date>` so the number's provenance travels with it.
- **No calibrated efficiency bands.** tracegauge's own token-economy axis compares your numbers against a baseline built from 75 Claude Code sessions. That baseline is not used here, on purpose — applying a Claude-Code-derived baseline to ADK agent behavior would be an unvalidated transfer. This package reports raw counts and dollars only; set your own thresholds for what "too expensive" means for your agent, and let `tracegauge check`'s bootstrap test decide what counts as a real regression rather than eyeballing a delta.
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

The hand-rolled sub-agent-rollup harness additionally depends on `EvaluationGenerator.convert_events_to_eval_invocations` — a non-public ADK internal with no `@experimental` marker at all (no stated breakage discipline whatsoever). **This package's own primary paths (`tracegauge check`, and `after_model_callback` + `adk eval`) never call this function** — confirmed by grep, nothing under `src/adk_tracegauge/` touches it outside `_compat.py`; only the optional sub-agent-rollup harness pattern does. Because it's still needed for that one path, it's wrapped behind `adk_tracegauge._compat.convert_events_to_eval_invocations`, which runs a version check against this package's known-tested `google-adk` range and raises a clear, actionable `RuntimeError` — naming the installed version and exactly which integration path is affected — instead of a bare, unexplained `AttributeError`/`ImportError` if the internal has moved. See `_compat.py`'s module docstring and `tests/test_compat.py` for the version-guard tests, including a simulated unsupported-version case.

A scheduled CI job (`.github/workflows/pypi-canary.yml`) installs the *latest* `google-adk[eval]` release (ignoring the pin) and runs the full test suite weekly, so a break surfaces on a schedule rather than via a user bug report.

## Troubleshooting

Real, live-triggered errors and their fixes — see [`docs/troubleshooting.md`](docs/troubleshooting.md) for the full text and context:

- **Wrong `google-adk` version installed** (outside the `>=2.6.0,<2.8.0` pin) → a loud `ModuleNotFoundError`/`RuntimeError` at import time, not a silent wrong answer.
- **Unknown/unresolvable model** → `score=None` plus an actionable warning naming every model this package can price.
- **Missing threshold** → a `ValueError` at construction time; this package never falls back to a permissive always-PASSED default.
- **A local model (Ollama/vLLM) reports `NOT_EVALUATED` instead of `$0.00`** (Phase 3 B1) → expected, fail-closed behavior since Ollama Cloud (paid) shares the same prefix as local Ollama — set `ADK_TRACEGAUGE_ASSUME_LOCAL` to opt in.
- **`tracegauge check` refuses to run at all (`exit code 3`)** at a smaller eval-set size than expected → this is `--min-n`'s refusal, not a bug; see "Known limitations" below for the measured detection-power reason `min_n=30` exists and why `--mode paired` may still work below it.

## Known limitations

These are real, current, and worth knowing before you rely on this package — not hidden, just not the first thing you read.

- **The default two-sample regression gate does not reliably detect a realistic-magnitude cost regression at a realistic ADK eval-set size — measured, not assumed (Phase 3 B4).** At `n=25` (a realistic ADK eval-set size — this repo's own `examples/03_ci_regression_gate.py` uses `n=40`, deliberately just above `min_n=30`, because real ADK eval cases can involve real/expensive model calls, so teams keep eval sets to tens of cases, not hundreds), **the default two-sample gate detects a true 10% cost regression only 69% of the time, and refuses to run at all below `n=30`'s own `min_n` floor — treat a clean two-sample result at small `n` with real skepticism.** The full measured power grid (200–250 trials per cell, seed=42) is in `PLAN.md`'s Phase 3 B4 entry. **If your eval harness pins a stable `session_id` per eval case** (`runner.run_async(session_id=...)`), **`tracegauge check --mode paired`** (or the `auto` default, which uses it automatically whenever enough `session_id`s overlap) **uses a paired comparison that is dramatically more sensitive at the same `n` whenever real per-case cost variance exists** — measured at `n=25` on a case-correlated generator, two-sample detected a real +$0.001/case regression on 0/200 trials while paired detected it on 200/200; paired's own false-positive rate at that `n` (5.5%) is close to but not identical to two-sample's (4.0%), flagged as worth a larger confirmatory run before treating paired as the default in a production-critical setting.
- **The above limitation is now surfaced at RUNTIME, every `tracegauge check` run, not only in this doc (Phase 4 R4).** Every run prints an `achieved power` line — the minimum effect size the bootstrap test could reliably (80% power, the same bar B4 used above) detect given THIS run's own observed variance and `n` — and an explicit `WARNING` whenever your configured `--min-effect-usd`/`--min-effect-pct` floor is smaller than that achievable floor (see the Quickstart output above for a real example: at `n=40`, `BASE_SD≈$0.0015`/mean≈$0.0086, the achievable floor is ~$0.000474/5.53%, ABOVE the default `$0.0001` floor, so the WARNING fires). The achieved-power figure is a normal-approximation to the bootstrap CI (bootstrap power has no closed form) — validated against B4/R2's own measured grid at 7 points, accurate to within 2–8 percentage points, worst at `n=25` (see `_regression.py`'s "Achieved statistical power" section for the full accuracy table and derivation). **`min_n=30` was explicitly re-examined (4.3), not left unchanged by default** — real measurement at n∈{30,35,40,45} (10% effect, B4's generator) showed 71.5%/79.0%/77.5%/83.0% detection, i.e. `n=30` genuinely doesn't clear 80% for this scenario either — but **kept at 30 anyway**: no single `min_n` generically solves "80% power for the regression size YOU care about" (that depends on your own cost variance and threshold, which this package cannot know in advance — B4's own grid shows even `n=100` only clears 64.5% for a 5% effect), so raising it would just trade real signal on legitimate 30–44-invocation eval sets for a false sense of a "fixed" problem. The runtime achieved-power/warning mechanism above is the actually-general fix. **False-positive rate at `n=30` (`min_n`, the SHIPPED default configuration — real confidence/floors/`n_boot`, not the isolated grid above) measured at 500 trials: 4.60% (23/500); independent re-check, different seed, 500 trials: 4.20% (21/500)** — both above the ~2.5% nominal expectation, because at this variance level the 5%-relative practical floor is only ~1.3 sampling standard errors from zero at `n=30` and doesn't meaningfully suppress noise-driven false positives on its own. **A BCa (bias-corrected/accelerated) bootstrap was implemented as an experiment and empirically measured (4.5)**: no measurable improvement (percentile vs. BCa FPR: 6.00% vs. 5.33% at `n=10`, 3.00% vs. 3.33% at `n=25`, 300 trials each) — expected, since BCa's corrections target bias/skew in the bootstrap distribution, near-zero for a near-symmetric mean statistic on this project's cost data; NOT shipped. A studentized bootstrap was assessed but not built or tested — it needs a per-resample SE estimate that is known to be unstable at `n<20-30`, exactly the regime it would need to help in; see `_regression.py`'s "Anti-conservatism at small n" section for the full reasoning. This remains a real, honest, unresolved limitation at small `n`.
- **`AgentEvaluator.evaluate()`'s own pytest-style pass/fail exit is directionally unreliable for this metric, at any threshold.** `agent_evaluator.py::_process_metrics_and_get_failures` (google-adk 2.6.3, lines ~713-719) recomputes PASSED/FAILED itself from raw scores and the *deprecated* `EvalMetric.threshold` scalar via `mean(scores) >= threshold` — hardcoded higher-is-better, ignoring this evaluator's own correct `eval_status` entirely. `adk eval`/`LocalEvalService` are **unaffected** — they read this evaluator's real `eval_status` directly, always correctly. Trust `adk eval`/`LocalEvalService`, `tracegauge check`, or this evaluator's own `eval_status` (call `evaluate_invocations()` directly), for real pass/fail — never `AgentEvaluator.evaluate()`'s own assert/no-assert outcome for this metric. See `evaluator.py`'s module docstring for the full source-confirmed detail, and `tests/test_agent_evaluator_integration.py` for the permanent regression test documenting this.
  **As of Phase 3 B3, this is also a real runtime `warnings.warn`** (not only documentation) — it fires when this metric is actually evaluated under a real `AgentEvaluator.evaluate()` call, naming this exact behavior and the installed `google-adk` version. Detection uses a `contextvars.ContextVar` set for the duration of the call (installed as an `adk_tracegauge` import side effect), not a call-stack check — a stack walk was tried first and empirically fails, because `LocalEvalService.evaluate()` forks each eval case into its own `asyncio.Task`, which erases the physical call stack back to whichever caller awaited it, identically for `AgentEvaluator.evaluate()` and `adk eval`. **Known gap:** the very first `AgentEvaluator.evaluate()` call in a process won't trigger the warning if `adk_tracegauge` is imported for the first time as a side effect of *that same call* loading your agent module (the "Also" quickstart's own pattern) — the wrap installs a moment too late for a call already in progress. Workaround: `import adk_tracegauge` explicitly at the top of your eval driver script or `conftest.py`, ahead of any `AgentEvaluator.evaluate()` call — every call after the wrap is installed is detected correctly. See `evaluator.py`'s `_install_agent_evaluator_marker` docstring for the full mechanism and `tests/test_agent_evaluator_integration.py` for a subprocess-based regression test proving both the detection and the gap are real.
- **`adk eval`'s own process exit code doesn't reflect PASSED/FAILED** (see "Also" section above) — this is exactly why `tracegauge check`, not `adk eval`, is this README's hero path; use its exit code for CI gating, not `adk eval`'s.
- **`is_local_model()`'s Ollama Cloud gap requires an explicit opt-in, or a local model reports `NOT_EVALUATED`, not `$0.00`.** (Phase 3 B1.) Ollama Cloud is a real paid product sharing the identical `ollama_chat/`/`ollama/` LiteLlm prefix as local Ollama, and google-adk's `LlmResponse` schema carries no host/endpoint field to distinguish them — confirmed by reading `models/lite_llm.py` and `models/llm_response.py` directly. Set `ADK_TRACEGAUGE_ASSUME_LOCAL` (see "Pricing" above) to opt in explicitly; without it, this is fail-closed, not a silent $0.00.
- **Pricing scope is Gemini + Claude + GPT (current-generation) + local models only.** Bedrock/Vertex AI/Azure-routed Claude/GPT, older GPT-4/o-series models, and any other vendor are not built in — register a custom price via `ADK_TRACEGAUGE_PRICE_TABLE` or open an issue.
- **Standard (interactive/online) tier pricing only** — no Batch API support (ADK's live-agent plugin path never observes a Batch API call at all, confirmed by source grep).
- **Text/image/video input rate only** — audio input is priced at the text rate for every model, which under-charges audio-heavy invocations (documented per-model in each price entry's `note`).
- **Streaming behavior is documentation-corroborated, not independently confirmed against a live API call** (no Gemini API key was available when this was implemented) — see `_adapter.py`'s module docstring for the monotonicity check that makes this gap largely moot: a stream whose reported totals ever decrease, or that never reaches a final chunk, is refused pricing rather than trusted under a possibly-wrong assumption.
- **No calibrated efficiency bands, no trajectory-quality judging, no OTel span export** — see "What it reports" above and Roadmap/Phase 3 notes in `PLAN.md`.

## How PASSED/FAILED is computed (and why not ADK's built-in `>=`)

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better, and there is no lower-is-better/inverted-metric convention anywhere in `google.adk.evaluation` (checked directly against the source). So `CostEfficiencyEvaluator` computes PASSED/FAILED itself: `PASSED` when `cost <= threshold`, `FAILED` when `cost > threshold` (`threshold` from `CostThresholdCriterion`, preferred, or the deprecated `EvalMetric.threshold`). A `ValueError` is raised at construction time if neither is set — this package never falls back to a permissive always-PASSED default. An invocation whose cost couldn't be verified at all (no usage captured, unresolved model, a streaming anomaly, an unpriced token category) reports `NOT_EVALUATED`, not a fabricated pass/fail. `tracegauge check`'s own PASS/regression/insufficient-data verdict is a separate, statistical question — see "Quickstart" above and `_regression.py`'s module docstring.

## Relationship to tracegauge

This package depends on `tracegauge`'s `tes.cost` module (`compute_session_cost` and its digest types) as a library. `tracegauge` overall is AGPL-3.0-only, but `tes/cost.py` and `tes/_digest.py` specifically are additionally available under Apache-2.0 — see [tracegauge's license note](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer#license) — which is what lets this package stay Apache-2.0 itself. Every other part of tracegauge (the token-economy/trajectory/waste axes, the CLI, the dashboard) remains AGPL-3.0-only and is not used here.

## What this is not

Not a general ADK observability/tracing tool — it has no span export, no trace viewer, no OTel integration (see `traceAI-google-adk` or ADK's own native OTel support for that). Not a statistics/confidence-interval layer for ADK *evaluation quality* results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain); the bootstrap-CI machinery here is specifically about *cost* regression, not eval-score regression. Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them. Not a guaranteed-sensitive regression detector at any eval-set size — see "Known limitations" above for the measured, honest power caveats and the `--mode paired` mitigation.

## License

[Apache-2.0](LICENSE).
