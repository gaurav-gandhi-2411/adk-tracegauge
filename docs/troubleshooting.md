# Troubleshooting

Five real, live-triggered errors — the exact text this package produces,
captured directly (not reconstructed from memory or the source). Entries
1–3 date from Phase 2 W5; entry 1's related sub-note, and entries 4–5, were
added/re-verified in Phase 3 B6, re-triggering each live rather than
trusting the earlier capture (entry 2's captured text had gone stale after
Phase 3 B1 changed the actual behavior — see the note under entry 2). All
five entries were re-triggered live again in Phase 4 R7, this time from a
genuinely fresh **wheel-only** install (not an editable dev checkout) in a
clean venv outside the repo — entries 2–4's captured text reproduced
byte-identical; entry 1 surfaced a real, previously-invisible gap (see its
own re-verification note below — a dev-checkout environment had an
undeclared transitive dependency a clean install doesn't). Each
one is a deliberate fail-closed design choice (see `PLAN.md`/README), not
an accident: this package would rather raise or refuse a number loudly
than guess.

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

**Re-verified Phase 4 R7, from a genuinely fresh wheel-only install (not an
editable dev checkout) — a real, earlier-failure gap was found and is worth
knowing before you follow this reproduction literally:** installing
`google-adk[eval]==1.0.0` into a clean venv (via `uv pip install`, full
dependency resolution, not `--no-deps`) and then running the exact command
above fails **one import frame earlier** than documented — `ModuleNotFoundError:
No module named 'deprecated'`, raised from `google/adk/tools/base_tool.py`,
before Python ever reaches `adk_tracegauge/__init__.py`. Confirmed by direct
inspection: `google-adk==1.0.0`'s own PyPI metadata (`importlib.metadata.
metadata("google-adk").get_all("Requires-Dist")`, all 52 entries checked) never
declares a dependency on the `deprecated` package under any extra, despite
`base_tool.py` importing `from deprecated import deprecated` unconditionally —
a real, undeclared-dependency packaging bug in the `google-adk==1.0.0` release
itself, independent of adk-tracegauge, that a genuinely clean resolver hits
today. (Phase 2 W5's original capture likely didn't hit this because its dev
venv already had `deprecated` installed transitively from some other
already-present package — editable/dev-checkout environments accumulate
transitive packages a fresh install doesn't, the exact class of gap this
Phase 4 work item exists to catch.) The documented text above **does**
reproduce exactly once `deprecated` is installed first (`uv pip install
deprecated`) — verified live this session. If you hit `No module named
'deprecated'` instead of the text above while reproducing this on a truly
clean environment, that's this same upstream gap, not a new problem — install
`deprecated` and re-run, or (more usefully) just don't install
`google-adk==1.0.0` at all outside of deliberately reproducing this doc entry;
it predates this package's supported floor for unrelated reasons too.

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
survive even when `LocalEvalService` would otherwise blank the
per-invocation rationale for a `NOT_EVALUATED` case):

```
adk_tracegauge: cost not computed: model 'some-totally-unknown-model-xyz' did not resolve against
adk-tracegauge's price table (Gemini, Claude, and GPT models known: __local_zero_cost__,
claude-haiku-4-5, claude-opus-4-8, claude-opus-5, claude-sonnet-5, gemini-2.0-flash,
gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-pro, gemini-2.5-pro-long-context,
gemini-3.1-flash-lite, gemini-3.1-pro-preview, gemini-3.1-pro-preview-long-context,
gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.6-flash, gemini-3.7-flash, gpt-5, gpt-5.1,
gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra). If this is a local/self-hosted model (Ollama, vLLM)
it needs an explicit opt-in before it resolves to zero cost -- see the ADK_TRACEGAUGE_ASSUME_LOCAL
environment variable; if it's routed through a cloud platform whose pricing can differ from the
first-party rate (Bedrock, Vertex AI, Azure), that's why it wasn't auto-resolved -- see
_pricing.py's module docstring. Otherwise, register a custom price by setting the
ADK_TRACEGAUGE_PRICE_TABLE environment variable to the path of a JSON file with the same schema
as the bundled table (src/adk_tracegauge/data/gemini_prices.json) containing an entry for this
model, or open an issue at https://github.com/gaurav-gandhi-2411/adk-tracegauge/issues if it
should ship built-in.
```

**Note (updated Phase 3 B6):** this text changed after Phase 3 B1 (the Ollama Cloud
fix) — it previously read "it should have resolved automatically to zero cost" for
any `ollama_chat/`/`ollama/`/`vllm/`-prefixed model. That was true before B1, but is
no longer accurate: as of B1, a local-prefixed model needs the explicit
`ADK_TRACEGAUGE_ASSUME_LOCAL` opt-in (see entry 4 below) before it resolves to
`$0.00` — re-triggered live above, this session, to confirm the current text is
accurate now, not carried over from a stale capture.

`result.per_invocation_results[0].eval_status` is `EvalStatus.NOT_EVALUATED`
and `.score` is `None` — never a fabricated pass/fail.

**Fix:** one of three things, depending on what the model actually is —
(a) if it's Ollama/vLLM, check the captured `model_version` string actually
carries a recognized local-model prefix, **and** set `ADK_TRACEGAUGE_ASSUME_LOCAL`
(see entry 4 below — a recognized prefix alone is no longer enough as of
Phase 3 B1); (b) if it's Claude/GPT routed through Bedrock/Vertex AI/Azure,
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

## 4. Local model (Ollama/vLLM) priced `NOT_EVALUATED`, not `$0.00`

New in Phase 3 (B1) — a real behavior change worth knowing if you upgraded
from a pre-B1 version and relied on local models resolving to `$0.00`
automatically. A recognized local-model LiteLlm prefix
(`ollama_chat/`, `ollama/`, `vllm/`) is no longer sufficient on its own,
because Ollama Cloud (a real paid product) shares the identical
`ollama_chat/`/`ollama/` prefix with local Ollama, and nothing
adk-tracegauge captures (confirmed against google-adk's `models/lite_llm.py`
and `models/llm_response.py` directly) can tell the two apart.

**Reproduction** (live this session, no `ADK_TRACEGAUGE_ASSUME_LOCAL` set):

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
        model_version="ollama_chat/qwen2.5:7b",
        prompt_token_count=1000,
        candidates_token_count=500,
        cached_content_token_count=0,
        total_token_count=1500,
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

**Real captured warning:**

```
adk_tracegauge: cost not computed: model 'ollama_chat/qwen2.5:7b' carries a local-model LiteLlm
prefix (ollama_chat/, ollama/, or vllm/) but was NOT priced at zero cost, because that prefix
alone cannot distinguish genuinely local/self-hosted inference from Ollama Cloud -- a real paid
product routed through the identical prefix, where only the api_base/host differs, and that field
is not available at the point adk-tracegauge captures usage (confirmed by reading google-adk's
models/lite_llm.py and models/llm_response.py directly -- see _pricing.py's module docstring). A
silently wrong $0.00 for a paid Ollama Cloud call would be worse than this refusal. If
'ollama_chat/qwen2.5:7b' really is local/self-hosted, opt in explicitly by setting
ADK_TRACEGAUGE_ASSUME_LOCAL=1 (asserts every recognized local prefix) or
ADK_TRACEGAUGE_ASSUME_LOCAL=<comma-separated prefixes> (e.g. 'vllm/' to assert only that one,
leaving ollama_chat/ still failing closed) before running your eval.
```

`result.per_invocation_results[0].eval_status` is `EvalStatus.NOT_EVALUATED`,
`.score` is `None` — same fail-closed shape as entry 2, not a `$0.00` PASS.

**Fix:** if the model genuinely is local/self-hosted, set
`ADK_TRACEGAUGE_ASSUME_LOCAL=1` (or a comma-separated prefix subset) before
running your eval. If it's actually routed through Ollama Cloud, this
refusal is correct behavior — register its real rate via
`ADK_TRACEGAUGE_PRICE_TABLE` instead of asserting it local.

## 5. `adk-tracegauge check` refuses to run (`exit code 3`) on a small eval set

Not a bug — `adk-tracegauge check` (this package's hero CI-gating path, see
README "Quickstart") refuses to emit a verdict when either snapshot has
fewer than `--min-n` (default 30) priced invocations, because a bootstrap
CI is not statistically meaningful below that size (see README "Known
limitations" and `adk_tracegauge._regression`'s module docstring for the
measured detection-power reasoning behind the default).

**Reproduction** (live this session, two synthetic 10-invocation snapshots,
`adk-tracegauge check` with no overrides):

```
adk-tracegauge check: mode=two-sample (--mode auto: best-available pairing key (none) only has 0
overlapping match(es) < --min-n=30, so falling back from paired -- see snapshot.py's docstring for
how to enable paired comparison)
adk-tracegauge check [method=two_sample]: n_baseline=10 n_current=10 (min_n=30)
  mean_baseline=$0.008408  mean_current=$0.008385
  achieved power: minimum reliably-detectable effect at 80% power, given this run's observed
  variance/n, is ~$0.001049 (+12.48% of mean baseline) [normal approximation to the bootstrap CI --
  see _regression.py module docstring for validated accuracy]
  INSUFFICIENT DATA: each group needs >= 30 invocations for a statistically meaningful bootstrap
  CI (see adk_tracegauge._regression module docstring for the n>=30 rationale) -- refusing to emit
  a verdict.
```

(Re-triggered Phase 4 R4 -- the "achieved power" line is new; the mean/exit-code numbers are
byte-identical to the prior capture, confirming R4 is purely additive here. Note it prints even
in the insufficient-data case, since even a too-small sample's own observed variance says
something real about the achievable detection floor.)

Real exit code: `3`.

**Fix:** one of three things — (a) grow your eval set to at least 30 cases
per group, the only fix that doesn't trade away statistical validity; (b) if
your eval harness pins a stable `session_id` per eval case, try `--mode
paired` explicitly (or rely on the `auto` default) — it can emit a
meaningful verdict at smaller `n` than two-sample requires, though it has
its own `--min-n` floor on overlapping `session_id`s; (c) pass a lower
`--min-n` explicitly if you understand and accept the reduced statistical
reliability documented in README's "Known limitations" — this package will
never do that silently on your behalf.
