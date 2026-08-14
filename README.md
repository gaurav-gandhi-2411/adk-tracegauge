# adk-tracegauge

Register one metric, get a real per-invocation **USD cost** with a **PASS/FAIL threshold verdict** inside `adk eval`, and a **CI gate that fails on statistically significant cost regression** — for [Google ADK](https://github.com/google/adk-python) agents. Built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. Raw dollars and tokens, no calibrated bands, no fabricated numbers for unknown models.

[![PyPI](https://img.shields.io/pypi/v/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![CI](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/adk-tracegauge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/adk-tracegauge.svg)](https://pypi.org/project/adk-tracegauge/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

---

## Quickstart

```bash
pip install adk-tracegauge
```

**1. Wire the plugin into your agent, and register the metric with a threshold.**

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

**2. Run `adk eval`, exactly as you would for any other metric:**

```bash
adk eval my_agent_module my_eval_set.json --config_file_path test_config.json --print_detailed_results
```

**3. See a real dollar figure and a real PASS/FAIL verdict.** Below is the actual, unedited output of the two runs in `examples/01_minimal_cost_gate.py` — same fixture, run once with a threshold above the real cost and once below it (see that file for the full runnable script):

```
Overall Eval Status: PASSED
Metric: adk_tracegauge_cost_usd, Status: PASSED, Score: 2.8, Threshold: 5.0
```

```
Overall Eval Status: FAILED
Metric: adk_tracegauge_cost_usd, Status: FAILED, Score: 2.8, Threshold: 1.0
```

That's it — no hand-rolled `Runner`, no private ADK internals, no `EvaluationGenerator` call. This is the package's own primary integration path, and it was measured, not estimated: **4 lines of adk-tracegauge-specific Python code** (`import adk_tracegauge`, `from adk_tracegauge import TraceGaugeUsagePlugin`, `_usage_plugin = TraceGaugeUsagePlugin()`, and the `after_model_callback=` wiring) **plus 1 line of threshold config**, and the full two-run proof above (both `adk eval` invocations, cold-start `uv`/ADK import overhead included) took **31.6 seconds wall-clock** (`examples/01_minimal_cost_gate.py`, this session, `google-adk==2.6.3`). No API key, no live network call, no paid usage — the example's model is a deterministic fake double so the number reproduces exactly on every run; swap in a real `model="gemini-2.5-flash"` string, or a `LiteLlm`-wrapped local Ollama model, to price a real call the same way.

**One real thing worth knowing before you wire this into CI:** `adk eval`'s own *process exit code* does not reflect PASSED/FAILED — verified live, it's `0` in both runs above, regardless of the printed verdict. The real result lives in `adk eval`'s stdout table and the persisted `eval_history/*.evalset_result.json`, not in `$?`. This is exactly why the CI regression gate below (`tracegauge check`) is a separate step with its own real, distinguishable exit codes — don't gate a CI job on `adk eval`'s exit code alone.

## The CI cost-regression gate

The differentiator this package exists for: a `tracegauge check --baseline` command with **real, distinguishable exit codes** (unlike `adk eval` above), backed by a percentile bootstrap on the difference in per-invocation mean cost — not a naive point-estimate delta.

```bash
tracegauge snapshot --entrypoint my_eval_suite:run_and_return_store --output current.json
tracegauge check --baseline eval_baselines/cost_baseline.json --current current.json
```

Real output from `examples/03_ci_regression_gate.py` (40 synthetic invocations per group, a genuine +20%-mean injected regression):

```
tracegauge check: n_baseline=40 n_current=40 (min_n=30)
  mean_baseline=$0.008583  mean_current=$0.009998
  observed effect: +0.001415 USD (+16.49%), 95% CI [+0.001085, +0.001744] (n_boot=10000, seed=42)
  statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the configured practical-significance floor.
tracegauge check exit code: 1
```

And a real passing run (same baseline, a second independent sample from the *same* distribution — no injected regression):

```
tracegauge check: n_baseline=40 n_current=40 (min_n=30)
  mean_baseline=$0.008583  mean_current=$0.008548
  observed effect: -0.000035 USD (-0.41%), 95% CI [-0.000372, +0.000294] (n_boot=10000, seed=42)
  statistically_significant=False practically_significant=False (floors: min_effect_usd=0.000100 OR min_effect_pct=5.00%)
  PASS: no regression clearing both the statistical and practical bars.
```

Exit codes: `0` = pass, `1` = regression (statistically AND practically significant), `3` = insufficient data (fewer than `--min-n`, default 30, invocations in either group — refuses to emit a statistically meaningless verdict). A full copy-pasteable GitHub Actions workflow (snapshot → compare → fail the build on regression) lives at [`docs/ci-snippet.md`](docs/ci-snippet.md); the full statistical methodology (percentile bootstrap, the n≥30 rationale, why a regression needs BOTH statistical and practical significance) lives in `adk_tracegauge._regression`'s module docstring.

## Examples

Three runnable, independently-verified scripts under [`examples/`](examples/):

1. [`01_minimal_cost_gate.py`](examples/01_minimal_cost_gate.py) — the quickstart above, as a standalone script.
2. [`02_subagent_rollup.py`](examples/02_subagent_rollup.py) — a real two-agent `AgentTool` delegation, showing the parent+child dollar rollup (`$0.565` combined, verified against the price table by hand).
3. [`03_ci_regression_gate.py`](examples/03_ci_regression_gate.py) — the CI gate above, end to end (`tracegauge snapshot` + `tracegauge check` as real subprocesses).

Each has a header comment stating exactly how to run it and what output to expect — and each was actually run, not just written, before being committed.

## What this actually is

A `TraceGaugeUsagePlugin` that captures real per-call token usage during inference (via `BasePlugin.after_model_callback`, the only place ADK exposes `usage_metadata`+`model_version` together), plus a `CostEfficiencyEvaluator` that turns captured usage into a priced, real `PASSED`/`FAILED` `PerInvocationResult` against a required max-USD-per-invocation threshold. Registers cleanly into ADK's `adk eval`/`AgentEvaluator.evaluate()` runner. Usage capture requires either the `after_model_callback` wiring above (works with `adk eval`/`AgentEvaluator` directly, the quickstart path) or a hand-rolled `App`+plugin harness (below — needed only for full sub-agent cost rollup or calling `evaluate_invocations()` yourself, outside `adk eval`).

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
- **No calibrated efficiency bands.** tracegauge's own token-economy axis compares your numbers against a baseline built from 75 Claude Code sessions. That baseline is not used here, on purpose — applying a Claude-Code-derived baseline to ADK agent behavior would be an unvalidated transfer. This package reports raw counts and dollars only; set your own thresholds for what "too expensive" means for your agent.
- **No trajectory-quality judging.** Out of scope — CC-specific tooling, unrelated to the cost story.

## Pricing: Gemini, Claude, GPT, and local models

`tracegauge`'s bundled price table covers Claude models only (its own domain — Claude Code sessions). This package ships and owns its own multi-provider price table (`src/adk_tracegauge/data/gemini_prices.json` — historically Gemini-only, hence the name; kept as-is rather than renamed, see `_pricing.py`'s module docstring), covering:

- **Gemini** (ADK's native backend): `gemini-2.5-pro` (+ long-context tier), `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash` (deprecated, kept for historical sessions), `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview` (+ long-context tier).
- **Claude and GPT**, reached through ADK's `LiteLlm` integration (`model="anthropic/claude-opus-5"`, `model="openai/gpt-5.1"`, etc.): `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.1`, `gpt-5`. Deliberately **not** the GPT-4/o-series family — their cache-read discount (0.25x–0.5x) diverges from every other entry's 0.1x, and this table has exactly one global cache-multiplier for the whole table with no per-model override; adding them would silently under-price cached calls by 2.5x–5x.
- **Local/self-hosted models** (Ollama, vLLM — `ollama_chat/...`, `ollama/...`, `vllm/...`) resolve automatically to a real, explicit zero-cost table entry — `cost_usd=0.000000`, trivially PASSED against any positive threshold, with a rationale line stating "local model, zero marginal cost" — not a silent default.
- **A custom-price extension mechanism**: set `ADK_TRACEGAUGE_PRICE_TABLE` to the path of a JSON file with the same schema (mirrors tracegauge's own `TES_PRICE_TABLE` pattern) to add or override entries — e.g. a model behind a paid gateway, or a Bedrock/Vertex AI/Azure-routed Claude/GPT model whose pricing differs from the first-party rate (deliberately not auto-resolved, since it can diverge).

**An invocation whose model isn't in the table is never priced with a fallback rate.** `score` reports `None` with a rationale/warning naming exactly which model string didn't resolve and every model this package knows how to price — a cost number for the wrong model is worse than no number.

Every entry carries its own `source_url` and `fetched_on` date, re-verified 2026-08-14 against each model's live published rate. **Prices change without notice** — verify against the source before relying on a number for a real budget decision. Two independent freshness guards: a per-entry `is_stale` check (past `STALE_THRESHOLD_DAYS=90`, warns loudly but still reports the number) at use time, and `.github/workflows/price-freshness.yml` running weekly in CI regardless of whether anyone's pushed a commit. See `_pricing.py`'s module docstring for the full detail (long-context tiering, cache-read discount verification, thinking-token billing, and the server-side built-in-tool tokens this package deliberately refuses to price rather than guess at).

## Compatibility risk

Registration uses `google.adk.evaluation.metric_evaluator_registry`, which google-adk marks `@experimental`. This package pins `google-adk[eval]>=2.6.0,<2.8.0` accordingly, re-validated on each bump — see `CHANGELOG.md`. If the registry API breaks in a future release, registration happens at import time as a side effect, so the failure mode is a loud, immediate error on `import adk_tracegauge`, not a silent no-op.

The hand-rolled sub-agent-rollup harness additionally depends on `EvaluationGenerator.convert_events_to_eval_invocations` — a non-public ADK internal with no `@experimental` marker at all (no stated breakage discipline whatsoever). **This package's own primary quickstart path (`after_model_callback` + `adk eval`) never calls this function** — confirmed by grep, nothing under `src/adk_tracegauge/` touches it; only the optional sub-agent-rollup harness pattern does. Because it's still needed for that one path, it's wrapped behind `adk_tracegauge._compat.convert_events_to_eval_invocations`, which runs a version check against this package's known-tested `google-adk` range and raises a clear, actionable `RuntimeError` — naming the installed version and exactly which integration path is affected — instead of a bare, unexplained `AttributeError`/`ImportError` if the internal has moved. See `_compat.py`'s module docstring and `tests/test_compat.py` for the version-guard tests, including a simulated unsupported-version case.

A scheduled CI job (`.github/workflows/pypi-canary.yml`) installs the *latest* `google-adk[eval]` release (ignoring the pin) and runs the full test suite weekly, so a break surfaces on a schedule rather than via a user bug report.

## Troubleshooting

Three real, live-triggered errors and their fixes — see [`docs/troubleshooting.md`](docs/troubleshooting.md) for the full text and context:

- **Wrong `google-adk` version installed** (outside the `>=2.6.0,<2.8.0` pin) → a loud `ModuleNotFoundError`/`RuntimeError` at import time, not a silent wrong answer.
- **Unknown/unresolvable model** → `score=None` plus an actionable warning naming every model this package can price.
- **Missing threshold** → a `ValueError` at construction time; this package never falls back to a permissive always-PASSED default.

## Known limitations

These are real, current, and worth knowing before you rely on this package — not hidden, just not the first thing you read.

- **`AgentEvaluator.evaluate()`'s own pytest-style pass/fail exit is directionally unreliable for this metric, at any threshold.** `agent_evaluator.py::_process_metrics_and_get_failures` (google-adk 2.6.3) recomputes PASSED/FAILED itself from raw scores and the *deprecated* `EvalMetric.threshold` scalar via `mean(scores) >= threshold` — hardcoded higher-is-better, ignoring this evaluator's own correct `eval_status` entirely. `adk eval`/`LocalEvalService` are **unaffected** — they read this evaluator's real `eval_status` directly, always correctly (this is the primary path documented above, and it's fully fixed with no caveats). Trust `adk eval`/`LocalEvalService`, or this evaluator's own `eval_status` (call `evaluate_invocations()` directly), for real pass/fail — never `AgentEvaluator.evaluate()`'s own assert/no-assert outcome for this metric. See `evaluator.py`'s module docstring for the full source-confirmed detail, and `tests/test_agent_evaluator_integration.py` for the permanent regression test documenting this.
- **`adk eval`'s own process exit code doesn't reflect PASSED/FAILED** (see Quickstart above) — use `tracegauge check`'s exit code for CI gating, not `adk eval`'s.
- **Pricing scope is Gemini + Claude + GPT (current-generation) + local models only.** Bedrock/Vertex AI/Azure-routed Claude/GPT, older GPT-4/o-series models, and any other vendor are not built in — register a custom price via `ADK_TRACEGAUGE_PRICE_TABLE` or open an issue.
- **Standard (interactive/online) tier pricing only** — no Batch API support (ADK's live-agent plugin path never observes a Batch API call at all, confirmed by source grep).
- **Text/image/video input rate only** — audio input is priced at the text rate for every model, which under-charges audio-heavy invocations (documented per-model in each price entry's `note`).
- **Streaming behavior is documentation-corroborated, not independently confirmed against a live API call** (no Gemini API key was available when this was implemented) — see `_adapter.py`'s module docstring for the monotonicity check that makes this gap largely moot: a stream whose reported totals ever decrease, or that never reaches a final chunk, is refused pricing rather than trusted under a possibly-wrong assumption.
- **No calibrated efficiency bands, no trajectory-quality judging, no OTel span export** — see "What it reports" above and Roadmap/Phase 3 notes in `PLAN.md`.

## How PASSED/FAILED is computed (and why not ADK's built-in `>=`)

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better, and there is no lower-is-better/inverted-metric convention anywhere in `google.adk.evaluation` (checked directly against the source). So `CostEfficiencyEvaluator` computes PASSED/FAILED itself: `PASSED` when `cost <= threshold`, `FAILED` when `cost > threshold` (`threshold` from `CostThresholdCriterion`, preferred, or the deprecated `EvalMetric.threshold`). A `ValueError` is raised at construction time if neither is set — this package never falls back to a permissive always-PASSED default. An invocation whose cost couldn't be verified at all (no usage captured, unresolved model, a streaming anomaly, an unpriced token category) reports `NOT_EVALUATED`, not a fabricated pass/fail.

## Relationship to tracegauge

This package depends on `tracegauge`'s `tes.cost` module (`compute_session_cost` and its digest types) as a library. `tracegauge` overall is AGPL-3.0-only, but `tes/cost.py` and `tes/_digest.py` specifically are additionally available under Apache-2.0 — see [tracegauge's license note](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer#license) — which is what lets this package stay Apache-2.0 itself. Every other part of tracegauge (the token-economy/trajectory/waste axes, the CLI, the dashboard) remains AGPL-3.0-only and is not used here.

## What this is not

Not a general ADK observability/tracing tool — it has no span export, no trace viewer, no OTel integration (see `traceAI-google-adk` or ADK's own native OTel support for that). Not a statistics/confidence-interval layer for ADK *evaluation quality* results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain); the bootstrap-CI machinery here is specifically about *cost* regression, not eval-score regression. Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them.

## License

[Apache-2.0](LICENSE).
