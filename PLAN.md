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
      DONE 2026-08-14, commit ea7262f. New `CostThresholdCriterion(BaseCriterion)` (reuses
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
- [x] W3 — Multi-provider pricing (promoted): Claude/GPT price entries, local models → cost 0.0 + PASSED,
      LiteLlm-prefix resolution, actionable unknown-model error. Depends on W1 schema + W2 PASSED framing.
      DONE 2026-08-14, commit (pending). Findings: read installed google-adk's models/lite_llm.py directly
      (both anaconda base env and the project's own .venv -- byte-identical, google-adk==2.6.3 pinned) --
      confirmed LlmResponse.model_version = litellm's own response.model, which echoes the requested
      "<provider>/<model>" string verbatim (e.g. "anthropic/claude-opus-5", "openai/gpt-5.1",
      "ollama_chat/qwen2.5:7b"), and confirmed _get_provider_from_model splits on the first "/". Added 9 new
      priced entries: claude-opus-5 ($5/$25), claude-sonnet-5 ($2/$10 -- VERIFIED not-promotional: the
      fetched pricing page's own dated note says the scheduled 2026-09-01 increase to $3/$15 "will not
      occur" and $2/$10 "is now the standard price"), claude-haiku-4-5 ($1/$5), claude-opus-4-8 ($5/$25,
      legacy-but-active), gpt-5.6-sol ($5/$30), gpt-5.6-terra ($2/$12), gpt-5.6-luna ($0.20/$1.20), gpt-5.1
      ($1.25/$10), gpt-5 ($1.25/$10) -- all VERIFIED against platform.claude.com/docs/en/about-claude/pricing
      and developers.openai.com/api/docs/pricing (openai.com/api/pricing/ 404'd, platform.openai.com/docs/pricing
      403'd/redirected -- used the redirect target, fetched twice independently plus cross-checked against
      developers.openai.com/api/docs/models). One real discrepancy caught and resolved: a WebSearch aggregator
      claimed gpt-5.1 was $0.625/$5.00; two independent direct fetches of the openai.com-domain pricing page
      both agreed on $1.25/$10.00 and disagreed with the aggregator -- went with the twice-confirmed
      first-party figure, documented the conflict in the JSON note rather than silently picking one.
      Deliberately did NOT add gpt-4o/gpt-4.1/o-series: their cache-read discount (0.25x-0.5x, verified from
      the same fetch) diverges from every other entry's 0.1x, and tracegauge's own tes.cost.compute_turn_cost
      has exactly ONE global cache_multipliers dict for the whole table with no per-model override -- adding
      those would silently under-price any cached call on them by 2.5x-5x. All 9 new entries independently
      verified to share the SAME 0.1x cache-read ratio as Gemini (Claude: $0.50/$5.00 Opus 5; GPT-5.x: e.g.
      $0.125/$1.25 gpt-5.1) -- this is WHY they could safely join the shared table at all. Local models
      (ollama_chat/, ollama/, vllm/ prefixes) resolve via a NEW explicit resolve_model_for_call short-circuit
      (is_local_model, checked before any price-table lookup) to a REAL zero-cost table entry
      (__local_zero_cost__, 0.0/0.0) rather than a bypass -- keeps local calls flowing through the identical
      compute_session_cost/threshold-gate pipeline as any priced call, so mixed local+cloud invocations sum
      correctly. Per-turn rationale now says "(local model, zero marginal cost)" explicitly, not silently.
      resolve_model now strips LiteLlm provider prefixes (anthropic/, openai/ only -- deliberately NOT
      bedrock/vertex_ai/azure, since Claude/GPT pricing there can diverge from first-party rates and silently
      mispricing is worse than failing closed) and a second dated-suffix pattern (-YYYY-MM-DD, the historical
      OpenAI snapshot convention) alongside the existing no-dash 8-digit one -- checked and confirmed CURRENT
      GPT-5.x model IDs are all bare/undated as of 2026-08-14, so this is defensive coverage for older
      LiteLlm-referenced deployments, not something exercised by a currently-observed real string; tested
      against a synthetic dated form of a real priced entry instead of asserting a fabricated "real" one.
      Added a minimal custom-price extension mechanism: ADK_TRACEGAUGE_PRICE_TABLE env var (mirrors
      tracegauge's own TES_PRICE_TABLE pattern exactly, for consistency) pointing to a whole-file JSON
      override -- no plugin system, per the work item's own explicit scope. unknown_model_message rewritten:
      no longer says "Gemini price table" (was stale per this item's own instructions once the table stopped
      being Gemini-only), now names the exact failing model key, distinguishes "should have auto-resolved as
      local" from "routed through a platform whose pricing can diverge" from "genuinely unknown vendor", and
      points at the env-var mechanism + GitHub issues. Deliberately did NOT rename gemini_prices.json /
      load_gemini_prices() (would ripple through _pricing.py, evaluator.py, 2 test files, the CI script, and
      the workflow file for a purely cosmetic gain -- rule 58b minimal-diff judgment call, flagged here for
      W5's docs pass rather than silently left unexplained) -- fixed the actually-instructed item (stale
      user-facing wording) without the rename's blast radius; module/JSON docstrings updated to explain the
      historical name explicitly so it doesn't read as an oversight. scripts/check_price_freshness.py's
      stale-entry guidance also fixed (was hardcoded to ai.google.dev regardless of which vendor's entry
      went stale -- now prints each entry's own source_url). 107->152 tests passing (45 new), 99% coverage
      (100% on _pricing.py and _adapter.py, the two files this item touched most; the one uncovered
      evaluator.py line is W2's pre-existing pragma: no cover, unrelated). ruff/mypy clean.
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
