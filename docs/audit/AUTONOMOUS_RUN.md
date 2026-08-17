# Autonomous run — Q1/Q2 completed, halted at STOP condition S2

**Status: HALTED after Q2, before Q3.** Per the run's own STOP-condition
protocol ("halt and report immediately"), this report is delivered now,
not at the end of the full Q1-Q7 queue. Q3-Q7 were not started.

## Rule conflict, resolved before any action — stated up front

The turn's opening sentence said "merge whatever's queued once GitHub
recovers." The STANDING RULES block in the same message says "No
self-merges, ever. PRs queue for GG; move to the next item rather than
waiting." These directly conflict. I treated the STANDING RULES block as
controlling: it is explicit, detailed, says "ever," and matches this
engagement's own standing global policy (which names these two repos
specifically and requires GG merge with no self-granted exceptions,
motivated by a prior incident where "trivial, all gates passed" reasoning
led to an unauthorized self-merge). **Nothing was merged. Every PR is
open, draft, and queued for GG** — see ROUTE TO GG below.

---

## Q1 — within-case CV: DONE, real measurement, triggered the STOP

**What was done**: `scripts/measure_within_case_cv_ollama.py` ran the
identical 36-case evalset (`reports/ad2_evalset.json`) TWICE against local
Ollama (`qwen2.5:7b`), matched per-case by `case_id`, and computed the
pooled within-case CV via the standard duplicate-measurement estimator.

**VERIFIED evidence**:
- Bias check: mean(delta) = -$0.000001, t-stat = -0.189 (not significant —
  the two runs are exchangeable, validating the pooled-variance estimator).
- **Within-case CV = 0.1566** (`reports/q1_within_case_cv.json`).
- `scripts/measure_q1_within_case_power.py` located this DIRECTLY on the
  paired-mode grid at the real n values — not interpolated:

  | n | effect | power [Wilson 95% CI] |
  |---|---|---|
  | 30 | 10% | **28.45% [26.52%, 30.47%]** (569/2,000) |
  | 36 | 10% | **32.25% [30.24%, 34.33%]** (645/2,000) |
  | 30 | 25% | 92.25% [91.00%, 93.34%] (1,845/2,000) |
  | 36 | 25% | 96.35% [95.44%, 97.09%] (1,927/2,000) |

  (`reports/q1_within_case_power.json`; n_boot=1,000 validated against
  10,000 at 98% agreement before trusting it.)

**Sanity check performed before trusting the result** (per the standing
"audit the measurement" rule): the measured point (CV=0.1566 → 28.45%
power at n=30) sits between AD1's own grid points CV=0.1 (58.35%) and
CV=0.2 (18.55%) — consistent with a monotonically decreasing, steep power
curve, not an outlier.

**Q1.4 (sampling settings, VERIFIED)**: `LlmAgent.generate_content_config`
defaults to `None` (confirmed by reading the field default) and `ollama
show qwen2.5:7b --modelfile` has zero `PARAMETER` lines (confirmed live)
— Ollama's own non-zero server default (temperature=0.8) applied. This is
NOT a deterministic/greedy decode, so this measurement does not have the
variance-understating failure mode Q1.4 warned about. It may still not
match a real hosted model's own default sampling config — a separate,
already-flagged (AD2.3) UNVERIFIED representativeness question.

**Q1.5**: README updated (`docs/audit/Q1_WITHIN_CASE_CV.md` created, new
paragraph added to the "Power depends on your own cost variance" section
stating the measured CV, the resulting power, and domain of validity).
**adk-docs page NOT updated for Q1** — deliberately, per Q4.4's "do not
push the adk-docs branch until the version is live on PyPI," which this
run is honoring even though Q4 itself was not reached.

**Q1.6, the STOP trigger**: at the shipped `min_n=30`, paired mode — the
DEFAULT mode whenever a pairing key resolves — detects a true 10% cost
regression only 28.45% of the time at this evalset's real, measured
within-case CV; 32.25% at the actual evalset size (n=36). Both are ~1/3 of
the previously published, unqualified "99.22% at n=30" figure that is
**still live on the published README right now** (the fix is in unmerged
PR #23). This is **S2** (measurement contradicts a published claim), not
merely "STOP-worthy" as a category — a real, currently-true contradiction
between what ships and what the gate's own default actually does.

**Committed**: branch `docs/ad1-ad2-cv-reframe`, commit `fe81f56`, pushed.
Same branch as AD1/AD2 (already PR #23) — same coherent "reframe the power
claim with real data" story, not a separate PR.

---

## Q2 — two-sample low-power warn/refuse: DONE

**2.3, recommendation and justification**: a distinct, non-zero exit code
(not just a louder warning), because the existing `power_warning` text
line was already present and already fires on every affected run — the
actual gap Q2.1 identified is that a CI job checking only `$? == 0` never
sees it. Text alone doesn't fix that; only the exit code does. **Refusal**
(withholding a verdict, like `insufficient_data`) was considered and
rejected: unlike `insufficient_data`, a real, statistically valid
bootstrap CI WAS computed here — what's misleading is only the false
confidence an exit-0 read implies, not the verdict's validity. So: warn
loudly AND use a distinct exit code, not refuse.

**Implementation** (`src/adk_tracegauge/_regression.py`,
`src/adk_tracegauge/_cli.py`):
- `RegressionCheckResult.underpowered_pass` (new property): `True` when
  `status="pass"`, `method="two_sample"`, and `power_warning` fired.
  Deliberately excludes paired mode (Q2's own scope) even though Q1 found
  paired can also be underpowered — flagged as a natural follow-up, not
  silently bundled in.
- `EXIT_UNDERPOWERED_PASS = 4` — distinct from 0/1/3 and from argparse's
  reserved 2.
- `report()` gains a loud, bannered closing section naming the exit code
  and the fix, when `underpowered_pass` is true.
- Module docstring's exit-code table and `__all__` updated.

**2.4, VERIFIED**: confirmed via a real end-to-end CLI test
(`test_cmd_check_end_to_end_underpowered_pass_two_sample`) that the
condition fires and is visible both in the exit code and in `stdout`
("UNDERPOWERED PASS" / "exit code 4" both asserted present).

**2.5, tests**: 4 new unit tests (fires for two-sample/pass; does NOT fire
for regression status, for paired mode, or for a genuinely low-variance
pass under the default floor) + 2 new CLI end-to-end tests (real-variance
underpowered → exit 4; low-variance default-floor pass → exit 0,
regression guard). **Two of these tests initially failed** on first run —
not a code bug, a test-fixture bug: my first "generous floor" fixture used
CV=60%/CV≈2%, both still too high to clear the DEFAULT $0.0001 absolute
floor (tighter than the 5% relative floor at this mean). Fixed by using a
genuinely low-CV (~0.5%) fixture. Full suite: **436 passed, 0 failed**
(re-verified after the fixture fix). `ruff check` clean.

**Committed**: new branch `feat/two-sample-underpowered-exit-code`, commit
`045f4ef`, pushed, PR #24 opened as draft.

---

## Q3-Q7: NOT DONE — halted by the STOP condition

No tracegauge release, no adk-tracegauge release, no 0.12.1, no dashboard
design, no branch cleanup, no docs-consolidation PR were started. This is
not an oversight — it is the STOP protocol working as specified. Q3
(tracegauge 0.12.0) was technically unblocked (PR #24 on
token-efficiency-scorer had already merged, confirmed via `gh pr view 24
--json state,mergedAt`: `MERGED`, `2026-08-17T17:55:34Z`, by GG, not by
this session) — but a release is exactly the kind of action a STOP
condition exists to gate, so it was not attempted.

---

## GitHub outage note

`gh` returned HTTP 503 on every call for several minutes at the start of
this turn (a GitHub-side GraphQL outage, not local — confirmed via `gh api
rate_limit` before and after). Recovered on its own; PR #23 (queued from
the prior turn) was created once it did. No workaround was needed beyond
waiting and retrying, per the standing "push the branch and continue"
rule — both branches were already safely pushed via git (which doesn't
depend on the GraphQL API) before the outage was even noticed.

---

## ROUTE TO GG

**R1 — paid Gemini validation run** (unchanged from the prior turn's
report, restated for completeness): confirming whether Ollama's local
7B-model variance is representative of a real hosted model's would need a
small paid run. Exact steps and cost estimate in
`docs/audit/AD2_REAL_CV_MEASUREMENT.md`, section 2.3: swap
`LiteLlm(model="ollama_chat/qwen2.5:7b")` for `model="gemini-2.5-flash-lite"`,
same 36-case evalset, no synthetic price table needed. **Estimated cost:
≈$0.0065 total** (mean 107.8 input + 423.2 output tokens × 36 cases, real
published rate), a few cents even with a 3-5x safety margin. Requires a
real `GOOGLE_API_KEY`, which this environment does not have and this
session did not acquire. Not run.

**R2 — every PR awaiting merge**:

| Repo | PR | Title |
|---|---|---|
| adk-tracegauge | [#23](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/23) | docs: AD1/AD2 + Q1 — reframe power claim as CV-dependent, measure real (across- and within-case) CV |
| adk-tracegauge | [#24](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/24) | feat(cli): Q2 — distinct exit code for an underpowered two-sample pass |
| adk-docs (fork) | [#2128](https://github.com/google/adk-docs/pull/2128) (upstream) | docs(integrations): add tracegauge Cost Evaluator for ADK agents — pre-existing, open, unrelated to today's push per Q4.4 (not updated with Q1's finding yet, by design) |
| adk-python (fork) | [#6739](https://github.com/google/adk-python/pull/6739) | fix(evaluation): honor each metric's own eval_status in AgentEvaluator.evaluate() — pre-existing, open, untouched this session |
| adk-python (fork) | [#6740](https://github.com/google/adk-python/pull/6740) | fix(cli): adk eval process exit code now reflects PASSED/FAILED — pre-existing, open, untouched this session |
| token-efficiency-scorer | none open | PR #24 (`tes impact`) already merged by GG before this turn started |

**R3 — venv/path the sandbox blocks from deletion**: `C:\tmp_ac1_venv`
(created in the immediately-prior turn, outside the scratchpad directory —
a mistake at the time, already flagged once). Still present, still
blocked (`rm -rf` and `Remove-Item -Force` both denied as a "protected
system path" by the sandbox). No new blocked paths this session — the
venv used here (`.../scratchpad/ad1_venv`) is correctly inside the
scratchpad directory and is session-managed, not something requiring
manual cleanup.

---

## What would come next (not started, for GG's sequencing, not a
## recommendation to proceed autonomously)

Q3 (tracegauge 0.12.0) has no dependency on Q1/Q2's finding and could
proceed independently once reviewed. Q4 (adk-tracegauge minor release)
depends on PR #23 and #24 both merging first. Q5-Q7 are independent of
the STOP finding and could run in any order once resumed.
