# adk-tracegauge — Phase 6 Report: two release trains, closed out

Branch: `feat/cost-regression-gate` (adk-tracegauge); `fix/0.10.2-pricing-defects`
(token-efficiency-scorer); `docs/adk-tracegauge-integration` (adk-docs). None pushed, none
tagged, none published, none merged. Audit date basis: 2026-08-16. Executes three decisions
locked in at kickoff (D1: ship 0.3.0 with the current in-house engine; D2: Option C accepted,
execution deferred to Phase 7; D3: `tracegauge` 0.10.2 is top priority) — not re-litigated.

---

## T1 — `tracegauge` 0.10.2 (urgent, done first)

Fixed in `C:\Users\gaura\ml-projects\token-efficiency-scorer`, branch
`fix/0.10.2-pricing-defects` @ `e67fe91`. **Root cause**: `_resolve_model` fell through to
`prices["default_model"]` ("claude-sonnet-4-6", $3/$15/Mtok) for any unmatched model string,
with only a buried `is_approximate` flag — `total_usd` stayed a confident-looking wrong
number. **Fixed to fail closed**, mirroring adk-tracegauge's own B1 pattern: `_resolve_model`
now returns `None` (never a guessed key); `compute_turn_cost`/`compute_session_cost` return
an explicit unpriced result naming the model and the remedy, never a wrong dollar figure.

### T1.7 — dollar magnitude, realistic single call (10,000 input / 2,000 output tokens, no cache)

| Model | Pre-fix charge | Post-fix charge | Difference |
|---|---|---|---|
| `claude-sonnet-5` | $0.06 (wrong default rate) | $0.04 | **−$0.02 (a 50% overcharge eliminated)** |
| `claude-opus-5` | $0.06 (wrong default rate) | $0.10 | **+$0.04 (was only 60% of true cost)** |
| Server-tool usage (20 web searches, per the S1 example) | $0.20 silently dropped | Detected, explicitly flagged `[NOT PRICED: ...]`, still not priced (out of this fix's scope, honestly disclosed) | — |

Added the actually-missing current models (`claude-opus-5`, `claude-sonnet-5`) plus two more
found missing (`claude-fable-5`, `claude-mythos-5`), all with real fetched `as_of`/
`source_url`. Ported a minimum-viable staleness guard (90-day threshold + CI job) — explicitly
not the full engine move, that's Phase 7. Downstream-dependent check: a GitHub-wide code
search for direct imports of the affected functions found zero public consumers outside this
repo and adk-tracegauge (which no longer depends on it). Version bumped to `0.10.2`, CHANGELOG
entry added, framed honestly as a bug-fix release correcting real mispricing. **Not published
— prepared, committed locally, awaiting human decision.**

**T1.2/T1.6 independently re-verified**: a separate verifier's own 12-model adversarial sweep
(different strings than the committed tests) plus a hunt across `compute_session_cost`/
`cli.py`/`watcher.py` for any remaining fallback path found none — CONFIRMED, zero
contradictions on the substantive fix.

---

## T2 — verifier-dispute re-adjudication

**Standing rule established this phase: a verifier contradiction gets a blind third party;
the orchestrator does not rule on its own work.** A fresh agent, given only the disputed test
file and both implementations — no exposure to either side's prior argument — independently
answered two separate questions:

**(a) Does the injected-dict harness prove the ported arithmetic is equivalent? YES.**
Independently traced the same mechanism the orchestrator found: a synthetic per-case `prices`
dict, not either package's own bundled table, fed identically to both implementations —
confirmed by `claude-opus-4-8` (present in tracegauge's table) and `claude-opus-5` (absent
from it) producing byte-identical results at the same injected rate, which would be
impossible under a bundled-table-lookup reading. One new, smaller gap flagged: the frozen
test checks model-*key* coverage but doesn't re-verify frozen *rates* stay current if a rate
changes later — a data-staleness risk in the test itself, not a methodology flaw, not fixed
this phase (out of T2's scope).

**(b) Do the two packages' bundled price tables actually agree today? NO.** Independently
re-confirmed the same divergence Phase 5's S1/S3 already found (4 shared models, all
matching; 18 models only in adk-tracegauge; 16 only in tracegauge; differing cache-write
multipliers by design) — a restatement from a blind source, not a new finding, not a
contradiction of anything.

**Outcome: the original Phase 5 S5.3 claim is independently re-confirmed by a genuinely blind
third party.** The earlier verifier's "CONTRADICTED" is now settled as a methodology misread,
via two independent routes (the orchestrator's own source read, and this blind adjudication).

---

## T3 — console script collision

`adk-tracegauge`'s console script renamed `tracegauge` → `adk-tracegauge`; `tracegauge` (the
sibling package) keeps its own name, untouched. Caught two of its own mistakes before
committing: a second hardcoded self-reference in `_regression.py`'s report output that a
narrower fix would have missed, and 3 accidentally-inverted sibling-package statements
introduced by a bulk regex pass, found by re-reading the full diff before committing.

**T3.3 independently re-verified**: two fresh venvs, both install orders, confirmed
`adk-tracegauge --help` and `tracegauge --help` each resolve to the correct package
regardless of order — CONFIRMED, no collision. The verifier also found one real remaining
gap (`_compat.py:219`, an error-message string still saying `tracegauge check` instead of
`adk-tracegauge check`) — fixed directly by the orchestrator, re-verified via a full-repo
sweep confirming no other bare reference remains outside the intentionally-unmodified
historical audit reports.

**CHANGELOG framing**: confirmed via git history that the console script was added in Phase 2
W4, entirely after 0.2.0 published — it never shipped under any name to a real user, so this
is documented as "new in 0.3.0," not a breaking rename.

---

## T4 — power at the shipped minimum n

### T4.1 — power at n=30, confidence=0.98

| True effect | Grid-sourced (Phase 5 S4) | Fresh re-measurement | Independent verifier's own re-measurement |
|---|---|---|---|
| 5% | 16.2% | not re-measured | — |
| 10% | 58.4% | 57.2% / 56.6% (two runs) | **56.2%** |
| 25% | 100% | not re-measured | — |
| 50% | 100% | 100.0% | **100.0%** |

### T4.2 — `min_n` kept at 30

Re-measured n ∈ {30, 35, 40, 45, 50} at confidence=0.98/10%-effect, multiple independent
seeds: **n=50's earlier "clears 80%" reading proved to be noise, not a robust result** —
three independent measurements landed at 79.6%, 81.0%, and (the independent verifier's own
check) 80.6%, averaging ~80.4% — a coin flip around the threshold, not a reliable win.
Raising `min_n` to 50 would only guarantee refusing every real 30–49-invocation eval set
without reliably buying 80% power in return. **Kept `min_n=30`**; the existing Phase 4 R4
achieved-power/minimum-detectable-effect runtime reporting is the correct general fix —
consistent with this project's established honesty-over-usability pattern.

### T4.3 — README

States FPR (2.3%) and power (58.4% for a 10% effect) at the same n=30/confidence=0.98
configuration together, plus the full min_n re-validation table, so no stale
confidence=0.95-era figures are left as the last word.

**Independently re-verified**: all 5 items (both power numbers, the marginal-n=50 claim,
both shipped constants, the test count) CONFIRMED by a separate verifier with its own fresh
seed, including specifically substantiating the "n=50 is marginal" reasoning that drove the
decision not to raise `min_n`.

---

## T5 — 0.3.0 release packet (final work item, closes the build)

Version bumped to `0.3.0`. Cross-checked the CHANGELOG's `[Unreleased]` section against
`git log main..HEAD` (54 commits) and all 5 phase reports — found it was **not** a complete
summary; 6 real, shipped capabilities had zero mention (the B1 opt-in requirement, B2's
promo auto-expiry, `price-freshness.yml`, the `--mode` paired/two-sample/auto CLI feature,
the achieved-power/MDE runtime reporting, the wheel-smoke-test CI job) — all added.

**Full 4-version suite, live google-adk 2.7.0**: 365/365 passing on Python
3.10.20/3.11.15/3.12.12/3.13.5, identical coverage on all 4, zero code changes required.

**Fresh-wheel pass**: the second clean pass in this project's history (after Phase 5 S5) —
zero discrepancies, confirmed only `adk-tracegauge.exe` installs (no stray `tracegauge`).

**Package inspection**: `gemini_prices.json` genuinely packaged in both wheel and sdist,
`entry_points.txt` correctly shows only `adk-tracegauge`, `twine check` passed both.

**adk-docs consistency check found and fixed one real staleness bug**: the paired-mode
captured output block still showed `95% CI` from before Phase 5 S4's confidence retune to
0.98 — never re-captured after that change landed. Fixed and re-verified live against the
fresh 0.3.0 wheel.

**Ordering dependency between the two release trains: none.** Re-verified (not assumed) that
`adk-tracegauge` has zero dependency on `tracegauge` (Phase 4 R5) — confirmed by every
scratch venv this session running clean with `tracegauge` absent. The two trains can ship in
either order.

Final verification: 365/365 passing, 99% coverage, ruff/mypy clean, both repos' working
trees confirmed clean.

---

## Before/after summary (Phase 5 end-state → Phase 6 end-state)

| Metric | Phase 5 | Phase 6 |
|---|---|---|
| Tests (adk-tracegauge) | 363 | **365** |
| `tracegauge` live-pricing state | Confirmed defective, unfixed | **Fixed, prepared as 0.10.2, not yet published** |
| Console script collision | Discovered | **Resolved — `adk-tracegauge` vs. `tracegauge`, verified both install orders** |
| Regression-gate power honesty | Runtime reporting existed | **Re-validated the min_n decision with fresh multi-seed measurement, confirmed noise not signal at n=50** |
| S5.3 dispute status | Orchestrator-resolved, unconfirmed by a third party | **Independently re-confirmed by a genuinely blind adjudicator** |
| adk-tracegauge version | 0.2.0 (published), 0.3.0 pending | **0.3.0 prepared, fully verified, not yet published** |
| adk-docs PR staleness | Unknown | **Checked, one real gap found and fixed (stale CI figure)** |

---

## T1.7 dollar table

See the T1 section above — reproduced here per the explicit output requirement: at a
realistic 10,000-input/2,000-output-token call, `tracegauge`'s pre-fix code overcharged
Sonnet-5 by 50% ($0.06 vs. correct $0.04) and undercharged Opus-5 to 60% of true cost ($0.06
vs. correct $0.10); both are now correct. Server-tool usage (20 web searches) that was
silently dropping $0.20 now surfaces an explicit `[NOT PRICED: ...]` warning instead.

## T2 adjudication result

See the T2 section above: **(a) arithmetic-equivalence harness — proven valid, YES; (b)
bundled price tables agree — NO, confirmed divergent by design.** Independently re-confirms
Phase 5's S5.3 claim; the standing dispute is settled.

## T4.1 power numbers

See the T4 section above: at the shipped default (confidence=0.98, n=30) — 16.2% power for a
5% true regression, ~57% for a 10% regression, 100% for 25%/50% regressions. `min_n` kept at
30 after confirming n=50's apparent improvement was statistical noise, not a robust gain.

---

## ROUTE-TO-GG list — two release trains

### Train 1: `tracegauge` 0.10.2 (in `token-efficiency-scorer`)

1. Review the branch: `cd C:\Users\gaura\ml-projects\token-efficiency-scorer && git log main..fix/0.10.2-pricing-defects`. Success signal: diff matches T1's summary above.
2. Push: `git push -u origin fix/0.10.2-pricing-defects`.
3. Open a PR (or push directly to a release branch per this repo's own workflow — check `RELEASING.md`) and merge.
4. Tag/release per `RELEASING.md`'s documented process — triggers the repo's own PyPI publish workflow.
5. Confirm live: `curl -s https://pypi.org/pypi/tracegauge/json | grep '"version"'` shows `0.10.2`.

### Train 2: `adk-tracegauge` 0.3.0 → `adk-docs` PR (no ordering dependency on Train 1 — verified, zero package dependency between them)

1. Review and push: `cd C:\Users\gaura\ml-projects\adk-tracegauge && git push -u origin feat/cost-regression-gate`.
2. Confirm the CI matrix and the wheel-smoke-test job are green on GitHub's real runners (only locally verified across all six phases).
3. Trigger the canary: `gh workflow run pypi-canary.yml --repo gaurav-gandhi-2411/adk-tracegauge`.
4. Upstream PR #1 (threshold directionality, `fix/cost-metric-threshold-directionality` @ `c2131b70` in `oss-contrib/adk-python`, re-confirmed ready by Phase 4's R6): push and `gh pr create` per the exact command in `docs/audit/PHASE4_REPORT.md`.
5. Upstream PR #2 (`adk eval` exit code, `fix/adk-eval-exit-code` @ `32c8991d`): same pattern.
6. Open a PR from `feat/cost-regression-gate` into `main` — **human merge required**, this branch is far over the ~400-reviewable-line auto-merge ceiling across 54+ commits.
7. Merge, then tag `v0.3.0` on `main` — triggers `release.yml` (build, twine check, OIDC publish, GitHub Release).
8. Confirm live: `curl -s https://pypi.org/pypi/adk-tracegauge/json | grep '"version"'` shows `0.3.0`.
9. **Only then**, push the adk-docs branch: `cd C:\Users\gaura\ml-projects\oss-contrib\adk-docs && git push origin docs/adk-tracegauge-integration` (updates PR #2128 automatically — the page documents 0.3.0-only behavior and must not go live before 0.3.0 does).
10. Optional: remote-delete the 5 already-merged branches carried from Phase 2 (local deletion already done).

No other outstanding TODOs found beyond what's listed, cross-checked against every phase report's own ROUTE-TO-GG list.
