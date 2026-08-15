# adk-tracegauge — Phase 2 Report: the cost regression gate

Branch: `feat/cost-regression-gate`. Not pushed, not tagged, not merged, not published.
Every claim below is tagged `[VERIFIED]` or `[UNVERIFIED]`; unverified only where explicitly noted.
This report was written by the orchestrator after all six work items and two independent verifier
passes completed. Audit date basis: 2026-08-14.

**Product reframe (accepted, executed):** adk-tracegauge moved from "a cost gauge" to "the cost
regression gate for ADK evals" — register one metric, get a per-invocation USD cost with a real
PASS/FAIL threshold verdict inside `adk eval`, plus a CI gate that fails on statistically
significant cost regression.

---

## Corrections applied to Phase 1 (confirmed this phase)

- **C1** — D2 (stale pin / canary never run) downgraded P1→P2. [VERIFIED: `git log -1 --format=%ci .github/workflows/pypi-canary.yml` → 2026-08-13 19:15 (Thu); cron is Mondays only; no Monday had elapsed as of 2026-08-14.]
- **C2** — D1 fixed via the threshold-redesign branch only (W2); the wrapper-exclusion branch was rejected.
- **C3** — Multi-provider pricing promoted to this phase (W3), delivered.
- **C4** — New P0 (price-table correctness never verified) — fixed first (W1), before any other work item, per instruction.
- **C5** — New P1 (unguarded private-API dependency) — resolved (W5.1): the primary documented path never needed it; the optional path that still does is now wrapped with a version guard and an actionable error.
- **C6** — OTel export deferred to Phase 3, as instructed; not built this phase.

---

## What changed per work item

### W1 — Price correctness (P0) — commit `7107527`

Before: flat-rate Gemini-only table, no `as_of`/staleness enforcement beyond a loose 180-day check, cache-read and thinking/tool-use tokens handled inconsistently, no long-context tiering.

**Real defects found and fixed** (not hypothetical — every one below was live-wrong before this commit):
- `gemini-3.6-flash` was priced at the **post-promotional rate** ($1.50/$7.50) while the live promotional rate ($0.75/$3.75, valid through 2026-12-31) was actually in effect — a ~2x overcharge.
- 3 current Gemini models were missing from the table entirely: `gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`.
- Long-context tiering (Gemini charges ~2x above 200k prompt tokens for `gemini-2.5-pro` and `gemini-3.1-pro-preview`) existed in published pricing and was completely unmodeled — now handled via `resolve_model_for_call(model, prompt_token_count)` resolving to a synthetic `<model>-long-context` table entry above the threshold.
- `thoughts_token_count` (Gemini "thinking" tokens, billed as output) and `tool_use_prompt_token_count` (server-side built-in tool tokens) were silently dropped by `_plugin.py`/`_adapter.py`, undercounting cost. Thinking tokens now fold into output cost; tool-use tokens have no verified rate, so a call reporting them nonzero now fails closed (`score=None`) instead of silently undercharging.
- Cache-read multiplier (0.1x) and Batch-API-out-of-scope were re-verified and found already correct — no fix needed, but not skipped either.

Staleness threshold tightened 180→90 days; `price_as_of` now threaded into every priced rationale; new `.github/workflows/price-freshness.yml` (pure date arithmetic, zero-cost) fails CI if any entry exceeds 90 days.

**Tests: 67 → 97 passing, 99% coverage.**

### W2 — Threshold gate (fixes P0/D1) — commit `ea7262f`

`CostEfficiencyEvaluator` redesigned around a real ADK protocol read directly from source (not guessed): a `BaseCriterion`-derived `CostThresholdCriterion(threshold=...)`, PASSED iff `cost <= threshold` (opposite comparison direction from a normal higher-is-better metric — explicitly implemented, not defaulted). Constructor now **requires** a threshold and raises `ValueError` if none is set — a silent always-PASS default was considered and explicitly rejected as "a gate that looks green while checking nothing." Unpriceable invocations (unresolved model, no usage) still correctly report `NOT_EVALUATED` — a distinct, legitimate case, not the old bug.

**End-to-end proof, real `adk eval` CLI against the actual installed unpatched `google-adk==2.6.3`** (see full verbatim output below).

**A genuine residual limitation was found and documented, not concealed:** `AgentEvaluator.evaluate()` (the pytest-style harness, distinct from `adk eval`/`LocalEvalService`) recomputes PASS/FAIL itself from the deprecated legacy `threshold` field via `mean(scores) >= threshold` — hardcoded higher-is-better, ignoring this evaluator's own correct `eval_status`. Since cost is lower-is-better, this can misclassify a genuinely-under-budget run as FAILED. A permissive legacy-field sentinel was considered and rejected (it would make that harness's gate permanently pass regardless of real cost — worse than the original bug). This is a real, source-confirmed ADK-side limitation this package cannot fix from its own code; `adk eval`/`LocalEvalService` (the primary documented path per this phase's reframe) are unaffected and fully correct.

Checked against GG's two open upstream PRs (adk-python#6682, #6710): neither touches the function responsible for this limitation — independent finding, not blocked on either landing.

**Tests: 97 → 107 passing, 99% coverage.**

### W3 — Multi-provider pricing (promoted) — commit `eb8bf3e`

Read the installed `google-adk`'s `lite_llm.py` directly: confirmed `LlmResponse.model_version` echoes the requested `"<provider>/<model>"` string verbatim (e.g. `anthropic/claude-opus-5`, `openai/gpt-5.1`, `ollama_chat/qwen2.5:7b`).

9 new priced entries added (Claude ×4, GPT ×5 — full diff table below). Deliberately excluded gpt-4o/gpt-4.1/o-series: their cache-read discount (0.25x–0.5x) diverges from the shared table's single global 0.1x multiplier, and adding them would silently under-price cached calls by 2.5x–5x — every entry that WAS added was independently confirmed to share the same 0.1x ratio first.

Local models (`ollama_chat/`, `ollama/`, `vllm/` prefixes) now resolve to a real `__local_zero_cost__` table entry ($0.0/$0.0, PASSED) via an explicit named code path — not a silent bypass — so mixed local+cloud invocations still sum correctly. Rationale explicitly states "(local model, zero marginal cost)."

Unknown-model error rewritten: no longer says "Gemini price table" (stale once the table stopped being Gemini-only), names the exact failing key, distinguishes local-but-unrecognized vs. cross-cloud-platform vs. genuinely-unknown, and points at a new `ADK_TRACEGAUGE_PRICE_TABLE` env-var extension mechanism.

**Tests: 107 → 152 passing (+45), 99% coverage, 100% on `_pricing.py`/`_adapter.py`.**

### W4 — CI regression gate (the differentiator) — commit `f90284d`

New `tracegauge` console entry point: `tracegauge snapshot --entrypoint module:callable --output path.json` (serializes a `UsageStore` to a versioned JSON format — one record per invocation, cost + token breakdown, a `skipped` list for anything unpriceable, never silently dropped) and `tracegauge check --baseline b.json --current c.json [--confidence 0.95] [--min-effect-usd 0.0001] [--min-effect-pct 5.0] [--min-n 30] [--n-boot 10000] [--seed 42]`.

Stdlib-only percentile bootstrap (no numpy/scipy dependency, despite both being already-available transitively — judged too fragile to depend on undeclared for the package's core differentiator) on the difference in means, one-sided (only cost increases count). A verdict fires only when **both** statistical significance (CI lower bound > 0) **and** practical significance (configurable minimum effect) hold. Refuses to emit a verdict below `min_n=30` (justified: standard CLT/bootstrap-stability rule of thumb, cited honestly as a rule of thumb, not independently re-derived this session) — distinct exit code (3) from a real regression (1) or a clean pass (0).

**Statistical validation — see "W4 statistical validation" section below for full numbers, including the independent adversarial re-check.**

**Tests: 152 → 199 passing (+47), 99% coverage.**

### W6 — Hygiene — commits `85918e7`/`6971a33`/`bff7006`/`0ee18b2`/`5a591e5`

- CI matrix extended to Python 3.10–3.13. **Adding the 3.10 leg surfaced a real bug before it reached CI**: `snapshot.py` (new in W4) imported `datetime.UTC`, which is 3.11+-only, despite the package's own `requires-python >=3.10` floor. Fixed to `timezone.utc` (stdlib since 3.2); also fixed `ruff`'s `target-version` (was `py311`, now `py310`) since it was actively recommending the 3.11-only form on future contributions. Full suite then run clean on both Python 3.10.20 and 3.13.5 (locally; not yet proven on GitHub Actions since the branch is unpushed — flagged as TODO, not claimed as done).
- `google-adk` pin bumped `<2.7.0`→`<2.8.0` (2.8.0 confirmed not to exist yet via PyPI JSON API) — but only after re-running the full 199-test suite for real against the live 2.7.0 release and confirming it passes clean, not on the strength of Phase 1's narrower smoke test alone.
- GitHub topics set live (`google-adk`, `agent-development-kit`, `llm-cost`, `llm-evaluation`, `opentelemetry`).
- `release.yml` now creates a GitHub Release after every successful PyPI publish; the 3 existing tags backfilled live with real auto-generated notes (verified citing actual PR numbers, not placeholders).
- Stale `dist/` cleaned; 5 already-merged local branches re-verified at the content level (not blindly trusted from Phase 1) and deleted locally — remote deletion deliberately deferred (rule-55 pause-for-confirmation on a destructive shared-ref operation).
- 2 shallow assertions in `test_registration.py` strengthened to real identity/type checks.
- oss-contrib's `adk-python` checkout re-synced on 3 of 4 branches that had drifted further behind their own fork since Phase 1 (grown to 39–105 commits) — re-verified fast-forward safety before syncing.
- keras-team/keras#23420's review thread: attempted `resolveReviewThread` via GraphQL — **succeeded directly** (contrary to Phase 1's expectation of a likely permission error for an external contributor). No ROUTE-TO-GG item needed here.

**Tests: 199 passing (unchanged count, fixed a latent cross-version bug), 99% coverage on both 3.10.20 and 3.13.5.**

### W5 — DX/adoption — commits `28faf6f`/`40fa498`/`cb77359`/`66b897f`

- **5.1**: confirmed by grep that the primary documented path (`adk eval`/`AgentEvaluator`) never touches the private `EvaluationGenerator.convert_events_to_eval_invocations` internal — it's only needed by the optional hand-rolled sub-agent-rollup harness, which is now wrapped in a new `_compat.py`: a best-effort version check plus a clear `RuntimeError` (naming the installed version and the affected path) instead of a bare `ImportError`/`AttributeError` if the internal ever moves.
- **5.2**: quickstart rewritten and *measured*, not estimated — **4 lines** of adk-tracegauge-specific code + 1 line of threshold config, **zero** private-API calls (down from Phase 1's 3 lines + 1 mandatory private call). Real `adk eval` CLI runs: 31.6s wall-clock for both a PASS and a FAIL case. **A new real finding surfaced by this measurement**: `adk eval`'s own process exit code does **not** reflect PASSED/FAILED (verified live — exit 0 in both the pass and fail runs) — now documented prominently, and it strengthens rather than undercuts the case for `tracegauge check`'s own real, distinguishable exit codes.
- **5.3**: 3 runnable examples added and actually executed this session: minimal cost gate, sub-agent rollup (real 2-agent delegation, root $0.525 + sub-agent $0.04 = $0.565 rolled up, verified by hand), and the CI regression gate end-to-end (real `snapshot`/`check` subprocesses, genuine +20%-mean injected regression detected, exit code 1).
- **5.4**: 4 badges (PyPI version, CI status, Python versions, license) — all 4 URLs independently confirmed to return HTTP 200 with real (not placeholder) content. Real passing/failing terminal captures for both `adk eval` and `tracegauge check`.
- **5.5**: `DEFAULT_USAGE_STORE` kept public (renaming would break existing tests/docs for no benefit) and now documented in the README explaining why it exists and when to use `store=` instead.
- **5.6**: `CHANGELOG.md` (retroactive, derived from real release notes/git log — not invented), `CONTRIBUTING.md`, 2 GitHub issue templates (bug report, price correction).
- **5.7**: all 3 deferred Phase-1 misconfiguration errors triggered live and captured verbatim into `docs/troubleshooting.md`: wrong google-adk version (`google-adk==1.0.0` → real `ModuleNotFoundError`), unknown model (real actionable warning text), missing threshold (real `ValueError` text).

**Tests: 199 → 210 passing (+11), 99% coverage.**

---

## Full price diff table (W1.2 + W3.1, independently re-verified by a separate adversarial pass this session)

All 21 priced entries (rows in the table below — 19 distinct model families, 2 of which carry a long-context tier) were independently re-fetched from live vendor pages by a verifier agent with no access to the implementing agents' prior work. **Result: 21/21 match.**

| Model | Our rate (input/output, $/Mtok) | Independently re-fetched rate | Verdict |
|---|---|---|---|
| gemini-2.5-pro (≤200k) | 1.25 / 10.00 | `[VERIFIED: ai.google.dev/gemini-api/docs/pricing]` 1.25 / 10.00 | match |
| gemini-2.5-pro (>200k) | 2.50 / 15.00 | `[VERIFIED]` 2.50 / 15.00 | match |
| gemini-2.5-flash | 0.30 / 2.50 | `[VERIFIED]` 0.30 / 2.50 | match |
| gemini-2.5-flash-lite | 0.10 / 0.40 | `[VERIFIED]` 0.10 / 0.40 | match |
| gemini-2.0-flash | 0.10 / 0.40 | `[VERIFIED]` 0.10 / 0.40 | match |
| gemini-3.5-flash | 1.50 / 9.00 | `[VERIFIED]` 1.50 / 9.00 | match |
| gemini-3.5-flash-lite | 0.30 / 2.50 | `[VERIFIED]` 0.30 / 2.50 | match |
| gemini-3.6-flash (promo, thru 2026-12-31) | 0.75 / 3.75 | `[VERIFIED]` 0.75 through Dec 31 2026 → 1.50 from Jan 1 2027 | match, incl. expiry date |
| gemini-3.7-flash | 0.75 / 3.75 | `[VERIFIED]` 0.75 / 3.75 (promo) | match |
| gemini-3.1-flash-lite | 0.25 / 1.50 | `[VERIFIED]` 0.25 / 1.50 (text) | match |
| gemini-3.1-pro-preview (≤200k) | 2.00 / 12.00 | `[VERIFIED]` 2.00 / 12.00 | match |
| gemini-3.1-pro-preview (>200k) | 4.00 / 18.00 | `[VERIFIED]` 4.00 / 18.00 | match |
| claude-opus-5 | 5.00 / 25.00 | `[VERIFIED: platform.claude.com/docs/en/about-claude/pricing]` 5.00 / 25.00 | match |
| claude-sonnet-5 | 2.00 / 10.00 | `[VERIFIED]` 2.00 / 10.00 — page's own note confirms the scheduled 2026-09-01 increase to $3/$15 "will not occur," still current | match |
| claude-haiku-4-5 | 1.00 / 5.00 | `[VERIFIED]` 1.00 / 5.00 | match |
| claude-opus-4-8 | 5.00 / 25.00 | `[VERIFIED]` 5.00 / 25.00 (legacy, active) | match |
| gpt-5.6-sol | 5.00 / 30.00 | `[VERIFIED: developers.openai.com/api/docs/pricing]` 5.00 / 30.00 | match |
| gpt-5.6-terra | 2.00 / 12.00 | `[VERIFIED]` 2.00 / 12.00 | match |
| gpt-5.6-luna | 0.20 / 1.20 | `[VERIFIED]` 0.20 / 1.20 | match |
| gpt-5.1 | 1.25 / 10.00 | `[VERIFIED]` 1.25 input / 0.125 cached / 10.00 output — conflict from a WebSearch aggregator ($0.625/$5.00) explicitly checked and **rejected**: unconfirmed anywhere on openai.com's own domain, three independent first-party fetches (2 from W3, 1 from the verifier) all agree on 1.25/10.00 | match, conflict resolved |
| gpt-5 | 1.25 / 10.00 | `[VERIFIED]` 1.25 / 10.00 | match |

Cache-read multiplier (0.1x, applied globally) independently re-checked against every model's own published cache-read rate — exact match on all checked (7 Gemini + all 4 Claude). gpt-4o/gpt-4.1's exclusion (cache discount diverges from the shared 0.1x) was independently confirmed **correct**, not overcautious.

**One new gap found during verification, after W3 had already closed:** `is_local_model()` treats any `ollama_chat/`, `ollama/`, or `vllm/` prefix as automatically zero-cost. Ollama has a real paid product, **Ollama Cloud**, that is routed through the *identical* prefix — only the `api_base` (localhost vs. `https://ollama.com`) differs, which the current check never inspects. A call genuinely routed through paid Ollama Cloud would be silently priced at $0.00. This is a real, code-level correctness gap — not fixed this phase (found after W3's work item closed, during the dedicated verification pass), documented here rather than silently absorbed, and flagged for Phase 3.

---

## W2.4 — actual `adk eval` CLI output (verbatim, real installed unpatched google-adk==2.6.3)

**PASS case** (`threshold=5.00`, real cost `$2.80`):
```
Eval Set Id: w2_cli_proof_eval_set
Overall Eval Status: PASSED
Metric: adk_tracegauge_cost_usd, Status: PASSED, Score: 2.8, Threshold: 5.0
```
Persisted `eval_history/*.evalset_result.json`: `"score": 2.8, "eval_status": 1` — non-null, the literal field that was `null` before this fix, independently re-confirmed by the verifier pass reading the file directly.

**FAIL case** (`threshold=1.00`, same real cost `$2.80`):
```
Eval Run Summary
w2_cli_proof_eval_set:
  Tests passed: 0
  Tests failed: 1
Overall Eval Status: FAILED
Metric: adk_tracegauge_cost_usd, Status: FAILED, Score: 2.8, Threshold: 1.0
```
Rationale: `FAILED: cost $2.800000 exceeds the configured threshold $1.000000 (over by $1.800000)`.

**`AgentEvaluator.evaluate()` no-crash proof** (threshold=0.01): completed with no `AssertionError` — the literal P0 fix.

**`AgentEvaluator.evaluate()` residual limitation, demonstrated live** (threshold=1000.0, real cost=$2.80, genuinely under budget per our own correct `eval_status=PASSED`): still raised —
```
Summary: `EvalStatus.FAILED` for Metric: `adk_tracegauge_cost_usd`. Expected threshold: `1000.0`, actual value: `2.8`.
AssertionError: adk_tracegauge_cost_usd for None Failed. Expected 1000.0, but got 2.8.
```
Root cause (source-confirmed, `agent_evaluator.py::_process_metrics_and_get_failures`): this harness recomputes PASS/FAIL itself via `mean(scores) >= threshold`, a hardcoded higher-is-better comparison that is structurally backwards for a lower-is-better metric like cost. This is an ADK-side limitation independent of adk-tracegauge's own correctness; the primary documented path (`adk eval` CLI / `LocalEvalService`, proven above) is unaffected. Both scenarios are permanent regression tests in `tests/test_agent_evaluator_integration.py`.

---

## W4 statistical validation

**4.3(a) — injected +20% regression, detected** (seed=1234/42, n=80/group):
```
mean_baseline=$0.010222  mean_current=$0.011741
effect=+$0.001520 (+14.87%)
95% CI [+0.001007, +0.002023]
statistically_significant=True  practically_significant=True  →  status=regression
```

**4.3(b) — measured false-positive rate, original run** (250 trials, n=40/group, identical generator, seed=90000/42):
```
false_positives=5/250 = 2.00%
nominal one-sided expectation at 95% CI: 2.5%
```

**Independent adversarial re-check by the verifier, different seed (777), 250 trials, no shared state with the original run:**
```
false_positives=4/250 = 1.60%
```
Both measured rates (2.00%, 1.60%) sit close to the 2.5% nominal one-sided expectation, ruling out the original figure being a cherry-picked lucky seed. No evidence of miscalibration.

---

## Verification methodology

Two independent verifier passes ran after all six work items closed:
1. **Tests/git-state/statistics verifier**: fresh clone-state test run (210/210 confirmed, 99% coverage, ruff/mypy clean), re-ran W2's `adk eval` proof fresh, re-ran W4's regression/FP tests fresh plus an adversarial different-seed FP measurement, spot-checked 4 price entries, confirmed all git/gh state claims (topics, releases, branch state, keras thread resolution), re-ran all 3 examples fresh. **Result: all 6 numbered claims CONFIRMED, zero contradictions.**
2. **Pricing verifier**: adversarially re-fetched all 21 price-table rows from live vendor pages independently, with no access to the implementing agents' reasoning. **Result: 21/21 match**, gpt-5.1 conflict definitively resolved, cache-multiplier exclusions confirmed correct, and one new gap found (Ollama Cloud pricing, documented above).

No claim in this report rests solely on a single agent's self-report — every numeric claim was either independently re-derived by a separate verifier or is a deterministic, seed-pinned test that reproduces exactly on every run.

---

## Before/after summary

| Metric | Phase 1 end-state | Phase 2 end-state |
|---|---|---|
| Tests | 67 passing | **210 passing** |
| Coverage | 99% | **99%** (3 pre-existing/defensive uncovered lines, none new) |
| Priced models | 7 (Gemini only, flat-rate) | **19 distinct model families** across 3 vendors (10 Gemini, 4 Claude, 5 GPT — 2 Gemini families carry a long-context tier, so the diff table above has 21 rows) plus local-model zero-cost handling |
| `adk eval` integration | Broken — `AssertionError` unconditionally, `score: null` | **Fixed** — real PASS/FAIL, real dollar score, persisted correctly |
| CI regression detection | None | **`tracegauge check`**, bootstrap-CI, measured FP rate ~2% |
| Python versions tested by CI | 1 (3.11 only) | **4** (3.10–3.13, locally verified; GitHub Actions run itself pending push) |
| google-adk pin | `<2.7.0` (excluded live release) | **`<2.8.0`** (admits live 2.7.0, re-verified against full suite first) |
| README quickstart LOC | ~30 (6-step harness, no working `adk eval` path) | **4 lines** + 1 threshold line, real `adk eval` path, zero private-API calls |
| Docs beyond README | None | examples/ (3, all run), CHANGELOG, CONTRIBUTING, 2 issue templates, troubleshooting.md |
| GitHub topics | None | **5 set live** |
| GitHub Releases | None (tags only) | **3 backfilled with real notes** |

---

## ROUTE-TO-GG list

**Empty.** No item this phase required escalation to a human for a 2FA-gated or browser-only action, or a genuine judgment call outside this session's authorized scope. The one item Phase 1 flagged as a likely ROUTE-TO-GG candidate (resolving keras-team/keras#23420's review thread) succeeded directly via the GraphQL API this phase — GitHub allows a PR's own author to resolve threads on their own PR regardless of reviewer/maintainer status, contrary to the Phase 1 expectation.

## Human TODOs (not ROUTE-TO-GG — these are follow-ups on this session's own deferred, non-destructive items)

1. Review this branch (`feat/cost-regression-gate`, 17 commits ahead of `main`) and push it: `git push -u origin feat/cost-regression-gate`.
2. After push, confirm the CI matrix is actually green on GitHub's `ubuntu-latest` runners (only verified locally, on Windows, this session).
3. After push, trigger the price-freshness/canary workflows once for real: `gh workflow run pypi-canary.yml --repo gaurav-gandhi-2411/adk-tracegauge` (needs a pushed ref; deferred this session because the branch is intentionally unpushed).
4. Optional: delete the 5 already-merged branches on `origin` (local deletions already done): `git push origin --delete chore/0.1.0-release chore/0.2.0-release chore/rc1-version-bump ci/pypi-trusted-publishing docs/releasing`.
5. Decide on the Ollama Cloud pricing gap (documented above) — fix now or explicitly defer to Phase 3.
6. When ready to ship: bump `pyproject.toml`'s version (CHANGELOG proposes 0.3.0, justified by W2's breaking threshold-requirement change), open a PR from this branch, and only then tag/publish — none of which happened this session per the standing "no publish, no tag, no merge without reporting first" instruction.

---

## Deferred to Phase 3 (not built, per instruction)

OTel span-attribute export, agent trajectory analysis, deterministic trace replay, HTML report, failure-mode clustering, LLM-judge scoring. Plus, newly surfaced this phase: fixing the Ollama Cloud pricing gap, and confirming the CI matrix on real GitHub Actions runners.
