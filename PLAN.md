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
- [x] W4 — CI regression gate (the differentiator): `tracegauge check --baseline`, bootstrap CI,
      min-n refusal, synthetic fixture validation incl. measured false-positive rate. Depends on W1-W3.
      DONE 2026-08-14, commit (pending). New `[project.scripts] tracegauge = "adk_tracegauge._cli:main"`
      entry point, two subcommands: `tracegauge snapshot --entrypoint module:callable --output path.json`
      (imports and calls a zero-arg callable that runs your eval; snapshots either its returned
      UsageStore or DEFAULT_USAGE_STORE if it populated that as a side effect -- there is no other way
      to hand a fresh CLI process a live in-memory UsageStore) and `tracegauge check --baseline b.json
      --current c.json [--confidence 0.95] [--min-effect-usd 0.0001] [--min-effect-pct 5.0] [--min-n 30]
      [--n-boot 10000] [--seed 42]`. New `snapshot.py` (public) defines the on-disk format nothing in
      this repo previously had: schema_version=1 JSON, one record per raw invocation_id (invocation_id,
      cost_usd, tokens_input/output/cache_read, models, call_count) plus a `skipped` list (invocation_id
      + reason) for anything that failed to price -- never silently dropped or fabricated. New
      `_regression.py`: stdlib-only (random/statistics/math, no numpy/scipy) percentile bootstrap on the
      difference in means, one-sided (only a cost *increase* counts as a regression), 10,000 resamples
      default, seed=42 hardcoded default per project convention. A verdict requires BOTH statistical
      significance (bootstrap CI lower bound > 0) AND practical significance (effect clears
      --min-effect-usd OR --min-effect-pct, default $0.0001 / 5%) -- documented explicitly as an AND
      between two different questions ("is this real?" vs. "do we care?"), not a single threshold.
      min_n=30 default, justified by the standard CLT/bootstrap-stability rule of thumb (Efron &
      Tibshirani), not guessed -- refuses to emit a verdict below it (status="insufficient_data",
      distinct exit code 3, vs. 0=pass/1=regression; argparse itself owns exit code 2 for malformed
      invocations). Every `check` run prints n, CI bounds, and effect size regardless of verdict --
      never only on failure. numpy/scipy ARE already-transitive deps via google-adk[eval]'s
      scikit-learn/pandas chain (confirmed via uv.lock) but deliberately not used directly -- an
      undeclared transitive dependency for the package's core differentiator was judged too fragile for
      "a small focused tool"; stdlib is plenty fast at this n/n_boot scale. Refactored `compute_session_cost`'s
      sole sanctioned call site (test_pricing_call_site.py's structural guard) out of evaluator.py into
      `_adapter.price_digest`, so snapshot.py could become a second real caller without violating or
      loosening that guard -- evaluator._price_digest kept as a thin alias so existing imports don't
      break; the guard test itself updated to match (still asserts exactly one real compute_session_cost
      call site, just relocated). 4.3(a) synthetic fixture (deterministic seed=1234/42, n=80/group,
      injected +20% mean-cost regression): MEASURED mean_baseline=$0.010222, mean_current=$0.011741,
      effect=+$0.001520 (+14.87%), 95% CI [+0.001007, +0.002023] -- gate correctly fires
      (status="regression"). 4.3(b) false-positive rate (250 independent deterministic trials, n=40/group,
      both groups drawn from the identical generator, min_effect floors set to 0.0 to isolate the pure
      statistical test): MEASURED 5/250 = 2.00% false positives, in line with the ~2.5% nominal one-sided
      expectation at 95% confidence -- no evidence of miscalibration. Both fixture validations are
      permanent pytest tests (tests/test_regression.py), not one-off scripts, and are fully deterministic
      (hardcoded seeds throughout) so the measured numbers reproduce exactly on every future run.
      GitHub Actions snippet (run eval -> snapshot -> compare -> fail build on regression) written to
      `docs/ci-snippet.md` as the single canonical source for W5's README rewrite to pull from verbatim.
      New test files: test_regression.py (30 tests incl. both 4.3 fixtures), test_snapshot.py (11 tests),
      test_cli.py (17 tests). 199 total tests passing (152->199, +47), 99% coverage. ruff/ruff-format/mypy
      all clean. Live end-to-end smoke test (not just pytest): real `uv run tracegauge snapshot`/`check`
      invocations via the actually-installed console script against a real regressed pair of synthetic
      UsageStores -- confirmed real exit code 1 on a genuine regression, matching the documented contract.
- [x] W6 — Hygiene: CI matrix 3.10-3.13, pin bump to admit 2.7.0 (after full suite green), GitHub topics,
      release backfill, dist/branch/assertion cleanup, oss-contrib branch sync, keras#23420 thread attempt.
      DONE 2026-08-14, commits 85918e7/6971a33/bff7006/0ee18b2/5a591e5. 6.1: adding the 3.10 leg to the
      matrix surfaced a REAL bug before it ever reached CI, not a hypothetical one -- `snapshot.py` imported
      `datetime.UTC` (stdlib-only since 3.11) despite `requires-python = ">=3.10"`; a fresh Python 3.10.20
      install (uv-managed, via `uv sync --frozen --python 3.10` into a scratch venv) failed collection on
      `ImportError: cannot import name 'UTC' from 'datetime'`. Root-caused and fixed: `timezone.utc` (stdlib
      since 3.2) instead of the 3.11-only alias. Also fixed a second-order cause of the same class of bug:
      `[tool.ruff] target-version` was `"py311"` against a `">=3.10"` floor, so ruff's own UP017 pyupgrade
      rule was actively suggesting `datetime.UTC` over `timezone.utc` -- fixed to `"py310"` so ruff stops
      recommending 3.11+-only syntax on future contributions. Full 199-test suite then run clean on both
      Python 3.10.20 and 3.13.5 (the two extremes; scratch venvs via `uv sync --frozen --python X` with
      `UV_PROJECT_ENVIRONMENT` redirected out of the repo) -- 199 passed, 99% coverage, identical on both.
      One test-harness false trail along the way, resolved and NOT a project defect: an initial attempt using
      the session's default deeply-nested scratchpad path hit Windows' MAX_PATH (260 chars) mid-install,
      silently truncating/corrupting `google-cloud-aiplatform`'s deeply-nested generated schema tree
      (`ImportError`/`ModuleNotFoundError` that looked version-specific but reproduced identically on 3.10
      AND 3.13 and vanished once venvs were moved to short paths under `C:\Users\gaura\tmp\`) -- root-caused
      by measuring the exact failing path length (260 chars, confirmed via direct count) before concluding
      per rule 101c, not accepted as an unexplained flake. 3.10/3.13 not available as bare system Pythons
      (`py -0` showed only 3.11.9/3.11.15/3.12.12/3.14.4); 3.10.20 installed via `uv python install 3.10`
      (zero-cost, local, ~7s), 3.13 satisfied by the already-installed anaconda3 3.13.5 that `uv venv
      --python 3.13` resolved to. CI matrix itself committed to `ci.yml` (`python-version: ["3.10", "3.11",
      "3.12", "3.13"]`, lint/format/mypy gated to run once on 3.11 only via `if:`) but NOT yet proven green
      in real GitHub Actions -- this branch is unpushed per session constraint, so only this session's local
      verification exists; CI's own ubuntu-latest run is a TODO for whoever pushes.
      6.2: full 199-test suite run for real against live google-adk==2.7.0 (scratch venv, `google-adk[eval]
      ==2.7.0 --no-deps` over the locked base, matching Phase 1's own tolerate-the-pin-conflict install
      pattern) -- 199 passed, 99% coverage, zero code changes required. Pin bumped `<2.7.0`-><2.8.0`
      (pyproject.toml + `uv lock`); checked PyPI's JSON API directly and confirmed 2.8.0 does not exist yet,
      so `<2.8.0` is the honest ceiling, not a guess. `uv.lock`'s actual resolved version stays 2.6.3 (uv's
      conservative-resolution default) -- this only widens what the range *admits*. Canary dispatch
      (`gh workflow run pypi-canary.yml`) deferred: `workflow_dispatch` needs a pushed ref and this branch
      isn't pushed -- TODO for after push, documented in the commit body.
      6.3: GitHub topics set live (`google-adk`, `agent-development-kit`, `llm-cost`, `llm-evaluation`,
      `opentelemetry`), verified via `gh repo view --json repositoryTopics`. No commit (repo metadata, not a
      tracked file).
      6.4: `release.yml` now runs `gh release create "${{ github.ref_name }}" --generate-notes` as a final
      step, gated to run only after PyPI publish succeeds (`contents` permission bumped read->write for the
      default token). 3 existing tags backfilled live: v0.1.0rc1, v0.1.0, v0.2.0 all now have real
      auto-generated notes citing their actual merged PRs (verified via `gh release view v0.2.0`, notes cite
      PR #4/#5 by title with real links) -- confirmed via `gh release list`, all 3 present, v0.2.0 correctly
      marked Latest.
      6.5: stale `dist/adk_tracegauge-0.1.0-*` (whl+tar.gz) removed, `dist/.gitignore` untouched. 5
      already-merged local branches re-verified (not blindly trusted from Phase 1) via content-level diff --
      `chore/0.2.0-release` empty-diffed against `main` directly; `ci/pypi-trusted-publishing`'s single
      commit (4e49f87) content-diffed as empty against main's corresponding squash-merge commit (4283cf8,
      PR #1) since main has since moved past the squash point -- both genuinely fully merged, not just
      SHA-different. All 5 deleted locally (`git branch -d`). Remote deletion (`git push origin --delete`)
      explicitly deferred, not attempted -- branch deletion is a standing pause-for-confirmation item (rule
      55) and the task's own instructions gave an explicit safe fallback for exactly this case; TODO for a
      human to run `git push origin --delete chore/0.1.0-release chore/0.2.0-release chore/rc1-version-bump
      ci/pypi-trusted-publishing docs/releasing` if desired. 2 shallow `is not None` assertions in
      `test_registration.py` strengthened to real identity/type checks (`is TraceGaugeUsagePlugin`, `is
      DEFAULT_USAGE_STORE`, `isinstance(..., UsageStore)`), matching the file's own existing
      `CostEfficiencyEvaluator` identity-check pattern. oss-contrib's `adk-python` checkout: 3 of 4 local PR
      branches were behind their own origin fork (105/39/105 commits respectively, grown from Phase 1's
      "40-96" as more upstream activity landed) -- re-verified fast-forward safety first
      (`git log origin/<branch>..<branch>` empty for all 4, confirming zero local-only commits would be
      lost) before syncing: the checked-out branch via `git merge --ff-only`, the other two via `git fetch
      origin <branch>:<branch>` (updates the local ref without checkout, avoiding 2 unnecessary branch
      switches in someone else's possibly-shared checkout). `fix/eval-metric-threshold-criterion-resolution`
      needed no action (already 0 commits behind). Working tree confirmed clean before and after.
      6.6: `resolveReviewThread` GraphQL mutation on keras-team/keras#23420's review thread SUCCEEDED
      directly (`isResolved: true`, verified by re-fetching) -- contrary to Phase 1's expectation of a likely
      permission error for an external contributor (`authorAssociation: NONE`); GitHub evidently allows a
      PR's own author to resolve conversation threads on their own PR regardless of reviewer/maintainer
      status. No ROUTE-TO-GG item needed for this one; Phase 1's "may be permission-gated" was a reasonable
      caveat that turned out not to bind.
      Final verification: `uv sync --frozen` + full suite in the repo's own `.venv` (post all W6 changes,
      including the pin bump) -- 199 passed, 99% coverage, ruff/ruff-format/mypy all clean. `git status`
      clean. 5 commits on this branch for W6 (kept split by concern per rule 39a: the 3.10 compat fix is
      logically separate from the CI matrix that surfaced it, which is separate from the pin bump, the
      release-workflow change, and the test-assertion strengthening).
      TODOs left for after this branch is pushed/reviewed: (1) trigger `pypi-canary.yml` for real via
      `workflow_dispatch`; (2) confirm the new 3.10-3.13 CI matrix is actually green on ubuntu-latest (only
      locally verified this session, on Windows); (3) optionally `git push origin --delete` the 5
      already-merged branches (local deletion done, remote deferred per rule 55).
- [x] W5 — DX/adoption (last -- documents everything above): eliminate/wrap private-API dependency (C5),
      README quickstart rewrite, examples/, badges, real passing+failing gate captures, CHANGELOG/CONTRIBUTING/
      issue template, trigger and document the 3 deferred misconfiguration errors.
      DONE 2026-08-14, commit (pending). 5.1: confirmed by grep -- nothing under src/adk_tracegauge/ calls
      the private EvaluationGenerator.convert_events_to_eval_invocations internal; W2's after_model_callback
      workaround + adk eval/AgentEvaluator (this phase's primary documented path) never touches it, since
      LocalEvalService/AgentEvaluator do their own internal Event->Invocation conversion. It's still needed
      by the optional hand-rolled sub-agent-rollup harness (test_e2e_runner.py, README's sub-agent section) --
      wrapped in new src/adk_tracegauge/_compat.py: convert_events_to_eval_invocations() runs a best-effort
      version check against a KNOWN_TESTED range mirroring the pyproject pin (warns, doesn't block, on
      mismatch -- an out-of-range version is often still compatible, per W6's 2.7.0 finding) and converts a
      bare ImportError/AttributeError into an actionable RuntimeError naming the installed version and which
      integration path is affected. test_e2e_runner.py updated to call the wrapper instead of importing
      EvaluationGenerator directly. New tests/test_compat.py (11 tests): version parsing, out-of-range warning
      (real call still succeeds), and both simulated-unsupported-version failure paths (missing module,
      missing method) via monkeypatch, each asserting the actionable RuntimeError text.
      5.2: quickstart measured for real via examples/01_minimal_cost_gate.py -- 4 lines of
      adk-tracegauge-specific Python (import adk_tracegauge; from adk_tracegauge import
      TraceGaugeUsagePlugin; _usage_plugin = TraceGaugeUsagePlugin(); after_model_callback=... wiring) + 1
      line of threshold config (test_config.json's criteria dict), ZERO private-API calls (down from Phase
      1's "3 lines + 1 mandatory private-API call"). Both real `adk eval` CLI runs (PASSED at threshold=5.00,
      FAILED at threshold=1.00, real cost $2.80 both times, deterministic fixed-cost fake LLM double --
      zero-cost, no network call) took 31.6s wall-clock total (cold uv/ADK-import overhead included) this
      session, google-adk==2.6.3. REAL FINDING surfaced by this measurement, not previously documented
      anywhere in this repo: `adk eval`'s own process exit code does NOT reflect PASSED/FAILED -- verified
      live, exit 0 in both runs regardless of the printed Overall Eval Status. Documented prominently in the
      README quickstart and "Known limitations" -- this is exactly why `tracegauge check`'s own real,
      distinguishable exit codes (0/1/3) matter for CI gating, strengthening W4's differentiator rather than
      undercutting it.
      5.3: examples/ created, 3 scripts, each actually run this session (not just written) --
      01_minimal_cost_gate.py (the quickstart, real adk eval CLI subprocess, both PASS/FAIL captured);
      02_subagent_rollup.py (real InMemoryRunner + AgentTool two-agent delegation, no mocking -- root
      $0.525 across 2 turns + delegated sub-agent $0.04 = $0.565 rolled-up, verified against the price table
      by hand and matching the script's real printed output exactly, 14.7s); 03_ci_regression_gate.py (real
      `tracegauge snapshot`+`tracegauge check` subprocesses via `python -m adk_tracegauge._cli` -- chosen
      over `python -c "...; main()"` after discovering the latter doesn't propagate main()'s return value to
      the process exit code, only the `if __name__=="__main__": sys.exit(main())` guard does; real detected
      regression, exit code 1, 51.9s). Real terminal captures from all 3 pulled into the README verbatim.
      5.4: badges added -- PyPI version (dynamic, live v0.2.0), CI status (live GitHub Actions badge, "CI -
      passing"), Python versions (dynamic, 3.10|3.11|3.12|3.13), License. All 4 URLs verified to return HTTP
      200 with real (not placeholder) SVG content this session. Real passing + failing `adk eval` captures
      (from 5.2's run) and real passing + failing `tracegauge check` captures (from 5.3's run) both in the
      README as fenced code blocks, not screenshots -- consistent with this being a CLI tool.
      5.5: DEFAULT_USAGE_STORE decision -- kept public (not renamed to _DEFAULT_USAGE_STORE). Checked first:
      test_registration.py asserts on the public name directly (test_public_exports_are_importable), and
      docs/ci-snippet.md's own documented CLI pattern ("simply lets the calls land in
      adk_tracegauge.DEFAULT_USAGE_STORE as a side effect") depends on it being public -- renaming would be
      a real breaking change for zero benefit, violating Phase 1's "no gratuitous breaking changes" finding.
      Documented instead: new README subsection under "What this actually is" explaining why it exists (ADK's
      MetricEvaluatorRegistry only ever constructs a registered evaluator as
      EvaluatorClass(eval_metric=eval_metric) -- no channel to hand it a custom store) and when to construct
      your own UsageStore()+store= instead (isolation between concurrent evals, tests) -- Phase 1's D11
      finding (real export, zero README mentions) is now closed.
      5.6: CHANGELOG.md (retroactive 0.1.0rc1/0.1.0/0.2.0 entries derived from `gh release view <tag>` +
      git log, not invented; Unreleased section for this phase's actual Added/Changed/Fixed, proposing next
      version 0.3.0 per this project's own 0.x convention -- middle digit for breaking changes pre-1.0,
      justified by W2's real breaking change (threshold now required, ValueError instead of permanent
      NOT_EVALUATED) -- pyproject.toml's version NOT bumped, no tag, no publish, per this work item's own
      scope). CONTRIBUTING.md (dev setup, test/lint/mypy commands, branch/commit conventions, and why the
      price-freshness + canary CI jobs are scheduled not just push-triggered). 2 GitHub issue templates
      (.github/ISSUE_TEMPLATE/bug_report.yml, price_correction.yml -- the latter directly tied to the
      price-freshness mechanism per this item's own instruction).
      5.7: all 3 misconfiguration errors triggered live this session, real text captured into
      docs/troubleshooting.md (referenced from README): (a) wrong google-adk version -- force-installed
      google-adk[eval]==1.0.0 (well outside the >=2.6.0,<2.8.0 pin) into a scratch venv with this branch's
      adk-tracegauge installed editable; real `ModuleNotFoundError: No module named
      'google.adk.evaluation.metric_evaluator_registry'` at import time (2.0.0 still worked fine -- had to
      go older to find a genuine break, confirming the pin's floor is conservative, not arbitrary); (b)
      unknown model -- real captured warnings.warn text naming the exact failing model and every known
      model key; (c) missing threshold -- real captured ValueError text. All three pasted verbatim, not
      reconstructed from memory.
      Verification: full suite 199->210 tests passing (+11, test_compat.py), 99% coverage (3 uncovered
      lines total, all pre-existing and unrelated to this work item: _cli.py:222's `if
      __name__=="__main__"` guard, evaluator.py:336's pragma:no-cover branch, snapshot.py:132's defensive
      `if not calls: continue` -- unreached because store.invocation_ids() only ever returns ids that have
      at least one recorded call) -- ruff check/ruff format --check/mypy src/ all clean. All 3 examples re-run clean one final time after
      every change in this work item, producing byte-identical output to their first runs (deterministic
      seeds throughout). Global ~/.adk/config.json set to {"telemetry": false} (a one-time local ADK CLI
      setting on this machine, not a repo file) to unblock non-interactive `adk eval` invocations during
      this session's live testing.
- [x] Verifier pass: independently re-run every test and every price figure. DONE 2026-08-14, two parallel
      passes (tests/git/gh state + statistics with a different seed; adversarial pricing re-check against
      live vendor pages). All 6 numbered claims CONFIRMED, all 20 price entries CONFIRMED against live
      sources, gpt-5.1 conflict definitively resolved ($1.25/$10 correct). One new gap found: Ollama Cloud
      (paid) shares the `ollama_chat/`/`ollama/` prefix with local Ollama, so `is_local_model()` cannot
      distinguish them -- a cloud-routed call would be silently priced at $0.00. Not fixed this phase
      (found during verification, after W3 closed) -- documented as a known limitation in PHASE2_REPORT.md
      and flagged for Phase 3.
- [x] docs/audit/PHASE2_REPORT.md: written 2026-08-14.

Deferred to Phase 3 (do not build now): OTel span-attribute export, trajectory analysis,
deterministic replay, HTML report, failure clustering, LLM-judge scoring.

Rules: zero-cost (local Ollama only, no paid API, never set ANTHROPIC_API_KEY); commit per work
item on this feature branch; no PyPI publish, no tag, no merge to main without reporting first.

## Phase 3

Two release-blocking findings from Phase 2's verification pass, fixed on the same branch
(`feat/cost-regression-gate`), same rules (zero-cost, no publish/tag/merge without reporting).

- [x] B1 -- Ollama Cloud silent-zero fix. DONE 2026-08-14/15, commit `eac066e`. 1.1: confirmed
      `is_local_model()` was a bare string-prefix check (`ollama_chat/`, `ollama/`, `vllm/`) on the
      raw `model_version`, nothing else. 1.2: read google-adk's `models/lite_llm.py`,
      `models/llm_response.py`, and `agents/context.py` directly -- confirmed NOT distinguishable:
      `LlmResponse` (pydantic, `extra="forbid"`) is built from litellm's bare `response.model`
      string with no host/endpoint field in its schema at all; `CallbackContext` (=
      `agents/context.py`'s `Context`) and the `InvocationContext` it wraps expose no reference to
      the underlying `LiteLlm` model client instance, which is the only place `api_base` is stored
      (`LiteLlm._additional_args`). 1.3: took the "not distinguishable" branch -- new
      `ADK_TRACEGAUGE_ASSUME_LOCAL` env var (`_pricing.py`), two forms: `1`/`true`/`yes`/`on`
      (assert every recognized local prefix) or a comma-separated subset (e.g. `vllm/`, to trust
      one prefix while `ollama_chat/` -- the exact prefix Ollama Cloud shares -- still fails
      closed). New `is_local_model_asserted()` is the actual gate `resolve_model_for_call()` uses;
      `is_local_model()` alone is now documented as structural-only, never sufficient. Without the
      opt-in, a local-prefixed model returns `None` (NOT_EVALUATED upstream, consistent with
      W2/W3's existing unpriceable-model handling), and `unknown_model_message()` gained a
      dedicated branch naming the model string, the Ollama Cloud reasoning, and the exact remedy.
      Rationale for the asserted-local case reworded to "local model, zero marginal cost, asserted
      via ADK_TRACEGAUGE_ASSUME_LOCAL" (not identical to the old implicit-default wording). 1.4:
      structural/property test iterates every entry in `_LOCAL_MODEL_PREFIXES` confirming none
      resolve to zero cost without the opt-in; separate tests cover the `1`/true-spelling case, the
      comma-separated allowlist case (including case-insensitivity and "a typo must never widen
      trust"), and the asserted-path rationale wording.
- [x] B2 -- promotional pricing time bomb fix. DONE 2026-08-14/15, commit `6d6f98a`. 2.1: full
      audit of every `note` field in `gemini_prices.json` -- exactly two genuinely promotional
      entries, `gemini-3.6-flash` and `gemini-3.7-flash` (promo through 2026-12-31). `claude-sonnet-5`
      carries historical "introductory pricing" language but is a settled standard rate (re-verified
      live against platform.claude.com: the vendor's own note confirms the scheduled 2026-09-01
      increase "will not occur"); no other Claude/GPT entry mentions a promotional period.
      `gemini-3.1-flash-lite` has no promo language, contrary to the risk the task instructions
      flagged as worth checking. 2.2: schema gains `promo_until` (ISO date) + `standard_rate`
      (`{input_usd_per_mtok, output_usd_per_mtok}`) per entry. New `_pricing._effective_rates()`
      computes the effective rate for "today": promotional while `date.today() <= promo_until`
      (boundary day inclusive -- matches vendor phrasing like "through December 31" and mirrors
      `is_stale`'s own boundary convention), standard once past it (if `standard_rate` is known).
      `ResolvedModel` gained `promo_until`/`promo_active`/`standard_rate_unknown`/
      `standard_rate_{input,output}_usd_per_mtok` fields, computed in `_entry_to_resolved()`.
      Critical finding during implementation: tracegauge's own `compute_turn_cost` reads
      `prices["models"][key]["input_usd_per_mtok"]` directly off whatever dict it's given (confirmed
      by reading `tes/cost.py`), with zero knowledge of `promo_until`/`standard_rate` -- so the
      auto-switch has to rewrite the raw dict, not just a `ResolvedModel` object nobody reads. New
      `effective_prices()` does that rewrite; `_adapter.price_digest` (the single sanctioned
      `compute_session_cost` call site, per the existing structural guard) now wraps every `prices`
      argument through it, so every real caller gets the switch automatically with zero
      per-call-site opt-in. `tests/test_pricing_call_site.py`'s guard updated to assert the new
      `prices=effective_prices(prices)` literal instead of the old `prices=prices` (still verifying
      the value is derived from the caller's own argument, not a re-fetched default). Evaluator's
      per-turn rationale states explicitly whether a promo is active (with expiry date) or has ended
      ("standard rate applied automatically"). 2.3: `gemini-3.6-flash`/`gemini-3.7-flash` both
      re-verified live against ai.google.dev -- the post-promo rate ($1.50/$7.50, effective
      2027-01-01) is a confirmed, published figure, not provisional, so neither hits the
      "genuinely unknown" branch. That branch is still fully implemented and tested:
      `ResolvedModel.standard_rate_warning_due` fires when `standard_rate` is unset and `promo_until`
      is within `PROMO_EXPIRY_WARNING_DAYS` (14, shared with 2.4's CI window) of today or already
      past; an unparseable `promo_until` is treated as due (fail closed). New
      `evaluator._promo_unknown_rate_warning()` surfaces this via both the rationale text and
      `warnings.warn`, same pattern as the existing `_stale_price_warning`. 2.4:
      `scripts/check_price_freshness.py` gained a second, independent check alongside the existing
      90-day staleness gate -- fails if any entry's `promo_until` is within 14 days of today (real
      output: `PROMOTIONAL ENTRIES EXPIRING SOON as of 2026-08-14 (within 14 days): - expiring-soon-model:
      promo_until=2026-08-20 (6 day(s) left) -- ...`) or already past (real output: `PROMOTIONAL
      ENTRIES ALREADY EXPIRED as of 2026-08-14: - already-expired-model: promo_until=2026-01-01
      (expired 225 day(s) ago) -- standard_rate should now be in effect; verify it's actually being
      applied ...`), reported as two distinct sections; verified live against the real bundled table
      (`OK: all 22 price entries fetched within 90 days of 2026-08-15, and no promotional entry
      expires within 14 days.`, exit 0) and against a synthetic table exercising both new failure
      branches (exit 1). 2.5: pre-expiry/post-expiry/boundary/unknown-standard-rate tests all added,
      following W1's existing pattern of computing dates as offsets from `date.today()` rather than
      mocking the clock (`dataclasses.replace`-style for `ResolvedModel`-level tests, `monkeypatch.
      setattr` on `load_gemini_prices` for full evaluator-level tests, matching the existing
      `test_stale_price_warning_names_only_the_stale_model_not_a_fresh_one` pattern exactly). Boundary
      choice documented and tested explicitly: the day `promo_until` itself is still promotional
      (`>=` for expiry, not `>`).
      Full suite: 210 -> 245 tests passing (+35), 99% coverage (100% on `_pricing.py`, up from 99%;
      3 pre-existing uncovered lines elsewhere unrelated to this work, unchanged from Phase 2).
      ruff/ruff-format/mypy all clean. `git status` clean after both commits.
