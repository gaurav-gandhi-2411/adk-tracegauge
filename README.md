# adk-tracegauge

Per-invocation dollar cost for [Google ADK](https://github.com/google/adk-python) agent evaluations, built on [tracegauge](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer)'s cost engine. ADK's own `evaluation` package reports zero cost or token-efficiency data on any built-in metric — this fills exactly that gap, nothing more.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/adk-tracegauge/)

---

## Required: wrap your agent in an App with the plugin

**This is a hard requirement, not a caveat.** ADK's `Invocation` objects — what an evaluator normally receives — never carry token usage or model identity; ADK strips that down to author+content before an evaluator ever sees it. The real data lives on the `LlmResponse` passed to `BasePlugin.after_model_callback` during inference. That part fires for any real ADK run, App or not — but ADK's **eval harness specifically** (`LocalEvalService`, which `AgentEvaluator` and the `adk eval` CLI both sit on top of) only pulls plugins from `app.plugins` when you pass it an `App`. Evaluate against a bare `root_agent` with no `App`, and `LocalEvalService` builds its Runner with only its own internal eval plugins — **your plugins, including this one, are silently never included.** This package will report `no usage captured` for every single invocation, not an error.

```python
from adk_tracegauge import TraceGaugeUsagePlugin
from google.adk.apps import App

app = App(name="my_app", root_agent=root_agent, plugins=[TraceGaugeUsagePlugin()])
```

`App`'s constructor requires `name` — there's no default, and there's no `agent=` alias for `root_agent=`, easy typos to make since other ADK APIs use `agent`. Then reference `adk_tracegauge_cost_usd` as a `metric_name` in your eval config, and make sure `AgentEvaluator` picks up this `app` (it looks for an `app` attribute in your agent module automatically).

## Install

```bash
pip install "adk-tracegauge"
```

`google-adk[eval]` and `tracegauge` are pulled in as dependencies. The `[eval]` extra on `google-adk` is not optional here even though this package doesn't use any of what it gates (pandas, jinja2, rouge-score, gepa, Vertex AI eval) — `google-adk`'s own `metric_evaluator_registry.py` unconditionally imports every one of its built-in evaluators at module import time, including the Vertex AI facade, which needs those packages just to import cleanly. Without the extra, `import adk_tracegauge` fails with `ModuleNotFoundError: No module named 'pandas'` — an ADK packaging quirk, not something this package controls.

## What it reports, and what it deliberately doesn't

- **`score`**: raw cost in USD for the invocation, summed across every real model call within it (tool loops and sub-agent delegation can mean more than one model call per invocation). Not normalized, not calibrated, not a 0–1 quality score.
- **`rationale`** (the only other channel ADK's native reporting preserves — see "Why this metric always reports NOT_EVALUATED" below): a per-call breakdown — model, fresh/cached/output token counts, and their individual dollar costs.
- **No calibrated efficiency bands.** tracegauge's own token-economy axis compares your numbers against a baseline built from 75 Claude Code sessions. That baseline is not used here, on purpose — applying a Claude-Code-derived baseline to ADK agent behavior would be an unvalidated transfer, and a plausible-looking-but-wrong number is worse than no number. This package reports raw counts and dollars only; set your own thresholds for what "too expensive" means for your agent.
- **No trajectory-quality judging.** tracegauge's Ollama/Anthropic-based trajectory axis is out of scope for v1 — it's CC-specific tooling, unrelated to the cost story, and would add a dependency this package doesn't need.

## Why this metric always reports `NOT_EVALUATED`

ADK's built-in pass/fail convention is `PASSED if score >= threshold else FAILED` — hardcoded, higher-is-better. Cost is lower-is-better. There is no lower-is-better or inverted-metric convention anywhere in `google.adk.evaluation` to plug into (checked directly against the source, not assumed). Silently negating the score to make the built-in gate technically "work" would misrepresent the number to anyone reading it — a `score: -0.0043` needs an explanation to even parse. So this metric doesn't participate in ADK's pass/fail gating: it always reports `eval_status=NOT_EVALUATED`, whatever threshold you configure. Read `score` directly, or write your own comparison against it.

## Gemini pricing

`tracegauge`'s bundled price table covers Claude models only (its own domain — Claude Code sessions). ADK is Gemini-native, so this package ships and owns its own Gemini price table (`src/adk_tracegauge/data/gemini_prices.json`), passed explicitly into tracegauge's cost engine.

- Every entry carries its own `source_url` and `fetched_on` date — currently `https://ai.google.dev/gemini-api/docs/pricing`, fetched 2026-08-13. **Prices change without notice.** Verify against the source before relying on a number for a real budget decision.
- Standard tier only (no batch/flex/priority). Covers `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash` (deprecated, kept for historical sessions), `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`.
- **Known gap:** `gemini-2.5-pro` is priced at its ≤200k-token-context rate only. Gemini bills >200k-context Pro calls at roughly double that rate; this table has no per-call tiered-pricing logic, so long-context Pro invocations will be under-priced. Not silently wrong — documented here and in the price table's own per-entry `note` field.
- Cache-read discount is `0.1x` the model's fresh input rate for every model (verified against the published rate for each model individually, not assumed from tracegauge's Claude convention — it happens to match). Cache-*write* multipliers are `0.0`: Gemini's default automatic caching has no write-time surcharge and no `cache_creation` token field in its usage metadata at all, unlike Anthropic's explicit cache-write billing.

**An invocation whose model isn't in this table is never priced with a fallback rate.** `score` reports `None`, and the rationale names exactly which model string didn't resolve and lists every model this package knows how to price. A cost number for the wrong model is worse than no number, so this package doesn't produce one.

### Staleness — what happens when Gemini's prices change and the table doesn't

A per-entry `source_url`/`fetched_on` date is provenance, not a freshness guarantee — nothing stops the table from silently aging out unless something actually checks it. Two independent things do:

- **At use time**: every `ResolvedModel.is_stale` check compares `fetched_on` against today; past 180 days (`STALE_THRESHOLD_DAYS` in `_pricing.py`), a priced result's `rationale` gets a `PRICE TABLE STALE` line naming the model and threshold, and a real `warnings.warn(...)` fires alongside it — visible to anyone watching logs, not only whoever reads that one invocation's output. Staleness never blocks the number; it warns and still reports it, on the position that a flagged-possibly-wrong number beats no number for something you already computed.
- **In CI**: `test_bundled_table_is_not_currently_stale` (`tests/test_pricing.py`) re-checks every bundled entry against the same 180-day threshold on every run. Once the table crosses it, this test starts failing on its own — not only when someone happens to notice a suspicious dollar figure.

**Updating the price table**: edit `src/adk_tracegauge/data/gemini_prices.json` — update `input_usd_per_mtok`/`output_usd_per_mtok` against the current values at the `source_url` already on each entry, and bump `fetched_on` to today. Run `pytest tests/test_pricing.py` to confirm the staleness test goes green and nothing else broke, then open a PR. There's no automated price-scraping here by design — a human should look at the actual pricing page before a dollar figure changes.

## Compatibility risk

Registration uses `google.adk.evaluation.metric_evaluator_registry`, which google-adk marks `@experimental`: "may change or be removed... at any time," with no SemVer guarantee. This package pins `google-adk[eval]>=2.6.0,<2.7.0` accordingly — narrow, re-validated deliberately on each bump, not left open-ended.

If the registry API breaks in a future google-adk release, registration happens at import time as a side effect, so the failure mode is a loud, immediate `AttributeError`/`TypeError` on `import adk_tracegauge` — not a silent no-op or a subtly wrong result. That's a favorable failure mode worth naming explicitly: you'll know immediately, not after a bad number reaches a dashboard.

A scheduled CI job (`.github/workflows/pypi-canary.yml`) installs the *latest* `google-adk[eval]` release (ignoring the pin) and runs the full test suite weekly, so a break surfaces on a schedule rather than via a user bug report.

The plugin half of this package (`BasePlugin.after_model_callback`) sits on firmer ground — it isn't marked `@experimental` anywhere in `google-adk`. If the registry API breaks, the usage-capture half likely still works; only evaluator registration needs a patch.

## Relationship to tracegauge

This package depends on `tracegauge`'s `tes.cost` module (`compute_session_cost` and its digest types) as a library. `tracegauge` overall is AGPL-3.0-only, but `tes/cost.py` and `tes/_digest.py` specifically are additionally available under Apache-2.0 — see [tracegauge's license note](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer#license) — which is what lets this package stay Apache-2.0 itself. Every other part of tracegauge (the token-economy/trajectory/waste axes, the CLI, the dashboard) remains AGPL-3.0-only and is not used here.

## What this is not

Not a general ADK observability/tracing tool — see `traceAI-google-adk` for that. Not a statistics/confidence-interval layer for ADK evaluation results — that's a separate, harder problem ([agentgauge](https://github.com/gaurav-gandhi-2411/agentgauge)'s domain) blocked by the same `EvalMetricResult` field-stripping this package works around by using `score`+`rationale` only. Not a replacement for any of ADK's quality metrics — this reports cost alongside them, not instead of them.

## License

[Apache-2.0](LICENSE).
