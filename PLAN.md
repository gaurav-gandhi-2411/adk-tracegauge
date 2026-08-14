# PLAN — Phase 2: cost regression gate

Tier: T1 portfolio project. Branch: `feat/cost-regression-gate`.

Reframe (per Phase 2 kickoff, accepted): adk-tracegauge is no longer "a cost gauge" — it is
"the cost regression gate for ADK evals." Register one metric, get a per-invocation USD cost
with a PASS/FAIL threshold verdict inside `adk eval`, plus a CI gate that fails on statistically
significant cost regression.

## Corrections applied to Phase 1 (docs/audit/PHASE1_DIAGNOSIS.md)
- C1: D2 (stale google-adk pin / canary never run) downgraded P1→P2. Canary workflow committed
  2026-08-13 19:15 (Thu); cron is Mondays only; no Monday has elapsed as of 2026-08-14. VERIFIED:
  `git log -1 --format=%ci .github/workflows/pypi-canary.yml`.
- C2: D1 fix takes the threshold-redesign branch only, not the wrapper-exclusion branch.
- C3: Roadmap #7 (multi-provider pricing) promoted to top tier (this phase, W3).
- C4: NEW P0 — price-table correctness was never verified in Phase 1. W1, do first.
- C5: NEW P1 — unguarded private-API dependency on `EvaluationGenerator.convert_events_to_eval_invocations`.
- C6: OTel export ranks below gate work; deferred to Phase 3 per kickoff.

## Work items (sequenced — most share files, executed in dependency order on one branch)
- [x] W1 — Price correctness (P0, do first): schema audit, live pricing diff, tiering/cache-read/batch
      discount checks, fixes, staleness guard + rationale field, price-freshness.yml CI, tests.
      DONE 2026-08-14, commit 7107527. Findings: gemini-3.6-flash was priced at the wrong (post-promo)
      rate; 3 models missing entirely (gemini-3.7-flash, gemini-3.1-flash-lite, gemini-3.1-pro-preview);
      long-context tiering (>200k tokens) existed and was unmodeled for gemini-2.5-pro/gemini-3.1-pro-preview
      -- now modeled via resolve_model_for_call + synthetic "<model>-long-context" table entries;
      thoughts_token_count and tool_use_prompt_token_count were silently dropped (undercounting) -- thoughts
      now folded into output cost, tool_use_prompt refused (fails closed, no verified rate). Cache-read
      (0.1x) and batch-out-of-scope were re-verified and found already correct. STALE_THRESHOLD_DAYS
      180->90. price_as_of now in every rationale. 67->97 tests passing, 99% coverage. Full diff table and
      per-row verification tags in the session report (not yet written to docs/audit/PHASE2_REPORT.md --
      that's the final wrap-up item at the bottom of this file, still open).
- [x] W2 — Threshold gate (fixes P0/D1): CostEfficiencyEvaluator redesigned to return real PASSED/FAILED,
      no path resolves to NOT_EVALUATED for a priceable model. Depends on W1 (price_as_of in rationale).
      DONE 2026-08-14, commit PENDING_W2_SHA. New `CostThresholdCriterion(BaseCriterion)` (reuses
      `threshold`, opposite comparison direction: PASSED iff cost<=threshold). Constructor now requires a
      threshold (criterion= preferred, deprecated eval_metric.threshold= supported) -- raises ValueError if
      neither set, no silent always-PASS default (rejected as a gate that looks green while checking
      nothing). Unpriceable invocations (no usage, unresolved model, streaming anomaly, unpriced component)
      still correctly report NOT_EVALUATED -- a distinct, legitimate "couldn't verify" case, not the old bug.
      Per-case overall_eval_status: FAILED dominates; else PASSED if >=1 invocation passed (deliberately
      NOT "PASSED only if all passed" -- source-confirmed LocalEvalService blanks every per-invocation
      result for a case whenever overall_eval_status is NOT_EVALUATED, so a stricter rule would destroy
      real per-invocation data in any case mixing a priced+passing invocation with an unpriceable one).
      Proven end to end against the real installed google-adk==2.6.3 (not reimplemented): `adk eval` CLI run
      twice (threshold=5.00 -> PASSED, score=2.8, non-null; threshold=1.00 -> FAILED, score=2.8) with real
      persisted `eval_history/*.evalset_result.json` (score/eval_status/criterion all correct, verified by
      reading the JSON directly) -- this was the literal Phase 1 regression (score:null), now fixed. Two new
      persistent tests (tests/test_agent_evaluator_integration.py) drive the real `AgentEvaluator.evaluate()`
      end to end: one proves the P0 (unconditional AssertionError, "no threshold avoids this") is fixed --
      a threshold now exists where it completes cleanly; the other documents a real, source-confirmed
      residual ADK-side limitation this package cannot fix from its own code -- `agent_evaluator.py::
      _process_metrics_and_get_failures` recomputes PASSED/FAILED itself from raw scores and the deprecated
      legacy threshold field via `mean(scores)>=threshold` (hardcoded higher-is-better, ignoring this
      evaluator's own eval_status), always populated the same way by `get_eval_metrics_from_config`
      regardless of plain-float-vs-criterion-object config shape -- so it can still misclassify a
      genuinely-under-budget run as FAILED. A permissive legacy-field sentinel (e.g. 0.0) was considered and
      rejected: it would make that one harness's gate permanently PASS regardless of real cost, which is
      worse than the original bug. `adk eval`/LocalEvalService are unaffected (read real eval_status
      directly) -- that's the primary target per this phase's reframe and is fully, correctly fixed with no
      caveats. Neither of GG's two open upstream PRs (adk-python#6682, #6710) touches this function --
      independent finding, not blocking. 97->107 tests passing, 99% coverage (one pre-existing uncovered
      line, `approximate` branch, unrelated to W2). README's "Read this first" and 4 other sections
      corrected (were actively false after this fix) -- full rewrite remains W5 scope.
- [ ] W3 — Multi-provider pricing (promoted): Claude/GPT price entries, local models → cost 0.0 + PASSED,
      LiteLlm-prefix resolution, actionable unknown-model error. Depends on W1 schema + W2 PASSED framing.
- [ ] W4 — CI regression gate (the differentiator): `tracegauge check --baseline`, bootstrap CI,
      min-n refusal, synthetic fixture validation incl. measured false-positive rate. Depends on W1-W3.
- [ ] W6 — Hygiene: CI matrix 3.10-3.13, pin bump to admit 2.7.0 (after full suite green), GitHub topics,
      release backfill, dist/branch/assertion cleanup, oss-contrib branch sync, keras#23420 thread attempt.
- [ ] W5 — DX/adoption (last — documents everything above): eliminate/wrap private-API dependency (C5),
      README quickstart rewrite, examples/, badges, real passing+failing gate captures, CHANGELOG/CONTRIBUTING/
      issue template, trigger and document the 3 deferred misconfiguration errors.
- [ ] Verifier pass: independently re-run every test and every price figure.
- [ ] docs/audit/PHASE2_REPORT.md: what changed per item, before/after numbers, W1.2 price diff table,
      W2.4 actual `adk eval` output, W4.3(b) measured false-positive rate, numbered ROUTE-TO-GG list.

Deferred to Phase 3 (do not build now): OTel span-attribute export, trajectory analysis,
deterministic replay, HTML report, failure clustering, LLM-judge scoring.

Rules: zero-cost (local Ollama only, no paid API, never set ANTHROPIC_API_KEY); commit per work
item on this feature branch; no PyPI publish, no tag, no merge to main without reporting first.
