# adk-tracegauge

A cost evaluator for **custom** [Google ADK](https://github.com/google/adk-python) eval harnesses: real per-invocation dollar cost, computed from actual token usage captured during inference, built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. Raw dollars and tokens, no calibrated bands, no fabricated numbers for unknown models.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/adk-tracegauge/)

---

## Read this first: this metric now gates `adk eval` / `AgentEvaluator.evaluate()` — with one caveat

**As of Phase 2 W2, this is a real threshold-based metric with a real PASSED/FAILED verdict, like ADK's other built-in metrics.** It requires a max-USD-per-invocation threshold at construction time: `EvalMetric(metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=<max_usd>))`. A priceable invocation always resolves to a real verdict; there is no path back to the old permanent `NOT_EVALUATED` for a model this package can price. Confirmed live against `google-adk==2.6.3` (see `docs/audit/` and the Phase 2 W2 commit for the full proof, including real `adk eval` CLI output and the persisted `eval_history/*.evalset_result.json`).

One real caveat, found while proving this end to end and **not fixable from this package**: `AgentEvaluator.evaluate()`'s pytest-style helper (`agent_evaluator.py::_process_metrics_and_get_failures`) recomputes PASSED/FAILED itself from raw scores and the deprecated `EvalMetric.threshold` scalar via `mean(scores) >= threshold` — hardcoded higher-is-better, ignoring this evaluator's own `eval_status`, and always populated the same way regardless of whether you configure a plain float or a criterion object in `test_config.json`. This means `AgentEvaluator.evaluate()`'s own assert/no-assert exit code is directionally unreliable for this metric at *any* threshold — a package-side sentinel can't silently correct it (a permissive `0.0` sentinel would make that harness's gate permanently PASS regardless of real cost, which is worse than the original bug: a "regression gate" that can never fire). `adk eval`/`LocalEvalService` are unaffected — they read this evaluator's real `eval_status` directly, correctly, always. **Trust `adk eval`/`LocalEvalService`, or this evaluator's own per-invocation `eval_status` (call `evaluate_invocations()` directly), for real pass/fail — never `AgentEvaluator.evaluate()`'s own assert outcome for this metric.** See `adk_tracegauge/evaluator.py`'s module docstring for the full detail and the source citations.

## What this actually is

A `TraceGaugeUsagePlugin` that captures real per-call token usage during inference (via `BasePlugin.after_model_callback`, the only place ADK exposes `usage_metadata`+`model_version` together), plus a `CostEfficiencyEvaluator` that turns captured usage into a priced, real PASSED/FAILED `PerInvocationResult`. Registers cleanly into ADK's `adk eval`/`AgentEvaluator.evaluate()` runner as of Phase 2 W2 (see "Read this first"); usage capture still requires either the hand-rolled `App`+plugin harness below or the `after_model_callback` workaround, since `AgentEvaluator`/`adk eval` build their own bare `Runner` that never fires an `App`-wired plugin.

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
from adk_tracegauge.evaluator import METRIC_NAME, CostThresholdCriterion

evaluator = CostEfficiencyEvaluator(
    eval_metric=EvalMetric(
        metric_name=METRIC_NAME,
        criterion=CostThresholdCriterion(threshold=0.05),  # max $0.05/invocation
    )
)
result = evaluator.evaluate_invocations(invocations)
```

**6. Read the score, verdict, and rationale straight out of the returned object:**

```python
for pir in result.per_invocation_results:
    print(pir.score, pir.eval_status, pir.rubric_scores[0].rationale)
print("total cost:", result.overall_score, result.overall_eval_status)
```

**Why a hand-rolled harness is still needed at all** (this part of the story is unchanged by Phase 2 W2): `AgentEvaluator`/`adk eval` build their *own* `Runner` internally and never look at the `App`/`plugins` you constructed in step 1 — so `TraceGaugeUsagePlugin` never fires and no usage is ever captured through that path, independent of the scoring fix below. What you lose by using this harness instead: every other built-in metric (`tool_trajectory_avg_score`, `response_match_score`, rubric-based judges, etc.), persistence to `eval_history/`, `num_runs` repetition, and parallelism across eval cases. If you want those *and* cost for the same conversations, you currently have to run inference twice — once through this harness, once through `AgentEvaluator`/`adk eval` — against a model that isn't guaranteed to behave identically both times. (The workaround below gets a real cost number *into* `adk eval` directly, trading away sub-agent cost rollup to do it.)

## Workaround for capturing usage inside `adk eval`/`AgentEvaluator`: `after_model_callback`

Since `AgentEvaluator`/`adk eval` never fire an `App`-wired plugin (see above — a usage-*capture* limitation, unrelated to the Phase 2 W2 scoring fix), attach the plugin's callback directly to your agent instead of wrapping it in an App if you want this metric to participate in a normal `adk eval` run:

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

This works because `agent.canonical_after_model_callbacks` (an ordinary `LlmAgent` field) fires independent of which Runner wraps the agent, unlike `BasePlugin.after_model_callback` — so it survives `AgentEvaluator`/`adk eval`'s hardcoded bare-Runner construction. Verified live against `google-adk==2.6.3`: the plugin does capture real usage this way, and `CostEfficiencyEvaluator` does compute the correct dollar figure and a real PASSED/FAILED verdict.

**As of Phase 2 W2, this now genuinely works end to end:** `AgentEvaluator.evaluate()` no longer raises unconditionally, and `adk eval` no longer nulls the per-invocation score/rationale — both read a real `score`/`eval_status`/rationale once this metric is given a threshold (see "Read this first" above for one remaining caveat specific to `AgentEvaluator.evaluate()`'s own pytest-style pass/fail helper).

One real limit specific to this workaround (unchanged by Phase 2 W2): `before_run_callback`/`after_run_callback` — what makes sub-agent delegation aggregate correctly (see "Sub-agent delegation" below) — are plugin-lifecycle hooks, invoked only through a Runner's `PluginManager`. Attaching just the bound `after_model_callback` method to `LlmAgent` bypasses that lifecycle entirely, so this workaround captures individual model calls but never correlates delegated sub-agent calls back to the parent. Only the full `App(plugins=[TraceGaugeUsagePlugin()])` wiring (the hand-rolled harness above) gets both.

## Diagnostics: `warnings.warn`, not rationale text alone

`rubric_scores`/rationale now reach a caller going through `AgentEvaluator`/`adk eval` for any invocation this metric could price (Phase 2 W2) — but LocalEvalService still substitutes empty per-invocation results for the *whole* eval case whenever this metric's case-level `overall_eval_status` is `NOT_EVALUATED` (which now only happens when literally nothing in that case could be priced — see `_aggregate_eval_status`'s docstring in `evaluator.py`). For that narrower remaining scenario, every diagnostic this package produces — "no usage captured", "unresolved model", the successful per-call cost breakdown, and the stale-price-table warning — is also emitted via `warnings.warn` at evaluate time, which does survive. If you're driving `CostEfficiencyEvaluator.evaluate_invocations()` directly, you always get both the rationale text *and* the warning.

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

## How PASSED/FAILED is computed (and why not ADK's built-in `>=`)

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better. There is no lower-is-better or inverted-metric convention anywhere in `google.adk.evaluation` to plug into (checked directly against the source, not assumed). Silently negating the score to make the built-in gate technically "work" would misrepresent the number to anyone reading it — a `score: -0.0043` needs an explanation to even parse.

So `CostEfficiencyEvaluator` computes PASSED/FAILED itself, directly, with the correct direction for a lower-is-better metric: `PASSED` when `cost <= threshold`, `FAILED` when `cost > threshold` (`threshold` from `CostThresholdCriterion`, preferred, or the deprecated `EvalMetric.threshold`). A `ValueError` is raised at construction time if neither is set — this package never falls back to a permissive always-PASSED default. An invocation whose cost couldn't be verified at all (no usage captured, unresolved model, a streaming anomaly, an unpriced token category) still reports `NOT_EVALUATED`, not a fabricated pass/fail — see `adk_tracegauge/evaluator.py`'s module docstring for the full design, including one ADK-side limitation (in `AgentEvaluator.evaluate()`'s pytest-style helper specifically, not `adk eval`/`LocalEvalService`) this package works around but cannot fully fix.

## Gemini pricing

`tracegauge`'s bundled price table covers Claude models only (its own domain — Claude Code sessions). ADK is Gemini-native, so this package ships and owns its own Gemini price table (`src/adk_tracegauge/data/gemini_prices.json`), passed explicitly into tracegauge's cost engine.

- Every entry carries its own `source_url` and `fetched_on` date — currently `https://ai.google.dev/gemini-api/docs/pricing`, re-verified 2026-08-14 against every model's live published rate (Phase 2 W1 price-correctness audit; see `docs/audit/PHASE1_DIAGNOSIS.md` for why this had never been done before). **Prices change without notice.** Verify against the source before relying on a number for a real budget decision.
- Standard (interactive/online) tier only — no Batch API pricing. Confirmed genuinely out of scope, not just unimplemented: ADK's live-agent plugin path (`BasePlugin.after_model_callback`) never observes a Batch API call at all (Batch is a separate async job-submission surface that doesn't flow through `Runner`/plugins — confirmed by source grep of `google-adk`'s `models`/`plugins` modules), so there is no code path where a batch call could be mispriced as interactive.
- Covers `gemini-2.5-pro` (+ its long-context tier), `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash` (deprecated, kept for historical sessions), `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview` (+ its long-context tier). Text/image/video input rate only — several models price audio input higher; audio calls are priced at the text rate, which under-charges audio-heavy invocations (documented per-model in each entry's `note`).
- **Long-context tiering is modeled** (schema_version 2): `gemini-2.5-pro` and `gemini-3.1-pro-preview` bill roughly double their base rate once a single call's `prompt_token_count` exceeds 200,000 tokens. `_pricing.resolve_model_for_call(model_version, prompt_token_count)` — not plain `resolve_model` — applies this, re-resolving to a separate synthetic table entry (`<model>-long-context`) with its own rates/provenance. Fixes the previously-documented "known gap" (Phase 1 flagged this as unmodeled; this was the P0 finding Phase 2 W1 fixed first).
- Cache-read discount is `0.1x` the model's fresh input rate for every model, at every tier, re-verified 2026-08-14 (10 models cross-checked against the published cache-read rate, including both long-context tiers — every one divides out to exactly 0.1x of that tier's own input rate). Correctness also depends on Gemini's `prompt_token_count` already including cached tokens (confirmed directly from the installed `google-genai` SDK's `GenerateContentResponseUsageMetadata` docstring) — so subtracting `cache_read` from `prompt_token_count` to get "fresh" tokens is not double-billing. Cache-*write* multipliers are `0.0`: Gemini's default automatic caching has no write-time surcharge and no `cache_creation` token field in its usage metadata at all, unlike Anthropic's explicit cache-write billing.
- **Thinking tokens are billed as output** (`thoughts_token_count` in Gemini's usage metadata, folded into `token_count_output` alongside `candidates_token_count` in `_adapter.py`). Missing this was a real undercount for any Gemini 2.5+ call with thinking enabled — on a reasoning-heavy turn, thinking tokens can be the majority of the true output bill.
- **Server-side built-in tool tokens are refused, not silently dropped or guessed.** Gemini's `tool_use_prompt_token_count` (tokens from server-side built-in tools like Google Search grounding or code execution, fed back to the model within the same call) has no publicly documented billing rate this package could verify. Rather than fabricate a number, any call reporting this field nonzero reports `score=None` with an explicit rationale — see `AdaptResult.unpriced_component`. Ordinary client-orchestrated function/tool calling (ADK's default, and what this package's own tests/smoke-tests exercise) never sets this field, so this only affects agents configured with Gemini's built-in tools.

**An invocation whose model isn't in this table is never priced with a fallback rate.** `score` reports `None`, and the rationale (or, going through ADK's eval runner, the warning — see "Diagnostics" above) names exactly which model string didn't resolve and lists every model this package knows how to price. A cost number for the wrong model is worse than no number, so this package doesn't produce one.

Every priced result's rationale also carries a `price_as_of=<date(s)>` line — the `fetched_on` date(s) of every price-table entry actually used for that invocation — so the number's provenance travels with it even through the one channel guaranteed to survive ADK's eval-result discarding (`warnings.warn`, see "Read this first" above).

### Staleness — what happens when Gemini's prices change and the table doesn't

A per-entry `source_url`/`fetched_on` date is provenance, not a freshness guarantee — nothing stops the table from silently aging out unless something actually checks it. Two independent things do:

- **At use time**: every `ResolvedModel.is_stale` check compares `fetched_on` against today; past 90 days (`STALE_THRESHOLD_DAYS` in `_pricing.py`, tightened from 180 in Phase 2 W1 after a live finding — a promotional rate scheduled to change on a fixed calendar date was stale-by-construction under the old window), a priced result's `rationale` gets a `PRICE TABLE STALE` line naming the model and threshold, and a real `warnings.warn(...)` fires alongside it — visible to anyone watching logs, not only whoever reads that one invocation's output. Staleness never blocks the number; it warns and still reports it, on the position that a flagged-possibly-wrong number beats no number for something you already computed.
- **In CI, at commit time**: `test_bundled_table_is_not_currently_stale` (`tests/test_pricing.py`) re-checks every bundled entry against the same 90-day threshold on every push. Once the table crosses it, this test starts failing on its own — not only when someone happens to notice a suspicious dollar figure.
- **In CI, on a schedule**: `.github/workflows/price-freshness.yml` runs `scripts/check_price_freshness.py` weekly (plus `workflow_dispatch`) even with no new commits — the commit-time test above only re-checks staleness when someone happens to push, so a table that goes stale during a quiet period would otherwise go unnoticed until the next unrelated commit. Pure date arithmetic against the JSON file; zero network/API calls.

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

Not a general ADK observability/tracing tool — see `traceAI-google-adk` for that. Not a statistics/confidence-interval layer for ADK evaluation results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain). Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them. Not a metric that captures usage on its own inside `AgentEvaluator`/`adk eval` without the hand-rolled harness or `after_model_callback` workaround above — see "Read this first".

## License

[Apache-2.0](LICENSE).
