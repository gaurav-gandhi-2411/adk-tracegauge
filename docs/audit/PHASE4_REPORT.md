# adk-tracegauge — Phase 4 Report: correctness, honesty, and process integrity

Branch: `feat/cost-regression-gate`. Not pushed, not tagged, not merged, not published.
Audit date basis: 2026-08-15/16. Written by the orchestrator after all seven work items
(R1–R7) and their required independent verifier passes completed.

---

## R1 — Independent history review (blocking, done first)

**Verdict: no evidence of harm.** Working tree clean, history genuinely linear, full test
suite independently re-run and confirmed matching the claimed end-state at every checkpoint.
All commit-scope, B5-commit, and CI/packaging-traceability claims independently re-verified
by a second agent with zero contradictions. Two non-blocking findings surfaced and resolved:

1. **B6's "docs-only" framing was loose, not wrong** — its commit touches 4 `src/` files, all
   confirmed to be pure docstring/comment fixes for renamed README section references, zero
   functional-line changes.
2. **The B5 coordination-incident causal mechanism (a dispatched fork racing its own parent in
   the shared checkout) was never written into `PLAN.md` at the time — only the resulting
   suspected-injection symptoms were.** This is a real documentation gap: an independent
   auditor reading only durable repository artifacts could not corroborate the fork-dispatch
   story, which existed only in an ephemeral session transcript. Closed this phase with a
   durable addendum to `PLAN.md`'s B5 entry, sourced explicitly to the orchestrator's direct,
   first-hand receipt of the B5 agent's own report — not re-derived or inferred after the fact.

### R1 per-commit table (32 commits, `main..HEAD`)

| SHA | Message | Scope match | Anomaly |
|---|---|---|---|
| 3454a3a | docs: Phase 1 diagnosis + Phase 2 plan | Yes | none |
| 7107527 | fix(pricing): P0 price correctness (W1) | Yes | none |
| 5129a9d | docs(plan): check off W1 | Yes | none |
| ea7262f | feat(evaluator): real PASSED/FAILED (W2) | Yes | none |
| 2063564 | docs(plan): fill in W2 SHA | Yes | none |
| eb8bf3e | feat(pricing): multi-provider (W3) | Yes | none |
| f90284d | feat(cli): tracegauge check (W4) | Yes | pyproject.toml `[project.scripts]` — expected, new entry point |
| 85918e7 | fix(compat): Python 3.10 (W6) | Yes | none |
| 6971a33 | ci: 3.10–3.13 matrix (W6) | Yes | none |
| bff7006 | chore(deps): pin bump (W6) | Yes | none |
| 0ee18b2 | ci(release): GitHub Release (W6) | Yes | none |
| 5a591e5 | test(registration): strengthen (W6) | Yes | none |
| 1cbbe2b | docs(plan): check off W6 | Yes | none |
| 28faf6f | feat(compat): wrap private API (W5) | Yes | none |
| 40fa498 | docs(examples): 3 examples (W5) | Yes | none |
| cb77359 | docs: README rewrite (W5) | Yes | none |
| 66b897f | docs: CHANGELOG/CONTRIBUTING (W5) | Yes | none |
| bf52765 | docs(plan): check off W5 | Yes | none |
| d77c9a2 | docs: Phase 2 report | Yes | none |
| eac066e | fix(pricing): Ollama Cloud opt-in (B1) | Yes | none |
| 6d6f98a | fix(pricing): promo auto-expiry (B2) | Yes | none |
| 5fd54e0 | docs(plan): record B1/B2 | Yes | none |
| cf20d74 | fix(evaluator): AgentEvaluator warning (B3) | Yes | none |
| 622f746 | docs(plan): record B3 | Yes | none |
| c1e9f80 | fix(regression): power grid + paired mode (B4) | Yes | pyproject.toml pythonpath += "scripts" — needed for the same commit's new script |
| fece6f6 | docs(plan): record B4 | Yes | none |
| **99d5204** | **test: mutation-test (B5)** | **Yes** | **PLAN.md-only, independently re-confirmed twice** |
| 2d38619 | docs: README hero-path rewrite (B6) | Yes, with caveat | touches 4 `src/` files — verified pure docstring fixes |
| a580f8f | fix(cli): cwd on sys.path (B7) | Yes | none |
| 66c84ec | docs(plan): record B7 | Yes | none |
| 06eb00c | docs: Phase 3 report | Yes | none |
| dad6d0d | docs(plan): B5 fork addendum (Phase 4 R1) | Yes | orchestrator-authored, documented above |

No revert/re-apply pairs found anywhere. All packaging/CI/publish-config changes traced
line-by-line to a named work item with zero unexplained lines. (Subsequent Phase 4 work —
R2, R4, R5, R7, and the R6/R3 PLAN.md entries — added 8 further commits after R1's own
audit ran; each was independently reviewed by that work item's own required verifier pass,
documented below.)

---

## R2 — Pairing-key correctness (blocking) — commit `9871292`

**The most consequential finding of this phase.** B4's (Phase 3) `--mode paired` regression
gate was **completely unreachable for the documented `adk eval` CLI workflow**, for two
independent reasons:

1. `session_id` is only stable across repeated runs if explicitly authored in the evalset
   JSON — otherwise ADK's `LocalEvalService` generates a fresh `uuid.uuid4()` every run
   (`local_eval_service.py:510-522`, `:67-68`).
2. The callback B4 used to capture `session_id` (`before_run_callback`) **never fires at all**
   during a real `adk eval` CLI run, which constructs a bare `Runner` with no `App`/`Plugin`
   wiring.

Paired mode had only ever been validated against a hand-rolled harness, never against the
primary documented path. Fixed by re-keying to `eval_case_id` — confirmed stable, read
directly from the evalset JSON file, never regenerated (`eval_case.py:150`) — recovered by
joining ADK's persisted `.evalset_result.json` against session_id now correctly captured via
`after_model_callback` (proven to fire through `adk eval`), with an explicit fallback chain
(`eval_case_id` → `session_id` → two-sample), the resolved key always printed, never silent.
Snapshot schema bumped 1→2, old files still readable. Proven end-to-end against a real `adk
eval` CLI run executed twice on the same 32-case evalset (not a hand-rolled harness):
`key=eval_case_id, 32 overlapping`, correct detection of a real injected regression.

**Tests: 294 → 320, 99% coverage.**

### R2.5 — detection rates (required independent re-verification)

| Measurement | Result |
|---|---|
| eval_case_id-keyed paired, n=25, case-correlated +$0.001/case regression | 200/200 = 1.000 |
| session_id-keyed paired (fallback path), same conditions | 200/200 = 1.000 |
| Independent verifier's own live end-to-end proof re-run | 32/32 eval_case_ids matched, real regression detected, exit code 1 |
| Independent verifier's own direct-construction statistical check (bypassing CLI/ADK layer) | Reproduced the same 1.000 detection rate |

Both the primary key (`eval_case_id`) and the fallback key (`session_id`, for hand-rolled
harnesses) independently reproduce B4's original headline mechanism — the fix corrects
*which* key is used and *how* it's captured, not the underlying statistical method. **All 5
independent-verification items: CONFIRMED, zero contradictions.**

---

## R3 — adk-docs PR rewrite (blocking for release, not for push)

Rewrote `docs/integrations/adk-tracegauge.md` on the existing `docs/adk-tracegauge-integration`
branch in `oss-contrib/adk-docs` (PR #2128) — commit `bec0f44` in that repo, **not pushed, PR
untouched**. Removed the pre-W2 "cannot surface this metric's output" blanket-broken framing
and all hand-rolled-harness content. New hero section leads with `tracegauge check`; a new
"Paired mode" section documents `--mode paired` keyed on `eval_case_id` (correctly, per R2 —
not the originally-shipped, broken `session_id` key); the `adk eval` metric moved to a labeled
secondary section; a "Known ADK-side limitations" section states both residual ADK bugs
accurately, with "a fix has been prepared and is pending submission upstream" phrasing (no PR
link — neither is open yet).

Every code block independently re-verified against a freshly-built wheel installed outside
both repos, per this phase's own R7 standard. **One real bug found and fixed in the process**:
the first-drafted paired-mode example (two separate shell `adk eval` invocations) doesn't
work — `TraceGaugeUsagePlugin`'s captured usage lives only in an in-process `UsageStore` that
does not survive a shell subprocess exiting. Corrected to an in-process `CliRunner`-based
entrypoint script, re-verified against the exact block now in the doc (real exit 1, 32/32
matched, `+33.93%`).

**Blocking sequencing constraint, stated explicitly (see ROUTE-TO-GG)**: this docs PR must not
merge before adk-tracegauge 0.3.0 — carrying every API this page now documents — is live on
PyPI. PyPI currently still serves 0.2.0, which has none of this.

---

## R4 — Power-aware gate — commit `ba6d21b`

Every `tracegauge check` run now prints a real, computed **minimum reliably-detectable effect
size at 80% power**, derived from the run's own observed sample variance and actual n (a
stated normal-approximation, since bootstrap power has no closed form — validated against
B4/R2's empirically-measured grid at 7 points, accurate within 2–8 percentage points, worst at
n=25). A new warning fires automatically when the user's configured practical-significance
floor is below this run's achieved detectable floor — the gate now says plainly when it's
claiming sensitivity it doesn't have, using real numbers from the actual run.

**`min_n` kept at 30`,** with real reasoning: measured detection at n∈{30,35,40,45} for a 10%
effect (71.5%/79.0%/77.5%/83.0%) shows no single `min_n` value generalizes — B4's own grid
shows even n=100 only reaches 64.5% for a 5% effect. Raising the floor would refuse real
30–44-invocation eval sets for a false sense of a fixed problem; 4.1/4.2's runtime honesty is
the general fix instead.

**BCa bootstrap was actually implemented and empirically measured** (not just reasoned about):
no meaningful FPR improvement over the shipped percentile method at n=10 (6.00% vs. 5.33%) or
n=25 (3.00% vs. 3.33%) — correctly **not shipped**, documented as a real, honest, unresolved
limitation rather than added for the appearance of rigor.

**Tests: 320 → 348, 99% coverage, `_regression.py` itself 100%.**

### R4.4 — FPR at n=30, shipped defaults (required independent re-verification)

| Run | Trials | Result |
|---|---|---|
| Original measurement (seed 500000) | 500 | 23/500 = 4.60% |
| Original independent re-check (seed 777777) | 500 | 21/500 = 4.20% |
| **Verifier's own independent third run (seed 333333)** | **500** | **15/500 = 3.00%** |
| **Combined, all three independent seeds** | **1,500** | **59/1,500 ≈ 3.93%** |

**All three independent measurements agree the real false-positive rate at the shipped default
configuration (n=30, confidence=0.95, default 5%-relative practical floor) sits well above the
2.5% nominal one-sided expectation — roughly 3–5%, not 2.5%.** Mechanism independently
confirmed: 100% of the verifier's own sampled false positives cleared both significance floors
because the default practical floor (~$0.0001 / 5% relative) sits only ~1.3 standard errors
from zero at this n/variance, so it doesn't meaningfully suppress sampling noise. This is the
number that goes in the README, stated plainly rather than the nominal-but-untrue 2.5%.

---

## R5 — Dependency contract — commit `b67526d`

Read `tracegauge`'s actual installed source in full and found 6 undocumented/reverse-engineered
assumptions this package relied on, most consequentially: **a load-bearing licensing claim in
the README ("this dependency's dual license is what lets us stay Apache-2.0") had never
actually been checked against the installed package, and was version-dependent** — the
dual-license SPDX header is present in `tracegauge==0.10.1` but **absent** in `0.10.0`, both
admitted by the old pin. Also found: a dead-code fallback branch in `tracegauge`'s own model
resolver, provably never exercised by any real call; several fields carried through the
integration that adk-tracegauge never reads.

Resolved by porting the actual dollar-arithmetic (39+59 lines, diffed byte-identical against
the installed source) fully in-house into a new `_cost.py`, with proper Apache-2.0 attribution,
and **removing the `tracegauge` PyPI dependency entirely** — eliminating the version-dependent
licensing risk and the undocumented-shape dependency going forward. 8 contract tests added
asserting `tracegauge`'s shape directly, each with a custom actionable failure message, verified
against both `tracegauge` versions admitted by the old pin (0.10.0 and 0.10.1 both pass;
0.10.0's absent SPDX header is exactly the finding this work item exists to catch).

**Tests: 348 → 357, 99% coverage, `_cost.py` itself 100%.**

---

## R6 — Upstream PRs re-verified — no adk-tracegauge changes (separate repo)

Re-fetched `upstream/main` fresh (3 new commits since Phase 3), ran ADK's full suite on an
isolated clean worktree: 33 real, platform-specific pre-existing failures (Windows
path-separator/mock-timing artifacts), none touching either target file. Independently
re-derived — not trusted from the prior report, which was re-read and found not to actually
state the "20 pre-existing failures" figure this phase's kickoff assumed, a premise correction
handled without derailing the actual verification — that both branches' fixes still fail
pre-fix and pass post-fix via a live source-only revert/restore on each, and that neither
branch introduces any failure beyond the clean 33-failure baseline. Fresh existing-issue/PR
search found nothing new landed upstream. No changes needed on either branch. **Verdict: both
PRs remain genuinely ready to offer**, still prepared, still not pushed, still not opened.

---

## R7 — Fresh-wheel testing as the standard — commits `54e3460`/`727f44b`/`006cc26`

Applied the pattern that caught Phase 3's B7 bug to everything: all 4 examples, both eval
paths (`adk eval` metric and `tracegauge check`), `--mode paired`, and every runnable code
block in README/docs, all run from a genuinely fresh wheel-only install outside the repo.
**Found and fixed one more real bug**: `docs/troubleshooting.md`'s entry 1 (wrong google-adk
version) reproduced one import-frame earlier than documented from a genuinely clean install —
`ModuleNotFoundError: No module named 'deprecated'`, an undeclared-dependency packaging bug in
`google-adk==1.0.0` itself, invisible in Phase 2's original capture because that venv already
had `deprecated` present transitively from something else already installed. Doc corrected.

Added a permanent `wheel-smoke-test` CI job (independent of `lint-and-test`): builds the wheel,
installs only the wheel into a fresh venv under `runner.temp`, runs the hero path plus one
example from an unrelated workdir, asserting the correct exit codes — so this exact class of
bug cannot silently recur.

**Tests: 357 (unchanged — docs/CI-only work item), 99% coverage.**

### R7.1 — required independent re-verification

A separate verifier agent independently built its own fresh wheel, its own fresh venv, and
reproduced every one of: all 4 examples, both eval paths, hero path, paired mode, the README
Quickstart, and the corrected troubleshooting entry 1 — from scratch, with no reuse of the
implementing agent's artifacts. **All 13 major categories CONFIRMED, zero contradictions,
zero new bugs found.** This is the third and, so far, final "fresh wheel install from outside
the repo" pass this build has run — the first two both found real, previously-invisible bugs;
this one, run after R7's own fixes, found none.

---

## Before/after summary (Phase 3 end-state → Phase 4 end-state)

| Metric | Phase 3 | Phase 4 |
|---|---|---|
| Tests | 294 | **357** |
| `--mode paired` reachability | Broken for the primary `adk eval` CLI path (unreachable) | **Fixed** — works end-to-end against real `adk eval` |
| Regression-gate honesty | Static docs caveat only | **Runtime achieved-power + below-floor warning, every run** |
| FPR at shipped n=30 defaults | Unmeasured at these exact settings | **Measured 3× independently: ~3–5%, not the nominal 2.5%** |
| `tracegauge` dependency | External, undocumented internal shape, version-dependent licensing risk | **Removed — arithmetic ported in-house, licensing risk eliminated** |
| Upstream PR readiness | Prepared once, not re-checked | **Independently re-verified clean against fresh upstream/main** |
| Fresh-wheel testing | One-off (B7) | **Standard pattern, permanent CI job, applied to every runnable artifact including a second repo's docs PR** |
| adk-docs PR (#2128) | Pre-W2 broken framing, never updated | **Rewritten for the corrected API, sequencing constraint made explicit** |
| Process integrity | One coordination incident (B5), causal record incomplete | **Independently audited, root cause now durably recorded** |

---

## ROUTE-TO-GG list

1. **Review and push this branch**: `git push -u origin feat/cost-regression-gate`. Success signal: branch appears on GitHub through the final Phase 4 commit.
2. **Confirm the 4-version CI matrix AND the new `wheel-smoke-test` job are both green on GitHub's real runners** (only locally verified across Phases 2–4).
3. **Trigger `pypi-canary.yml`**: `gh workflow run pypi-canary.yml --repo gaurav-gandhi-2411/adk-tracegauge` (needs a pushed ref).
4. **Upstream PR #1** (threshold directionality, re-confirmed ready by R6):
   ```
   cd C:\Users\gaura\ml-projects\oss-contrib\adk-python
   git push -u origin fix/cost-metric-threshold-directionality
   gh pr create --repo google/adk-python --base main \
     --head gaurav-gandhi-2411:fix/cost-metric-threshold-directionality \
     --title "fix(evaluation): honor each metric's own eval_status in AgentEvaluator.evaluate()" \
     --body-file <path — full body in R6's session report / PLAN.md Phase 4 R6 entry>
   ```
5. **Upstream PR #2** (adk eval exit code, re-confirmed ready by R6):
   ```
   cd C:\Users\gaura\ml-projects\oss-contrib\adk-python
   git push -u origin fix/adk-eval-exit-code
   gh pr create --repo google/adk-python --base main \
     --head gaurav-gandhi-2411:fix/adk-eval-exit-code \
     --title "fix(cli): adk eval process exit code now reflects PASSED/FAILED" \
     --body-file <path — full body in R6's session report / PLAN.md Phase 4 R6 entry>
   ```
6. **adk-docs PR #2128 update — blocked on item 8 landing first, do not push yet**:
   ```
   cd C:\Users\gaura\ml-projects\oss-contrib\adk-docs
   git push origin docs/adk-tracegauge-integration   # updates PR #2128 automatically
   ```
   **Explicit sequencing constraint**: do NOT push this until adk-tracegauge 0.3.0 is live on
   PyPI (item 8) — the rewritten page documents commands and behavior (`tracegauge check
   --mode paired`, the required-threshold constructor, no external `tracegauge` dependency)
   that do not exist in the currently-published 0.2.0. Pushing early would put live-but-wrong
   documentation in front of real users.
7. **Optional remote branch cleanup** (carried from Phase 2, still outstanding): `git push origin --delete chore/0.1.0-release chore/0.2.0-release chore/rc1-version-bump ci/pypi-trusted-publishing docs/releasing`.
8. **Version bump / PR / publish sequencing, once ready to ship**: (a) push this branch, confirm CI green including the new wheel-smoke-test job; (b) trigger the canary; (c) bump `pyproject.toml`'s version to `0.3.0` per `CHANGELOG.md`'s own proposed entry; (d) move `[Unreleased]` to a dated `[0.3.0]` entry; (e) open a PR from this branch into `main` — **human merge required**, this branch is far over the ~400-reviewable-line auto-merge ceiling across 32+ commits; (f) merge; (g) tag `v0.3.0`, triggering `release.yml` (build, twine check, OIDC publish, GitHub Release); (h) confirm the PyPI listing; (i) **only then** push the adk-docs branch (item 6).

No other outstanding TODOs found beyond what's listed above, cross-checked against every "TODO"/"deferred" mention across all four phase reports and `PLAN.md`.
