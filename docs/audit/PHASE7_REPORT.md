# adk-tracegauge — Phase 7 Report: paired-by-default, resolved with rigor

Branch: `feat/cost-regression-gate`. Not pushed, not tagged, not merged, not published.
Audit date basis: 2026-08-16. Train 1 (`tracegauge` 0.10.2) is approved for push — GG
executes that separately. Train 2 (`adk-tracegauge` 0.3.0 → adk-docs PR) holds this phase.

---

## U1 — paired-by-default — commit `5061ed4`

Paired comparison is now the DEFAULT mode of `tracegauge check` whenever a pairing key
resolves with sufficient overlap (kept at the existing `--min-n=30` threshold, not lowered
— see reasoning below); two-sample is the automatic fallback otherwise. The resolved mode
and key print on every run, unconditionally. `--mode` still forces either explicitly;
forcing `paired` without a resolvable key fails loud with the real overlap count named,
never silently degrades.

**Measured overlap rate on the real 32-case evalset**: 100% (32/32), reconfirmed a second
time via the fresh-wheel proof in a separate process.

**Threshold decision**: kept identical to the existing `--min-n=30` rather than inventing a
separate paired-specific number — justified directly by the power-grid finding below (paired
mode's own FPR is not better than two-sample's at small n, so there was no statistical basis
for a lower bar).

**Paired-mode power grid** (n ∈ {10,25,50,100}, confidence=0.98, 1,000 trials/cell):
```
n\effect%    0%      5%     10%     25%     50%
10         0.041   0.255   0.763   1.000   1.000
25         0.024   0.498   0.978   1.000   1.000
50         0.016   0.764   1.000   1.000   1.000
100        0.012   0.978   1.000   1.000   1.000
```
Two-sample equivalent (Phase 5 S4, same n/confidence):
```
n\effect%    0%      5%     10%     25%     50%
10         0.022   0.084   0.276   0.888   1.000
25         0.012   0.142   0.514   1.000   1.000
50         0.016   0.248   0.834   1.000   1.000
100        0.004   0.484   0.990   1.000   1.000
```
**The counter-intuitive finding**: paired mode has a *higher* FPR than two-sample at every
shared n except n=50 (near-parity), despite being dramatically more powerful. Independently
re-verified twice — once by the dispatched verifier (same seed, confirming determinism) and
once by the orchestrator directly with a genuinely fresh, previously-unused seed base
(paired FPR 3.5%/1.3% vs. two-sample ~0% at n=10/25 respectively) — the direction holds under
both checks.

**End-to-end proof, fresh wheel, no `--mode` flag**: real output showed
`mode=paired (key=eval_case_id, 32 overlapping eval_case_ids matched)`, exit code 1 on a real
injected regression. **Tests: 365 → 374.**

---

## U2 — resolve the alpha decision, with confidence intervals — commit `fc564ee`

Full 36-cell grid (confidence ∈ {0.95, 0.98, 0.99} × n ∈ {30, 50} × effect ∈ {0%, 10%, 25%},
2,000 trials/cell, Wilson 95% CI on every proportion, both modes) — 72,000 total simulated
evaluations, 15.0 minutes real wall-clock.

### U2.1 — two-sample grid

| confidence | n | 0% (FPR) | 10% | 25% |
|---|---|---|---|---|
| 0.95 | 30 | 2.75% [2.12, 3.56]% | 72.05% [70.04, 73.97]% | 100.00% [99.81, 100]% |
| 0.95 | 50 | 3.00% [2.34, 3.84]% | 88.40% [86.92, 89.73]% | 100.00% [99.81, 100]% |
| 0.98 | 30 | 0.85% [0.53, 1.36]% | 57.80% [55.62, 59.95]% | 99.95% [99.72, 99.99]% |
| 0.98 | 50 | 1.20% [0.81, 1.78]% | 81.25% [79.48, 82.90]% | 100.00% [99.81, 100]% |
| 0.99 | 30 | 0.50% [0.27, 0.92]% | 49.10% [46.91, 51.29]% | 99.95% [99.72, 99.99]% |
| 0.99 | 50 | 0.65% [0.38, 1.11]% | 74.20% [72.24, 76.07]% | 100.00% [99.81, 100]% |

### U2.2 — paired grid (same cells, same trial count/CI method)

| confidence | n | 0% (FPR) | 10% | 25% |
|---|---|---|---|---|
| 0.95 | 30 | 2.55% [1.94, 3.34]% | 99.85% [99.56, 99.95]% | 100.00% [99.81, 100]% |
| 0.95 | 50 | 3.70% [2.96, 4.62]% | 100.00% [99.81, 100]% | 100.00% [99.81, 100]% |
| 0.98 | 30 | 1.40% [0.97, 2.02]% | 99.45% [99.02, 99.69]% | 100.00% [99.81, 100]% |
| 0.98 | 50 | 1.80% [1.30, 2.48]% | 100.00% [99.81, 100]% | 100.00% [99.81, 100]% |
| 0.99 | 30 | 0.90% [0.57, 1.42]% | 98.80% [98.22, 99.19]% | 100.00% [99.81, 100]% |
| 0.99 | 50 | 1.10% [0.73, 1.66]% | 100.00% [99.81, 100]% | 100.00% [99.81, 100]% |

### U2.3 — re-decision: `DEFAULT_CONFIDENCE` stays at 0.98 — answer unchanged, reasoning sharpened

Paired mode's power is already near-ceiling at 0.98 and barely moves at 0.99 (99.45%→98.80%
at n=30; 100%→100% at n=50) — no real headroom to gain by tightening. Two-sample's power
drops sharply over the same tightening (57.80%→49.10% at n=30; 81.25%→74.20% at n=50) and
crosses below an 80%-power bar at n=50/confidence=0.99. **One shared constant serves both
modes — tightening it would help the path (paired) that needs it least, at the direct expense
of the path (two-sample fallback) that needs it most.** A future paired-mode-specific tighter
default is a real option, noted as new scope, not implemented this phase.

**Independently re-verified**: all 6 checked claims CONFIRMED, including a manual,
independent re-derivation of the Wilson score interval formula itself (matched the
Brown/Cai/DasGupta 2001 textbook definition to 6+ decimal places on spot checks) — the
package's "honest uncertainty" positioning rests on this formula being correct, and it is.

**Tests: 374 → 382.**

---

## U3 — honest README numbers — commit `ebf8008` (adk-docs: `9ab70b16`)

New `## Shipped default, stated plainly` section states the default mode (paired, with the
automatic two-sample fallback described), and both modes' measured FPR and 10%-effect power
at the shipped configuration — every number carrying its trial count and Wilson CI (e.g.
"1.40% [0.97%, 2.02%] (28/2,000 trials)"). The two-sample fallback's numbers are stated
separately, explicitly labeled as "what you get when no pairing key resolves," never blended
with paired's numbers.

New `## What this gate can and cannot detect` section: large regressions (25%+) reliably
detected at any realistic n under either mode; moderate regressions (10%) near-ceiling under
paired, only moderate-to-poor under the two-sample fallback (57–81% depending on n); small
regressions (5%) not reliably detected at small n under either mode (two-sample: 16.20%
[13.23%, 19.69%] at n=30; paired: 49.80% [46.71%, 52.89%] at n=25 — still well under 80%).
Framed explicitly as the differentiator: no competitor found in this project's own Phase 1
research reports statistical power at all.

Full-file sweep confirmed zero bare percentages remain anywhere in the README. The adk-docs
PR's "Paired mode" section rewritten to reflect it's now the default (not opt-in via a flag),
re-verified against a fresh wheel from outside both repos — byte-identical to the committed
example.

**Tests: 382 (unchanged, docs-only item).**

---

## U4 — `tracegauge` 0.10.2 CHANGELOG — commit `db0e209` (in `token-efficiency-scorer`)

Scoped, docs-only (confirmed via `git diff --stat`: only `CHANGELOG.md`/`README.md`
touched). The `[0.10.2]` entry now leads with a `### BREAKING` callout naming the exact
change precisely, followed by a migration-path section describing the real field names
(`priced`, `approximate`, `approximate_reasons`, `approximate_turn_count`) a caller must now
check, confirmed by reading the actual current source rather than guessed. README gained a
matching top-of-file note. Nothing else in this train changed — approved for push as-is, per
instruction.

---

## U5 — final 0.3.0 re-verification — commit `657be0f`

**The first work item in the entire 7-phase build to find zero problems anywhere.** Given
every prior fresh-wheel pass before this one found at least one real bug, this was treated as
the claim most needing adversarial scrutiny, not the one to wave through — a dedicated
verifier independently rebuilt its own wheel, its own venv, and re-ran every example, the
auto-selected-paired hero path, both explicit `--mode` overrides, and 5 doc code blocks not
already quoted in the primary report. **Confirmed: zero code defects, zero documentation
defects, 100% byte-for-byte numerical accuracy.**

Full suite: 382/382 passing on all 4 Python versions (3.10.20/3.11.15/3.12.12/3.13.5) with
live google-adk 2.7.0 (confirmed still the current release). `gemini_prices.json` genuinely
packaged in both sdist and wheel; `twine check` passed both. Both prepared upstream
`google/adk-python` PRs re-confirmed still valid (9 commits behind a fresh `upstream/main`,
zero file overlap — not stale). The adk-docs PR (#2128) confirmed still open.

---

## Before/after summary (Phase 6 end-state → Phase 7 end-state)

| Metric | Phase 6 | Phase 7 |
|---|---|---|
| Tests (adk-tracegauge) | 365 | **382** |
| Default check mode | Two-sample (paired opt-in via `--mode paired`) | **Paired, when a key resolves — two-sample automatic fallback** |
| Confidence-level measurement rigor | Point estimates, 500 trials/cell | **Point estimates + Wilson 95% CIs, 2,000 trials/cell, both modes** |
| Shipped `DEFAULT_CONFIDENCE` | 0.98 | **0.98 — unchanged, but now justified against the actual default path (paired), not just two-sample** |
| README power/FPR claims | Bare percentages in places | **Every figure carries trial count + CI, zero bare percentages remain** |
| `tracegauge` 0.10.2 CHANGELOG | Present, not prominently labeled breaking | **Leads with BREAKING, real migration path, README callout** |
| Fresh-wheel pass result | Clean (Phase 5 S5, Phase 6 T5) | **Clean again — third clean pass in a row, first with zero findings at all** |

---

## ROUTE-TO-GG — Train 2 (`adk-tracegauge` 0.3.0 → adk-docs PR)

Train 1 (`tracegauge` 0.10.2) already approved and holds separately — see Phase 6's report;
U4 above only added the BREAKING CHANGELOG framing it was missing, nothing else changed.

1. Review and push: `cd C:\Users\gaura\ml-projects\adk-tracegauge && git push -u origin feat/cost-regression-gate`. Success: branch visible on GitHub, no force needed.
2. Confirm the CI matrix and the wheel-smoke-test job are green on GitHub's real runners (only locally verified across all 7 phases). Success: `ci.yml` all green.
3. Trigger the canary: `gh workflow run pypi-canary.yml --repo gaurav-gandhi-2411/adk-tracegauge`. Success: run completes, installs latest unpinned `google-adk[eval]`, full suite passes.
4. Upstream PR #1: `cd C:\Users\gaura\ml-projects\oss-contrib\adk-python && git push -u origin fix/cost-metric-threshold-directionality && gh pr create --repo google/adk-python --base main --head gaurav-gandhi-2411:fix/cost-metric-threshold-directionality --title "fix(evaluation): honor each metric's own eval_status in AgentEvaluator.evaluate()" --body-file <path>`. Success: PR opens against `google/adk-python`.
5. Upstream PR #2: same pattern with `fix/adk-eval-exit-code` / title `"fix(cli): adk eval process exit code now reflects PASSED/FAILED"`. Success: PR opens.
6. Open a PR from `feat/cost-regression-gate` into `main` — **human merge required** (61 commits, far over the ~400-reviewable-line auto-merge ceiling). Success: PR created, marked DRAFT if any gate is ambiguous.
7. Merge, then per `RELEASING.md`: confirm `git show origin/main:pyproject.toml | grep "^version"` reads `0.3.0` before tagging, then `git checkout main && git pull && git tag v0.3.0 && git push origin v0.3.0` — triggers `release.yml`. Success: `gh run list --workflow release.yml --limit 1` green, log shows the Sigstore verification line and the PyPI project URL.
8. Confirm live: `curl -s https://pypi.org/pypi/adk-tracegauge/json | grep '"version"'` shows `0.3.0`. Post-publish, verify from a **short-path** fresh venv (`uv venv --python 3.11 C:\adk-tg-verify`) — the Windows MAX_PATH trap this build hit repeatedly is documented in `RELEASING.md` now; always use a short path for this check.
9. **Only then**, push adk-docs: `cd C:\Users\gaura\ml-projects\oss-contrib\adk-docs && git push origin docs/adk-tracegauge-integration` (updates the still-open PR #2128 automatically — the page documents 0.3.0-only behavior and must not go live before 0.3.0 does).
10. Optional: remote-delete the 5 already-merged branches carried from Phase 2 (local deletion already done).

No other outstanding TODOs found — cross-checked against every phase report's own ROUTE-TO-GG list and `RELEASING.md`'s actual documented flow.
