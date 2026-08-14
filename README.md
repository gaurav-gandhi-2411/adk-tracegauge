# adk-tracegauge

A cost evaluator for **custom** [Google ADK](https://github.com/google/adk-python) eval harnesses: real per-invocation dollar cost, computed from actual token usage captured during inference, built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. Raw dollars and tokens, no calibrated bands, no fabricated numbers for unknown models.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/adk-tracegauge/)

---

## Read this first: `adk eval` and `AgentEvaluator.evaluate()` cannot surface this metric's output

**This is not a drop-in metric for your normal ADK eval runs.** Confirmed against `google-adk==2.6.3`:

- `AgentEvaluator.evaluate()` **raises `AssertionError` unconditionally** whenever `adk_tracegauge_cost_usd` is present in your eval config's `criteria` — regardless of the actual computed cost or the threshold you configure. There is no threshold that avoids this.
- `adk eval` (the CLI) does not raise, but discards the per-invocation score and rationale entirely — both the printed per-invocation table and the persisted `eval_history/*.evalset_result.json` show `score: null`. Only one coarse, un-persisted console line (the run's aggregate score) carries a real number.

The root cause is in ADK itself, not in this package: `LocalEvalService._evaluate_metric_for_eval_case` (`local_eval_service.py:428-436`) discards a metric's real per-invocation result and substitutes an empty one whenever that metric's `eval_status` is `EvalStatus.NOT_EVALUATED` — and `NOT_EVALUATED` is this metric's permanent status, by design: cost is lower-is-better, and misusing ADK's `score >= threshold -> PASSED` convention to force a pass/fail verdict on a dollar figure would misrepresent the number to anyone reading it. ADK's `Evaluator` contract currently has no shape for "measures something real, but isn't pass/fail" — filed upstream as a design question: **[google/adk-python#6725](https://github.com/google/adk-python/issues/6725)**.

Until that's resolved, this package works through a **hand-rolled Runner harness you build and drive yourself** — not through `adk eval` or `AgentEvaluator`. That's a smaller claim than earlier versions of this README made, and it's the honest one. See below for what that actually looks like, and for a documented (but not supported) partial workaround.

## What this actually is

A `TraceGaugeUsagePlugin` that captures real per-call token usage during inference (via `BasePlugin.after_model_callback`, the only place ADK exposes `usage_metadata`+`model_version` together), plus a `CostEfficiencyEvaluator` that turns captured usage into a priced `PerInvocationResult` you read directly out of Python — a library for building your own cost-tracking eval harness, not a metric you register into ADK's dataset-driven eval runner and expect to work end to end unattended.

## Install

```bash
pip install "adk-tracegauge"
```

`google-adk[eval]` and `tracegauge` are pulled in as dependencies. The `[eval]` extra on `google-adk` is not optional here even though this package doesn't use any of what it gates (pandas, jinja2, rouge-score, gepa, Vertex AI eval) — `google-adk`'s own `metric_evaluator_registry.py` unconditionally imports every one of its built-in evaluators at module import time, including the Vertex AI facade, which needs those packages just to import cleanly. Without the extra, `import adk_tracegauge` fails with `ModuleNotFoundError: No module named 'pandas'` — an ADK packaging quirk, not something this package controls.

## The only path that reliably works: a hand-rolled Runner harness

This is what `tests/test_e2e_runner.py` in this repo actually does, written out as a worked example. Six steps, all of them necessary:

**1. Build your own `App` + `Runner`, with the plugin wired in.**

```python
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.runners import InMemoryRunner
from adk_tracegauge import TraceGaugeUsagePlugin

root_agent = LlmAgent(name="my_agent", model="gemini-2.5-flash", instruction="...")
app = App(name="my_app", root_agent=root_agent, plugins=[TraceGaugeUsagePlugin()])
runner = InMemoryRunner(app=app)
```

This part is unaffected by the issue above — a plugin you wire into your *own* App fires correctly on every real model call. The problem is specifically that `AgentEvaluator`/`adk eval` build their *own* Runner internally and never look at this `app` object at all.

**2. Create a session.**

```python
session = await runner.session_service.create_session(app_name=app.name, user_id="eval_user")
```

**3. Drive the conversation yourself, turn by turn, for each eval case.** There is no user-simulator, no `num_runs` repetition, no parallelism across cases here — you write the loop:

```python
from google.genai import types as genai_types

events = []
async for event in runner.run_async(
    user_id="eval_user",
    session_id=session.id,
    new_message=genai_types.Content(parts=[genai_types.Part(text="what is 2+2?")], role="user"),
):
    events.append(event)
```

**4. Convert the collected `Event`s into `Invocation` objects using ADK's own internal helper.** `EvaluationGenerator.convert_events_to_eval_invocations` is the exact function `LocalEvalService` calls internally to do this — not a reimplementation this package maintains — but it is **not documented public API**. It lives in `google.adk.evaluation.evaluation_generator`, carries no stability guarantee, and could change or move without notice in any ADK release. This is the one piece of this harness with real breakage risk; there is no supported alternative:

```python
from google.adk.evaluation.evaluation_generator import EvaluationGenerator

invocations = EvaluationGenerator.convert_events_to_eval_invocations(events)
```

**5. Build the metric and evaluator, and score the invocations directly.**

```python
from google.adk.evaluation.eval_metrics import EvalMetric
from adk_tracegauge import CostEfficiencyEvaluator
from adk_tracegauge.evaluator import METRIC_NAME

evaluator = CostEfficiencyEvaluator(eval_metric=EvalMetric(metric_name=METRIC_NAME))
result = evaluator.evaluate_invocations(invocations)
```

**6. Read the score and rationale straight out of the returned object** — this in-memory path never touches `LocalEvalService`, so nothing here is discarded:

```python
for pir in result.per_invocation_results:
    print(pir.score, pir.rubric_scores[0].rationale)
print("total cost:", result.overall_score)
```

**What you lose by not going through `AgentEvaluator`/`adk eval`:** every other built-in metric (`tool_trajectory_avg_score`, `response_match_score`, rubric-based judges, etc.), persistence to `eval_history/`, `num_runs` repetition, and parallelism across eval cases. If you want those *and* cost for the same conversations, you currently have to run inference twice — once through this harness, once through `AgentEvaluator`/`adk eval` — against a model that isn't guaranteed to behave identically both times.

## Documented workaround (not the supported path): `after_model_callback`

If you need this metric to show up in an `adk eval` run *at all* — accepting real limitations, not as a fix — attach the plugin's callback directly to your agent instead of wrapping it in an App:

```python
from google.adk.agents.llm_agent import LlmAgent
from adk_tracegauge import TraceGaugeUsagePlugin

_usage_plugin = TraceGaugeUsagePlugin()
root_agent = LlmAgent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="...",
    after_model_callback=_usage_plugin.after_model_callback,
)
```

This works because `agent.canonical_after_model_callbacks` (an ordinary `LlmAgent` field) fires independent of which Runner wraps the agent, unlike `BasePlugin.after_model_callback` — so it survives `AgentEvaluator`/`adk eval`'s hardcoded bare-Runner construction. Verified live against `google-adk==2.6.3`: the plugin does capture real usage this way, and `CostEfficiencyEvaluator` does compute the correct dollar figure internally.

**But it does not fix the underlying problem:**
- `AgentEvaluator.evaluate()` **still raises unconditionally** — the NOT_EVALUATED-nulling bug happens after scoring, regardless of how usage was captured.
- `adk eval` still discards the per-invocation score/rationale (`score: null` in the table and in `eval_history/*.json`) — you only get the one aggregate console line, and it isn't written anywhere.

Net effect: this gets you a single correct dollar figure printed to the console during an `adk eval` run, and nothing else — no per-call breakdown, no persistence, and it's still incompatible with `AgentEvaluator.evaluate()`. Treat it as a narrow, caveated option, not something to build a workflow around. The hand-rolled harness above is the reliable path.

One more limit specific to this workaround: `before_run_callback`/`after_run_callback` — what makes sub-agent delegation aggregate correctly (see "Sub-agent delegation" below) — are plugin-lifecycle hooks, invoked only through a Runner's `PluginManager`. Attaching just the bound `after_model_callback` method to `LlmAgent` bypasses that lifecycle entirely, so this workaround captures individual model calls but never correlates delegated sub-agent calls back to the parent. Only the full `App(plugins=[TraceGaugeUsagePlugin()])` wiring gets both.

## Diagnostics: `warnings.warn`, not rationale text

Because `LocalEvalService` discards `rubric_scores`/rationale for this metric unconditionally (see above), nothing written there is guaranteed to reach a caller going through `AgentEvaluator`/`adk eval`. So every diagnostic this package produces — "no usage captured", "unresolved model", the successful per-call cost breakdown, and the stale-price-table warning — is also emitted via `warnings.warn` at evaluate time, which does survive. If you're driving `CostEfficiencyEvaluator.evaluate_invocations()` directly (the hand-rolled harness above), you get both the rationale text *and* the warning; if you're going through ADK's own eval runner, the warning is the only channel that reaches you.

## What it reports, and what it deliberately doesn't

- **`score`**: raw cost in USD for the invocation, summed across every real model call within it (tool loops and sub-agent delegation can mean more than one model call per invocation). Not normalized, not calibrated, not a 0–1 quality score.
- **`rationale`**: a per-call breakdown — model, fresh/cached/output token counts, and their individual dollar costs. See "Diagnostics" above for where this actually ends up.
- **No calibrated efficiency bands.** tracegauge's own token-economy axis compares your numbers against a baseline built from 75 Claude Code sessions. That baseline is not used here, on purpose — applying a Claude-Code-derived baseline to ADK agent behavior would be an unvalidated transfer, and a plausible-looking-but-wrong number is worse than no number. This package reports raw counts and dollars only; set your own thresholds for what "too expensive" means for your agent.
- **No trajectory-quality judging.** tracegauge's Ollama/Anthropic-based trajectory axis is out of scope for v1 — it's CC-specific tooling, unrelated to the cost story, and would add a dependency this package doesn't need.

## Sub-agent delegation (`AgentTool`)

`AgentTool` (agent-as-a-tool delegation) builds a brand-new `Runner` internally, so a delegated sub-agent's real model calls land under a different `invocation_id` than the parent's — one ADK's own eval-event conversion never surfaces on the parent invocation. Earlier versions of this package silently dropped that cost from the reported total (a genuinely wrong number, not a missing one) rather than either aggregating it correctly or refusing to report.

As of this version, it's aggregated correctly, not detected-and-refused: `TraceGaugeUsagePlugin` implements `before_run_callback`/`after_run_callback`, which fire once each around every `Runner.run_async()` call and bracket that invocation's whole lifetime. Because `AgentTool.run_async` reuses the *same plugin instances* from the parent Runner by default (`include_plugins=True`), the plugin directly observes the nesting — when a new invocation starts while another is still active on the same instance, the new one is recorded as a child of the active one (a `contextvars.ContextVar`-backed stack, safe under both nested awaits and concurrent sibling invocations — see `_plugin.py`'s module docstring for why a plain instance attribute wouldn't be). `CostEfficiencyEvaluator` then sums the parent's own calls plus every recorded descendant's calls (recursively, so nested delegation — a sub-agent that itself delegates further — aggregates too) into one total. Verified live: a root agent delegating one call to a sub-agent via `AgentTool` now reports the correct combined cost, not just the root's own share.

**What this doesn't cover:** if a delegation pattern doesn't share the plugin instance with the parent (`AgentTool(..., include_plugins=False)`, or any other sub-Runner construction this package doesn't know about), there's no lifecycle signal to observe the nesting from, and that sub-portion's cost is invisible to this package — the same as any other "plugin never wired in" gap, not a new failure mode. This package does not attempt to guess at a correlation it can't observe (e.g. by flagging "extra" invocation_ids sitting in a shared store) — that heuristic would misfire constantly in ordinary usage, since a shared `UsageStore` legitimately accumulates entries from many unrelated invocations across an eval run.

## Streaming

Gemini's `generateContentStream` reports `usage_metadata` on every streamed chunk, not just the final one, with token counts growing cumulatively — **this is documentation-corroborated, not independently confirmed against a live API call in this package's own testing** (no Gemini API key was available when this was implemented). Sources: Google's own field docs describe `candidatesTokenCount` as "the total number of tokens... across all the generated response candidates" (consistent with, though not conclusive of, a running total); two independent third-party technical references both describe the same specific behavior in detail, one with a concrete raw SSE example across three chunks. If you can settle this definitively against a live call, please open an issue or PR — a few cents of API spend would close this out properly.

Earlier versions of this package recorded every streamed chunk as if it were a separate real model call, overcounting cost by roughly the number of chunks in the response (confirmed in testing: a 3-chunk simulated stream reported 2.03x the true cost). As of this version, `CapturedCall` carries ADK's own `partial` flag (set by `base_llm_flow`'s streaming aggregator — `True` for an intermediate chunk, `False` for a call's final response, streamed or not), and `_adapter.py` groups consecutive `partial=True` chunks with their terminating `partial=False` response into a single priced call, using only the final chunk's own reported totals.

That grouping doesn't blindly trust the "cumulative total" assumption above — it verifies it, every time: **token counts within a group must be monotonically non-decreasing, or the whole invocation is refused rather than priced under a broken assumption.** If a later chunk's `total_token_count` is ever *less* than an earlier one's, `score` reports `None` with a rationale (and a `warnings.warn`) naming exactly what regressed and between which values — never a number computed by silently taking "the last chunk" when the ordering assumption it depends on has already been observed to not hold. A stream that never reaches a final (non-partial) chunk — an interrupted response — is refused the same way, since its true total is genuinely unknown. This makes the documentation-vs-live-API gap above largely moot: if Gemini's real behavior ever differs from what's documented here, this check is what catches it, rather than this package silently trusting an unverified assumption forever.

## Why this metric always reports `NOT_EVALUATED`

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better. There is no lower-is-better or inverted-metric convention anywhere in `google.adk.evaluation` to plug into (checked directly against the source, not assumed). Silently negating the score to make the built-in gate technically "work" would misrepresent the number to anyone reading it — a `score: -0.0043` needs an explanation to even parse. So this metric doesn't participate in ADK's pass/fail gating: it always reports `eval_status=NOT_EVALUATED`, whatever threshold you configure.

This is also exactly the status value that triggers ADK's own per-invocation result discarding — see "Read this first" above and [google/adk-python#6725](https://github.com/google/adk-python/issues/6725). Read `score` directly (via the hand-rolled harness), or write your own threshold comparison against it — never rely on ADK's built-in pass/fail gate to expose it.

## Gemini pricing

`tracegauge`'s bundled price table covers Claude models only (its own domain — Claude Code sessions). ADK is Gemini-native, so this package ships and owns its own Gemini price table (`src/adk_tracegauge/data/gemini_prices.json`), passed explicitly into tracegauge's cost engine.

- Every entry carries its own `source_url` and `fetched_on` date — currently `https://ai.google.dev/gemini-api/docs/pricing`, fetched 2026-08-13. **Prices change without notice.** Verify against the source before relying on a number for a real budget decision.
- Standard tier only (no batch/flex/priority). Covers `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash` (deprecated, kept for historical sessions), `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`.
- **Known gap:** `gemini-2.5-pro` is priced at its ≤200k-token-context rate only. Gemini bills >200k-context Pro calls at roughly double that rate; this table has no per-call tiered-pricing logic, so long-context Pro invocations will be under-priced. Not silently wrong — documented here and in the price table's own per-entry `note` field.
- Cache-read discount is `0.1x` the model's fresh input rate for every model (verified against the published rate for each model individually, not assumed from tracegauge's Claude convention — it happens to match). Cache-*write* multipliers are `0.0`: Gemini's default automatic caching has no write-time surcharge and no `cache_creation` token field in its usage metadata at all, unlike Anthropic's explicit cache-write billing.

**An invocation whose model isn't in this table is never priced with a fallback rate.** `score` reports `None`, and the rationale (or, going through ADK's eval runner, the warning — see "Diagnostics" above) names exactly which model string didn't resolve and lists every model this package knows how to price. A cost number for the wrong model is worse than no number, so this package doesn't produce one.

### Staleness — what happens when Gemini's prices change and the table doesn't

A per-entry `source_url`/`fetched_on` date is provenance, not a freshness guarantee — nothing stops the table from silently aging out unless something actually checks it. Two independent things do:

- **At use time**: every `ResolvedModel.is_stale` check compares `fetched_on` against today; past 180 days (`STALE_THRESHOLD_DAYS` in `_pricing.py`), a priced result's `rationale` gets a `PRICE TABLE STALE` line naming the model and threshold, and a real `warnings.warn(...)` fires alongside it — visible to anyone watching logs, not only whoever reads that one invocation's output. Staleness never blocks the number; it warns and still reports it, on the position that a flagged-possibly-wrong number beats no number for something you already computed.
- **In CI**: `test_bundled_table_is_not_currently_stale` (`tests/test_pricing.py`) re-checks every bundled entry against the same 180-day threshold on every run. Once the table crosses it, this test starts failing on its own — not only when someone happens to notice a suspicious dollar figure.

**Updating the price table**: edit `src/adk_tracegauge/data/gemini_prices.json` — update `input_usd_per_mtok`/`output_usd_per_mtok` against the current values at the `source_url` already on each entry, and bump `fetched_on` to today. Run `pytest tests/test_pricing.py` to confirm the staleness test goes green and nothing else broke, then open a PR. There's no automated price-scraping here by design — a human should look at the actual pricing page before a dollar figure changes.

## Compatibility risk

Registration uses `google.adk.evaluation.metric_evaluator_registry`, which google-adk marks `@experimental`: "may change or be removed... at any time," with no SemVer guarantee. This package pins `google-adk[eval]>=2.6.0,<2.7.0` accordingly — narrow, re-validated deliberately on each bump, not left open-ended.

If the registry API breaks in a future google-adk release, registration happens at import time as a side effect, so the failure mode is a loud, immediate `AttributeError`/`TypeError` on `import adk_tracegauge` — not a silent no-op or a subtly wrong result. That's a favorable failure mode worth naming explicitly: you'll know immediately, not after a bad number reaches a dashboard.

The hand-rolled harness above additionally depends on `EvaluationGenerator.convert_events_to_eval_invocations` — an internal ADK helper with no `@experimental` marker at all (meaning no stated deprecation/breakage discipline whatsoever, not even the loose one `@experimental` implies) and no public-API status. This is a real, distinct risk on top of the registry one; there is currently no alternative to it for this harness shape.

A scheduled CI job (`.github/workflows/pypi-canary.yml`) installs the *latest* `google-adk[eval]` release (ignoring the pin) and runs the full test suite weekly, so a break surfaces on a schedule rather than via a user bug report.

The plugin half of this package (`BasePlugin.after_model_callback`) sits on firmer ground — it isn't marked `@experimental` anywhere in `google-adk`. If the registry API breaks, the usage-capture half likely still works; only evaluator registration needs a patch.

## Relationship to tracegauge

This package depends on `tracegauge`'s `tes.cost` module (`compute_session_cost` and its digest types) as a library. `tracegauge` overall is AGPL-3.0-only, but `tes/cost.py` and `tes/_digest.py` specifically are additionally available under Apache-2.0 — see [tracegauge's license note](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer#license) — which is what lets this package stay Apache-2.0 itself. Every other part of tracegauge (the token-economy/trajectory/waste axes, the CLI, the dashboard) remains AGPL-3.0-only and is not used here.

## What this is not

Not a general ADK observability/tracing tool — see `traceAI-google-adk` for that. Not a statistics/confidence-interval layer for ADK evaluation results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain) blocked by the same `EvalMetricResult` field-stripping this package works around by using `score`+`rationale` only. Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them. Not (currently) a drop-in `adk eval`/`AgentEvaluator` metric — see "Read this first" above.

## License

[Apache-2.0](LICENSE).
