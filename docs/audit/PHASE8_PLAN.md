# Phase 8 — Option C Migration Plan (re-derived against current reality)

**Status: PLANNING ONLY.** No migration code, no branches, no PRs. This document
re-derives Phase 5 S2's "core+adapters" consolidation recommendation
(`tracegauge` absorbing `adk-tracegauge`'s pricing and statistics engine)
against the two packages as they exist today, post-Phases 6–8: `tracegauge`
0.10.2 (published) and `adk-tracegauge` 0.3.1 (published).

**Bottom line, stated up front (full reasoning in FF4):** the evidence does
**not** support Phase 5 S2's consolidation as originally scoped. Do not
proceed. Recommendation: a targeted cross-repo divergence test (FF4.3), not a
package merge — full reasoning below.

---

## FF1 — Current state

### 1.1 Per-module inventory, read at published versions

**`adk-tracegauge` 0.3.1** (`main`, commit `769e67a`, tag `v0.3.1` — confirmed
identical to the PyPI-published artifact this session, DD2/DD3). Total `src/`:
**4,722 LOC** across 12 modules.

| Module | LOC | Public API? | ADK-specific or provider-agnostic |
|---|---|---|---|
| `_regression.py` | 1,173 | Yes — `evaluate_regression`, `evaluate_regression_paired`, `DEFAULT_CONFIDENCE`, `MIN_N_DEFAULT`, etc. | **Provider-agnostic.** Operates on plain float cost lists; no ADK import, no google-adk types anywhere in this file. |
| `evaluator.py` | 790 | Yes — `CostThresholdCriterion`, `CostEfficiencyEvaluator`, `METRIC_NAME` | **ADK-specific.** Implements ADK's `BaseCriterion`/`Evaluator` interfaces directly; imports `google.adk.evaluation.*` throughout. |
| `_pricing.py` | 585 | No (underscore-private; used internally by `evaluator.py`/`snapshot.py`) | **Mostly provider-agnostic data + one ADK-specific concern.** The price-table schema, resolution algorithm, promo-expiry logic are provider-agnostic; `_strip_litellm_provider_prefix`/`is_local_model`/`is_local_model_asserted` are ADK-`LiteLlm`-integration-specific (a LiteLLM concept, technically reusable outside ADK, but only ever exercised through ADK's `LiteLlm` model class here). |
| `_cli.py` | 471 | Yes — `main`, `build_parser` (the `adk-tracegauge` console script) | **Provider-agnostic** except for one flag (`--eval-history`, which reads ADK's `.evalset_result.json` format). |
| `snapshot.py` | 459 | Yes — `write_snapshot`, `build_snapshot` | **ADK-specific.** Consumes `_store.UsageStore` (provider-agnostic) but its `eval_case_ids_by_session` parameter and `--eval-history` join exist only because of ADK's `eval_case_id`/`session_id` split (Phase 4 R2). |
| `_cost.py` | 311 | No (private) | **Provider-agnostic.** Pure dollar arithmetic — no ADK import. Ported in-house from `tracegauge`'s `tes.cost` module at Phase 4 R5 specifically to *remove* the cross-package dependency. |
| `_adapter.py` | 269 | No (private) | **ADK-specific.** Adapts ADK's `Invocation`/`Event` objects into this package's internal digest shape. |
| `_compat.py` | 245 | Yes — `convert_events_to_eval_invocations`, `load_eval_case_ids_by_session_id` | **ADK-specific by definition** — this module exists solely to wrap non-public ADK internals. |
| `_store.py` | 166 | Yes — `UsageStore`, `DEFAULT_USAGE_STORE`, `CapturedCall` | **Provider-agnostic.** A plain in-memory store keyed by invocation ID; nothing ADK-shaped about the data structure itself. |
| `_plugin.py` | 165 | Yes — `TraceGaugeUsagePlugin` | **ADK-specific.** Implements ADK's `BasePlugin` callback interface directly. |
| `__init__.py` | 68 | Yes (package root) | ADK-specific (registers the metric against ADK's registry as an import side effect). |
| `__main__.py` | 20 | No | Provider-agnostic (thin CLI entry shim, Phase 8 CC1 addition). |

**`tracegauge` 0.10.2** (`master`, commit `858971a` — `tes/` confirmed
byte-identical to the `v0.10.2` tag via `git diff v0.10.2..master --stat --
tes/`, empty diff; only unrelated `scripts/` infra changed since). Total
`tes/`: **10,137 LOC** across 23 modules — more than double
`adk-tracegauge`'s entire codebase, reflecting `tracegauge`'s much broader
scope (a full Claude Code session-analysis product: CLI, Flask dashboard,
ML-based waste/pattern detection, community baselines — not just a cost
engine). Only the pricing-relevant modules are itemized here:

| Module | LOC | Public API? | Notes |
|---|---|---|---|
| `cost.py` | 405 | Yes — `compute_turn_cost`, `compute_session_cost`, `check_price_table_staleness`, `load_price_table` | Provider-agnostic in principle, but built exclusively for Claude Code's own transcript format (`TurnDigest`/`SessionDigest` from `_digest.py`) — no generalized "any provider" abstraction exists here. |
| `_digest.py` | 109 | No (private) | Claude-Code-transcript-specific dataclasses. |

`tracegauge`'s core dependencies (`flask`, `httpx`, `numpy`, `scikit-learn`)
share **zero** overlap with `adk-tracegauge`'s sole dependency
(`google-adk[eval]`). Confirmed via direct `pyproject.toml` read: no
`google-adk`/`adk` string anywhere in `tracegauge`'s dependency list — the
Phase 4 R5 decoupling (removing `adk-tracegauge`'s dependency on
`tracegauge`) is still fully intact in both directions; nothing currently
links the two packages at runtime.

### 1.2 What changed since Phase 5's assessment

Phase 5 S2 was written before Phases 6–8. Since then:

- **`adk-tracegauge` grew, not shrank.** `__main__.py` added (Phase 8 CC1),
  Python 3.14 support verified and shipped (Phase 8 CC2), the FPR-anomaly
  audit corrected published statistics and added a `two_proportion_z_test`
  helper + two new discriminant scripts to `_regression.py`'s surrounding
  toolchain (Phase 8 AA3), `examples/04` and `examples/05` were added
  (documenting both pairing-key paths end to end), and the paired-key
  fallback chain (`eval_case_id` → `session_id` → two-sample) was hardened
  and re-verified against the real `adk eval` CLI path this session (Phase 8
  EE1). None of this shrinks the migration surface Phase 5 S2 scoped —
  `_regression.py` alone grew by roughly 130 lines (docstring
  additions/discriminant-test documentation) since Phase 7.
- **`tracegauge` independently re-built a piece of `adk-tracegauge`'s own
  infrastructure** in the same window: `tes/cost.py`'s
  `check_price_table_staleness` + `.github/workflows/price-freshness.yml`
  were added at `tracegauge` 0.10.2, explicitly documented in that
  workflow's own comment as "adapted from adk-tracegauge's own
  price-freshness.yml" — see FF2.1/2.2 below. This is new evidence Phase 5
  S2 didn't have: concrete, already-manifested duplication cost, not a
  hypothetical one.
- **Both packages' price tables were independently re-verified and extended**
  around the same date (`adk-tracegauge`: 2026-08-14; `tracegauge`:
  2026-08-15) — by the same person, in two separate sessions, against two
  separately-fetched copies of the same upstream Claude pricing page for the
  models they share.

### 1.3 LOC/tests that would move, stay, or be deleted under Option C as originally scoped

("Move" = migrates from `adk-tracegauge` into `tracegauge`'s new core;
"stay" = remains in `adk-tracegauge` as the ADK-specific adapter layer;
"delete" = superseded entirely by the merged core, no replacement needed.)

| | LOC | Tests |
|---|---|---|
| **Move** (`_regression.py` + `_pricing.py` + `_cost.py`) | 1,173 + 585 + 311 = **2,069** (43.8% of `adk-tracegauge`'s `src/`) | `test_regression.py` + `test_regression_power.py` + `test_regression_confidence_grid.py` + `test_fpr_anomaly_audit.py` + `test_pricing.py` + `test_pricing_call_site.py` + `test_cost_port_fidelity.py` = **208 of 395 tests (52.7%)**, collected and counted live this session |
| **Stay** (`evaluator.py`, `_cli.py`, `snapshot.py`, `_adapter.py`, `_compat.py`, `_store.py`, `_plugin.py`, `__init__.py`, `__main__.py`) | 2,653 (56.2%) | 187 of 395 (47.3%) |
| **Delete** | 0 — nothing in the current design is superseded outright; every moved module would need a thin re-export or ADK-facing wrapper left behind in `adk-tracegauge` regardless, per FF3.1/3.2 | 0 |

Note the asymmetry: `_regression.py` (1,173 LOC, the "statistics engine" half
of Option C's name) has **no counterpart in `tracegauge` at all** — nothing
to de-duplicate there. Moving it is a pure one-way donation of a capability
`tracegauge` doesn't currently have and has never had a user request for
(see FF2.3). Only the pricing side (`_pricing.py` + `_cost.py`, 896 LOC) has
an actual duplication story, and even that overlaps with `tracegauge`'s own
`cost.py` (405 LOC) on data/concept, not on code (see FF2.1).

---

## FF2 — Validate the premise

### 2.1 Diff the two pricing implementations — every behavioral difference found

Read both price-table JSON files and both resolution modules in full
(`adk-tracegauge/src/adk_tracegauge/data/gemini_prices.json` +
`_pricing.py`; `token-efficiency-scorer/tes/data/prices.json` + `tes/cost.py`).

**Data-shape differences (not just different values — different schema):**

1. **Model coverage barely overlaps.** `tracegauge`: 19 Claude-family entries
   only (current + 6 retired). `adk-tracegauge`: 10 Gemini entries + 4 Claude
   entries (`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`,
   `claude-opus-4-8`) + 5 GPT entries + 1 synthetic local-zero-cost entry.
   **Only those same 4 Claude models are present in both tables.**
   `adk-tracegauge` is missing `claude-opus-4-7`/`4-6`/`4-5`/`4-1`/`4` and
   every `claude-sonnet-4-*`/`claude-3-*` entry `tracegauge` carries — not a
   bug, just genuinely different scope (ADK agents calling current-generation
   models via `LiteLlm` vs. Claude Code sessions spanning years of model
   history).
2. **The 4 overlapping entries currently agree exactly** — `claude-opus-5`
   $5.00/$25.00, `claude-sonnet-5` $2.00/$10.00, `claude-haiku-4-5`
   $1.00/$5.00, `claude-opus-4-8` $5.00/$25.00 in both tables, verified by
   direct read of both JSON files. **But nothing enforces this agreement** —
   two independent fetches, one day apart (2026-08-14 vs. 2026-08-15), from
   the same source URL, by the same person, in two different sessions. This
   is the literal "one owner, two divergent copies" risk Phase 5 S2 named —
   confirmed real, but narrow (4 rows, not the whole system).
3. **`cache_multipliers.write_5min`/`write_1hr` genuinely differ in value**,
   not just applicability: `tracegauge` models real cache-write surcharges
   (1.25x / 2.0x) because Claude Code's own API reports a
   `cache_creation_input_tokens` field directly; `adk-tracegauge` hardcodes
   both to `0.0` because ADK's plugin capture surface never exposes a
   separate cache-write token count for any provider (confirmed via the
   table's own note, citing a direct read of `_plugin.py`/`_store.py`). This
   is not a bug in either — each is correct for what it can actually observe
   — but it means a naive "merge the JSON files" would be wrong for both
   sides unless the schema explicitly models "not observable" vs. "zero."
4. **Only `adk-tracegauge` has promo-expiry auto-switching**
   (`schema_version: 3`'s `promo_until`/`standard_rate`, Phase 3 B2) —
   `tracegauge` has no equivalent field or resolver logic at all. If a
   shared Claude model ever ran a time-limited promo, `tracegauge` would
   need a manual table edit at expiry; `adk-tracegauge` would auto-switch.
5. **Only `adk-tracegauge` has long-context tiering**
   (`long_context_threshold_tokens`/`long_context_model_key`, `gemini-2.5-pro`
   and `gemini-3.1-pro-preview`) — no `tracegauge` equivalent, and none of
   `tracegauge`'s Claude-only scope would need one (Claude doesn't publish
   context-length-tiered pricing the way Gemini does).
6. **Different staleness-threshold storage mechanism, same value.** Both use
   90 days, but `tracegauge` stores it as a JSON top-level field
   (`stale_threshold_days`) while `adk-tracegauge` hardcodes
   `STALE_THRESHOLD_DAYS` as a Python constant in `_pricing.py`. Changing one
   does not change the other.

**Code-shape differences (confirmed by reading both modules' function
signatures directly, not inferred):**

`tes/cost.py`: `load_price_table`, `_resolve_model`, `_server_tool_warning`,
`compute_turn_cost`, `compute_session_cost`, `check_price_table_staleness` —
6 functions, no LiteLLM-prefix handling, no local-model-assertion logic (no
such ambiguity exists for Claude Code's own transcript format).

`adk_tracegauge/_pricing.py`: `load_gemini_prices`, `_effective_rates`,
`effective_prices`, `_entry_to_resolved`, `_strip_litellm_provider_prefix`,
`is_local_model`, `_asserted_local_prefixes`, `is_local_model_asserted`,
`resolve_model`, `resolve_model_for_call`, `known_model_keys` — 11 functions,
including 4 (`_strip_litellm_provider_prefix`, `is_local_model`,
`_asserted_local_prefixes`, `is_local_model_asserted`) that exist
**specifically** to handle ADK's `LiteLlm` integration's local/cloud
ambiguity — a problem `tracegauge` does not have and has no reason to solve.

**Conclusion: these are not two divergent copies of the same code.** They
are two independently-designed implementations, built for genuinely
different capture surfaces (Claude Code's direct API responses vs. ADK's
`LiteLlm`-mediated, plugin-captured usage metadata), that happen to share a
JSON-table *convention* and 4 overlapping data rows. Phase 5 S2's framing
overstates the duplication at the code level; it understates a different,
real cost — see 2.2.

### 2.2 The concrete cost of NOT consolidating — specific, not general

**What actually broke, already, once:** `tracegauge`'s own
`price-freshness.yml` workflow comment states directly: *"Added 0.10.2 (S1
audit fix, adapted from adk-tracegauge's own price-freshness.yml): before
this workflow existed, prices.json had zero CI staleness signal of any kind
and went 67 days stale with nobody noticing — this is exactly the gap that
let the missing claude-opus-5/claude-sonnet-5 entries (S1's mainline
finding) go undetected for as long as they did."* This is a **real, already-
occurred incident**, not a hypothetical one: `tracegauge` shipped stale/
missing prices for 67 days because it lacked a guard `adk-tracegauge` had
already built, and the guard had to be manually rediscovered, re-designed,
and hand-ported across repos before the gap closed.

**What could break next, and for whom:** the 4 shared Claude model prices
(`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`)
silently disagreeing between the two tables. Concrete failure mode: Anthropic
changes `claude-opus-5`'s rate; the maintainer updates `adk-tracegauge`'s
table (used in a CI cost-regression gate — a wrong rate there produces a
wrong PASS/FAIL verdict on a real build) but the `tracegauge` update lags or
is forgotten (used in Claude Code session cost reporting — a wrong rate
there silently mis-reports a user's actual spend). Both are real, both are
plausible, given it already happened once for the freshness-guard
*infrastructure* itself. **This is the one concrete, specific, non-general
cost Phase 5 S2 was gesturing at** — and it is narrow enough (4 rows) to be
solved directly (FF4.3) without merging either package's engine.

### 2.3 CEO lens — real user or architecture for its own sake?

Checked live (WebSearch, this session) whether LangChain, LlamaIndex, and
CrewAI expose per-call token usage in a form this engine could consume:

- **LangChain: real, usable signal.** `AIMessage.usage_metadata` is a
  standardized `UsageMetadata` structure — `input_tokens`, `output_tokens`,
  `total_tokens`, with optional `input_token_details`/`output_token_details`
  covering cache_creation/cache_read/audio/reasoning — consistent across
  provider integrations. Structurally close enough to what
  `adk-tracegauge`'s `_store.CapturedCall` already captures from ADK
  (`prompt_token_count`/`candidates_token_count`/`cached_content_token_count`)
  that an adapter is plausible, not speculative.
  [Messages docs](https://docs.langchain.com/oss/python/langchain/messages) ·
  [`usage_metadata` reference](https://reference.langchain.com/python/langchain-core/messages/ai/AIMessage/usage_metadata)
- **LlamaIndex: real, usable signal.** `TokenCountingHandler` produces a
  `TokenCountingEvent` per call (`prompt_token_count`, `completion_token_count`,
  `total_token_count`) collected in `token_counter.llm_token_counts` — also
  genuinely per-call, not just an aggregate.
  [Token Counting Handler docs](https://docs.llamaindex.ai/en/stable/examples/callbacks/TokenCountingHandler/)
- **CrewAI: weaker, immature signal.** `@before_llm_call`/`@after_llm_call`
  hooks exist and can reach the underlying LLM response, but community
  threads (not vendor marketing) report `result.token_usage` **not matching**
  real per-LLM-call usage, and per-task token accounting is an open,
  unresolved feature request as of this search.
  [LLM Call Hooks](https://docs.crewai.com/en/learn/llm-hooks) ·
  [community report of mismatched token_usage](https://community.crewai.com/t/crewai-result-token-usage-not-matching-with-llms-token-usage-count/3467)

**So the technical premise is not purely theoretical** — 2 of 3 named
frameworks expose exactly the shape of data this engine needs. **But
technical feasibility is a different question from validated demand, and on
demand the evidence is silent**: neither `PLAN.md` shows a user request, a
GitHub issue, or any external signal that anyone has asked either package to
support LangChain, LlamaIndex, or CrewAI. Both packages are solo-maintainer
portfolio projects with a specific, narrow, currently-served niche (ADK
cost-gating; Claude Code session analysis) — the adapter story is a bet on
future demand, not a response to present demand.

### 2.4 Verdict on the premise

**2.3 comes back mixed, and the honest reading is that it doesn't clear the
bar for what Phase 5 S2 proposed.** The technical case for an adapter
platform is real (LangChain/LlamaIndex genuinely expose the needed data
shape) but entirely unvalidated by demand — nobody has asked. Meanwhile the
actual, concrete, already-manifested cost of the current two-package
structure (2.2) is narrow: a freshness-guard pattern that had to be hand-
ported once, and a 4-row price-agreement risk. Per this document's own
instruction: **not defending Phase 5 S2 out of consistency.** The cheaper
option — a targeted divergence test, not a merge — is recommended in FF4,
not the consolidation FF3 details below.

FF3 is still worked through in full, in case the recommendation in FF4 is
overridden.

---

## FF3 — If consolidation held (worked through in full; not recommended, see FF4)

### 3.1 Exact module-by-module move list, in dependency order

1. `adk_tracegauge/_cost.py` (311 LOC, zero internal dependents besides
   `evaluator.py`) → `tracegauge`'s new `tes/_engine/cost.py` (or similar),
   reconciled against `tracegauge`'s existing `tes/cost.py` — this step
   alone requires deciding whether the two `compute_*_cost` functions merge
   into one generalized function or coexist as two call sites into a shared
   arithmetic core. No pre-existing tests would need to change if kept as
   two thin call sites over one core.
2. `adk_tracegauge/_pricing.py` (585 LOC, depends only on stdlib +
   `_cost.py`'s types) → next, since it depends on nothing else internal.
   Requires resolving the schema differences in 2.1 (cache-write modeling,
   promo-expiry, long-context tiering, LiteLLM-prefix stripping) into one
   superset schema both packages' loaders can read — the single largest
   piece of real engineering work in this migration, larger than moving the
   code itself.
3. `adk_tracegauge/_regression.py` (1,173 LOC, depends on nothing internal
   at all — confirmed provider-agnostic, operates on plain float lists) →
   moves cleanly, no schema reconciliation needed since `tracegauge` has no
   existing regression-gate code to conflict with.
4. `adk-tracegauge`'s `evaluator.py`, `_cli.py`, `snapshot.py`, `_adapter.py`,
   `_compat.py`, `_plugin.py`, `_store.py` become thin ADK-facing wrappers
   importing from `tracegauge` instead of defining the logic locally — last,
   since everything else must exist first.

### 3.2 Public API for `tracegauge` 0.11.0

**Becomes supported surface:** `tes.pricing.resolve_model`/
`resolve_model_for_call` (generalized from `adk_tracegauge._pricing`'s
current private functions), `tes.regression.evaluate_regression`/
`evaluate_regression_paired` (generalized from `adk_tracegauge._regression`,
made fully public since `tracegauge` has no existing regression-gate feature
to keep private), `tes.cost.compute_turn_cost`/`compute_session_cost`
(already public, unchanged signature if possible).

**Stays private:** the LiteLLM-prefix/local-model-assertion helpers — these
are ADK/LiteLLM-specific and have no meaning in `tracegauge`'s own Claude
Code context; they'd need to live in `adk-tracegauge`'s own adapter layer,
not in the shared core, or be generalized into a provider-agnostic
"caller-asserts-local" concept that serves both — undecided, real design
work, not mechanical.

### 3.3 Breaking changes for existing users of either package

Both have **real installs** (confirmed live this session: `adk-tracegauge`
0.3.1 on PyPI, `tracegauge` 0.10.2 on PyPI, both with real published
`requires_python`/wheel/sdist artifacts — not hypothetical).

- **`tracegauge` users**: `tes.cost.compute_turn_cost`/`compute_session_cost`
  would need to keep their exact current signature and behavior for
  Claude-Code-transcript callers, even as the underlying engine gains
  Gemini/GPT support underneath — a real compatibility constraint, not just
  a version bump. Any schema change to `tes/data/prices.json` (to
  accommodate cache-write modeling alongside `adk-tracegauge`'s zero-write
  convention) risks breaking a user's own `TES_PRICE_TABLE`/
  `~/.tes/prices.json` override file if the schema shape changes.
- **`adk-tracegauge` users**: would gain a new mandatory or optional
  dependency on `tracegauge` (reintroducing the exact cross-package
  dependency Phase 4 R5 deliberately removed) — and would inherit
  `tracegauge`'s core dependencies (`flask`, `httpx`, `numpy`,
  `scikit-learn`) unless the new dependency is scoped via an extras group
  (e.g. `tracegauge[engine]` with no Flask/sklearn pulled in) — real,
  non-trivial packaging work, given `tracegauge`'s dashboard/ML features are
  currently bundled in the same top-level package as its core, not already
  split into extras.

### 3.4 Release sequence with gates

1. **`tracegauge` 0.11.0**: ship the generalized `tes.pricing`/`tes.regression`
   modules, packaging split (extras group so `adk-tracegauge` doesn't inherit
   Flask/sklearn). Gate: full `tracegauge` test suite green, both old and new
   public APIs covered, `tes.cost.compute_turn_cost` byte-identical output on
   every existing fixture (a port-fidelity test mirroring
   `test_cost_port_fidelity.py`'s own existing pattern from the Phase 4 R5
   direction, run in reverse).
2. **Verify live**: fresh isolated-venv install of `tracegauge==0.11.0` from
   PyPI, exercise both the pre-existing `tes.cost` surface and the new
   `tes.pricing`/`tes.regression` surface against real fixtures.
3. **`adk-tracegauge` 0.4.0 depending on `tracegauge>=0.11.0`**: replace
   `_pricing.py`/`_regression.py`/`_cost.py` with thin re-exports, run the
   full existing 395-test suite against the new dependency, confirm zero
   behavioral change (every current price/statistics test must still pass
   unmodified — a hard bar, since Phase 8's own FPR-anomaly-corrected figures
   and every other measured statistic in `README.md` must reproduce
   identically post-migration).
4. **Verify live**: fresh isolated-venv install of `adk-tracegauge==0.4.0`
   from PyPI, same DD3-style functional check this session already ran
   (version reads, `python -m` fallback, paired-mode auto-selection, eval
   PASS/FAIL, unknown-model fail-loud, local-model opt-in gate) — all must
   reproduce identically against the new dependency chain.
5. **`adk-docs` update**: `docs/integrations/adk-tracegauge.md`'s
   prerequisites section gains a `tracegauge` dependency line; every code
   example re-verified against the new install (same discipline as Phase 8
   Z1/AA1 this session).

### 3.5 What could go wrong, and how each is caught before publish

| Risk | Catch mechanism |
|---|---|
| Schema reconciliation (2.1's cache-write/promo/long-context differences) silently drops a capability one package needs | A combined schema-validation test asserting every field either package's loader currently reads is still present and correctly typed post-merge |
| `tracegauge` users' `TES_PRICE_TABLE`/`~/.tes/prices.json` overrides break on the new schema | A fixture using a real pre-migration override file, asserting it still loads and prices correctly post-migration |
| `adk-tracegauge` users inherit Flask/sklearn transitively | A fresh-venv install-and-inspect test: `pip install adk-tracegauge` then assert `flask`/`scikit-learn` are NOT importable unless explicitly requested via an extras group |
| Any of the 208 moving tests' behavior subtly changes during the port | Bit-for-bit output comparison against this session's own freshly-recorded baseline (every example script's output, every measured statistic in `README.md`) before/after |
| Published statistics in `README.md`/`docs/audit/FPR_ANOMALY.md` no longer reproduce | Re-run `scripts/measure_regression_confidence_grid.py` post-migration exactly as Phase 8 AA3 did, diff against the corrected 5,000-trial figures already published |

### 3.6 Effort estimate per step

Rough, stated as ranges since no actual estimation methodology was applied
(flagging this as UNVERIFIED — these are order-of-magnitude judgment calls,
not measured): schema reconciliation (3.2/3.5's largest risk) — multi-session
effort, the dominant cost of the whole migration; module moves themselves
(3.1) — mechanical, smaller than the schema work; two gated release cycles
(3.4) — each mirrors this session's own Phase 8 DD2/DD3 process (multi-hour,
already measured this session as the real cost of ONE release); adk-docs
update (3.4 step 5) — small, mirrors this session's own EE1.5/CC3 work.
Total: **the single largest engineering investment either package has made
to date**, larger than any Phase 1–8 work item this session has actually
executed and verified.

---

## FF4 — Recommend

### 4.1 Proceed or don't

**Don't proceed with Option C as scoped by Phase 5 S2.** Reasoning,
applying "simplest option satisfying the constraint" (the constraint being
FF2.2's real, narrow, already-manifested risk — not FF2.3's speculative
adapter platform):

- The actual duplication that has caused a real incident (2.2: the 67-day
  staleness gap) is a **guard pattern**, not the pricing/statistics engines
  themselves — and it's already been fixed, by hand-porting, once.
- The remaining live risk (4 shared Claude model rows silently disagreeing)
  is real but narrow — solvable directly, cheaply, without touching either
  package's architecture.
- The larger justification for the FULL merge — a multi-framework adapter
  platform — has real technical footing (2.3) but zero demand validation.
  Building a two-release, schema-reconciling, dependency-restructuring
  migration (FF3, the single largest engineering investment either package
  has made) to serve a demand signal that doesn't currently exist fails the
  CEO-lens test this document was asked to apply.
- `_regression.py`'s move (43.8% of the migration's `src/` weight, per FF1.3)
  isn't de-duplication at all — `tracegauge` has no counterpart to
  de-duplicate against. It's a one-way capability donation justified purely
  by the unvalidated adapter-platform bet.

### 4.2 If proceed — not applicable (4.1 recommends against)

### 4.3 If don't — the divergence test, and what triggers revisiting

**The divergence test** (small, targeted, addresses 2.2's actual risk
directly): a script — runnable in either repo's CI, or as a small
standalone script checked into one of them — that:
1. Loads both `adk-tracegauge/src/adk_tracegauge/data/gemini_prices.json`
   and `token-efficiency-scorer/tes/data/prices.json`.
2. For every model key present in **both** tables (currently: `claude-opus-5`,
   `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`), asserts
   `input_usd_per_mtok`/`output_usd_per_mtok` are identical. Fails loud,
   naming the specific model and both disagreeing values, if not.
3. Asserts both packages' staleness-threshold constants
   (`adk_tracegauge._pricing.STALE_THRESHOLD_DAYS`,
   `tes.cost.STALE_THRESHOLD_DAYS`) remain equal — the exact class of drift
   that caused the 67-day gap, now caught structurally instead of by luck.
4. Runs on the same weekly cadence as each repo's existing
   `price-freshness.yml` (Monday mornings), since both already run then —
   zero new CI infrastructure needed beyond one new script and one new
   scheduled step in one of the two existing workflows.

**What triggers revisiting this recommendation:**
- A real, external, unprompted user request for LangChain/LlamaIndex/CrewAI
  support on either package — the concrete demand signal 2.3/2.4 found
  entirely absent today.
- The divergence test (once built) actually catches a real, live
  disagreement between the two price tables that a simple test can't
  resolve on its own — evidence the risk is bigger than 4 rows.
- `tracegauge` independently develops its own need for a regression-gate/
  statistics-engine feature (e.g. a Claude Code session cost-regression
  check, mirroring what `adk-tracegauge` already does for ADK evals) — at
  that point `_regression.py`'s move stops being a speculative donation and
  becomes a validated, demand-driven consolidation, and this document's
  recommendation should be re-run against that new fact.
