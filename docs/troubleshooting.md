# Troubleshooting

Three real, live-triggered errors — the exact text this package produces,
captured directly (not reconstructed from memory or the source) during
Phase 2 W5 of this package's development. Each one is a deliberate
fail-closed design choice (see `PLAN.md`/README), not an accident: this
package would rather raise or refuse a number loudly than guess.

## 1. Wrong `google-adk` version installed

This package pins `google-adk[eval]>=2.6.0,<2.8.0` (see `pyproject.toml`,
"Compatibility risk" in the README). Installing a version well outside
that range breaks registration loudly, at `import adk_tracegauge` time —
never a silent no-op or a subtly wrong result.

**Reproduction** (a scratch venv, `adk-tracegauge` installed editable from
this repo, then `google-adk[eval]` force-installed at `1.0.0`, well below
the pin floor):

```
$ .venv/Scripts/python.exe -c "import adk_tracegauge"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "...\src\adk_tracegauge\__init__.py", line 31, in <module>
    from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY
ModuleNotFoundError: No module named 'google.adk.evaluation.metric_evaluator_registry'
```

**What happened:** `google-adk==1.0.0` predates the `metric_evaluator_registry`
module this package's own registration depends on — it simply doesn't
exist yet at that version. The failure is immediate and unambiguous:
`import adk_tracegauge` cannot succeed at all, which is the intended
failure mode (see README, "Compatibility risk" — "a loud, immediate error
... not a silent no-op").

**Fix:** `pip install "google-adk[eval]>=2.6.0,<2.8.0"` (or let
`adk-tracegauge`'s own dependency pin resolve it for you — this error only
happens when something else in your environment force-installs an
out-of-range version afterward, e.g. `pip install --upgrade google-adk`
without re-checking the pin). If you've deliberately upgraded past `2.8.0`
because a newer google-adk is out and you want to try it, check
`.github/workflows/pypi-canary.yml`'s latest run first (it installs the
*unpinned* latest `google-adk[eval]` weekly and runs the full test suite) —
if canary is green on your target version, the pin is just stale, not
actually broken; open an issue or a PR bumping it.

**Note on the hand-rolled sub-agent-rollup harness specifically:** if
you're on that path (not the primary `adk eval`/`after_model_callback`
quickstart) and the break is instead in
`EvaluationGenerator.convert_events_to_eval_invocations` (a separate,
non-public ADK internal with no version guarantee at all), you'll get a
different, equally actionable error from `adk_tracegauge._compat` — see
`_compat.py`'s module docstring and `tests/test_compat.py`.

## 2. Unknown/unresolvable model

Any model string that doesn't resolve against this package's price table
(and isn't a recognized local-model prefix) refuses to report a cost,
rather than fabricate one.

**Reproduction:**

```python
from adk_tracegauge._store import UsageStore, CapturedCall
from adk_tracegauge.evaluator import CostEfficiencyEvaluator, METRIC_NAME
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.genai import types as genai_types

store = UsageStore()
store.record(
    "inv-1",
    CapturedCall(
        model_version="some-totally-unknown-model-xyz",
        prompt_token_count=100,
        candidates_token_count=50,
        cached_content_token_count=0,
        total_token_count=150,
    ),
)
evaluator = CostEfficiencyEvaluator(
    eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1.0),
    store=store,
)
result = evaluator.evaluate_invocations(
    [Invocation(invocation_id="inv-1", user_content=genai_types.Content(parts=[]))]
)
```

**Real captured warning** (`warnings.warn`, the channel guaranteed to
survive even when `LocalEvalService` would otherwise blank the rationale —
see README, "Diagnostics"):

```
adk_tracegauge: cost not computed: model 'some-totally-unknown-model-xyz' did not resolve against
adk-tracegauge's price table (Gemini, Claude, and GPT models known: __local_zero_cost__,
claude-haiku-4-5, claude-opus-4-8, claude-opus-5, claude-sonnet-5, gemini-2.0-flash,
gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-pro, gemini-2.5-pro-long-context,
gemini-3.1-flash-lite, gemini-3.1-pro-preview, gemini-3.1-pro-preview-long-context,
gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.6-flash, gemini-3.7-flash, gpt-5, gpt-5.1,
gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra). If this is a local/self-hosted model (Ollama, vLLM)
it should have resolved automatically to zero cost -- check the captured model string actually
carries one of the recognized local prefixes (ollama_chat/, ollama/, vllm/); if it's routed
through a cloud platform whose pricing can differ from the first-party rate (Bedrock, Vertex AI,
Azure), that's why it wasn't auto-resolved -- see _pricing.py's module docstring. Otherwise,
register a custom price by setting the ADK_TRACEGAUGE_PRICE_TABLE environment variable to the
path of a JSON file with the same schema as the bundled table (src/adk_tracegauge/data/
gemini_prices.json) containing an entry for this model, or open an issue at
https://github.com/gaurav-gandhi-2411/adk-tracegauge/issues if it should ship built-in.
```

`result.per_invocation_results[0].eval_status` is `EvalStatus.NOT_EVALUATED`
and `.score` is `None` — never a fabricated pass/fail.

**Fix:** one of three things, depending on what the model actually is —
(a) check the captured `model_version` string actually carries a recognized
local-model prefix if it's Ollama/vLLM (it should auto-resolve to $0.00
otherwise); (b) if it's Claude/GPT routed through Bedrock/Vertex AI/Azure,
register its real negotiated rate via `ADK_TRACEGAUGE_PRICE_TABLE` (see
README, "Pricing"); (c) if it's a model this package should just know
about, open an issue with the exact model string.

## 3. Missing threshold

`CostEfficiencyEvaluator` requires a max-USD-per-invocation threshold at
construction time — no permissive always-PASSED default.

**Reproduction:**

```python
from adk_tracegauge.evaluator import CostEfficiencyEvaluator, METRIC_NAME
from google.adk.evaluation.eval_metrics import EvalMetric

CostEfficiencyEvaluator(eval_metric=EvalMetric(metric_name=METRIC_NAME))
```

**Real captured error:**

```
ValueError: CostEfficiencyEvaluator requires a max-USD-per-invocation threshold -- it never
defaults to a permissive always-PASSED sentinel (that would be a gate that looks green while
checking nothing). Pass either EvalMetric(metric_name=METRIC_NAME,
criterion=CostThresholdCriterion(threshold=<max_usd_per_invocation>)) (preferred) or the
deprecated EvalMetric(metric_name=METRIC_NAME, threshold=<max_usd_per_invocation>).
```

**Fix:** set a threshold, either the preferred criterion form —
`EvalMetric(metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=0.05))`
— or the deprecated scalar form — `EvalMetric(metric_name=METRIC_NAME, threshold=0.05)`
— per invocation, in USD. There's no "reasonable default" this package could
pick on your behalf; what counts as too expensive is specific to your agent
and your budget.
