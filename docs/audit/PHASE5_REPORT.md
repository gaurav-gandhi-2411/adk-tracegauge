# adk-tracegauge — Phase 5 Report: reconsidering the fork, tuning the gate

Branch: `feat/cost-regression-gate`. Not pushed, not tagged, not merged, not published.
Audit date basis: 2026-08-16. Written by the orchestrator after all five work items (S1–S5)
and their required independent verifier passes completed.

**Framing:** GG owns both `adk-tracegauge` and `tracegauge` (source repo:
`token-efficiency-scorer`, import name `tes`). Phase 4's R5 forked the pricing arithmetic
out of `tracegauge`, citing a version-dependent licensing claim among its reasons. This
phase re-examines that decision on its merits — and, along the way, found something more
urgent than the architecture question itself.

---

## S1 — Does live `tracegauge` ship wrong dollars (blocking, done first)

**Yes. Confirmed, independently re-verified.** `tracegauge==0.10.1` — live on PyPI today,
169 downloads/week, actively maintained (10 releases since 2026-06-07) — has real,
unfixed pricing defects, confirmed by downloading and diffing the actual published wheel
(not just the local checkout).

### S1 defect list with dollar magnitudes

| Defect | Present in live `tracegauge`? | Dollar magnitude (realistic call) |
|---|---|---|
| **No price-table entries for `claude-opus-5`/`claude-sonnet-5`** — the current Claude flagship models, and very likely the *mainline* real-world case (tracegauge's whole purpose is scoring Claude Code sessions, and this session itself runs on Sonnet 5) | **YES — confirmed** | Sonnet-5 call (600k input + 80k output tokens): charged $3.00 (wrong default rate) vs. true $2.00 → **overcharge +$1.00 (+50%)**. Opus-5 call, same tokens: charged $3.00 vs. true $5.00 → **undercharge, only 60% of true cost reported** |
| **Server-side tool billing never captured** (e.g. web search, $10/1,000 searches) | **YES — confirmed** | 20 web-search calls in an otherwise-correct session: **$0.20 silently dropped, zero warning of any kind** — scales linearly, worse than the model-default case since there's no partial flag at all |
| No staleness-guard mechanism (`as_of` read only for display, never compared to current date; no CI equivalent to adk-tracegauge's `price-freshness.yml`) | **YES — confirmed** | Table is 67 days stale today with zero alert, ever |
| Promotional-rate expiry handling | Mechanism absent, but no currently-realized mispricing from it (no active promo in the current table) | $0 today, but the capability genuinely doesn't exist |
| Cache-read/write multipliers | **NO — correct**, verified against live vendor page | — |
| All 9 non-retired Claude model rates present in the table | **NO — correct**, verified against live vendor page | — |
| Long-context tiering | Not applicable — Claude doesn't tier by context length | — |
| Thinking-token handling | Not applicable — already billed as part of `output_tokens` on Claude's API | — |

### S1.4 — incident classification and remediation

**Classified as a published-package correctness incident, separate from and prior in
priority to the `adk-tracegauge` release** (which already removed its dependency and is
unaffected — real, independent `tracegauge` users are not). **Recommendation: a patch
release, `0.10.2`,** fixing the model-table gap and the server-tool-billing gap — not a
yank (does nothing for existing installs, disrupts anyone on a `>=0.10.0,<0.11.0`-style
pin for no corrective benefit, no crash/data-loss/security failure mode that would
justify it) and not ship-as-is-with-a-note (the mainline-default-model finding means
"known issue" would leave the tool wrong for most real sessions by default). Not
implemented this item — read-only mandate, reported for a human decision. Both the
primary agent and its independent verifier CONFIRMED all 5 checked claims, zero
contradictions.

---

## S2 — Architecture decision (blocking)

### S2.1 responsibility table (summary — full table in `PLAN.md`)

Read all 11 `adk-tracegauge` source modules (4,396 lines). **1,849 of 4,396 lines (42%)
are genuinely provider-agnostic today** — most notably `_cost.py` (fully provider-agnostic,
a byte-identical port of `tracegauge`'s own general-purpose arithmetic) and `_regression.py`
(953 lines, zero non-stdlib imports — the package's actual differentiator, and the most
portable module in the codebase). `_plugin.py`, `evaluator.py`, `_compat.py` are
irreducibly ADK-specific (subclass ADK's `BasePlugin`, reverse-engineer ADK's
`AgentEvaluator` internals, import `google.adk` directly).

### S2.2 three options, evaluated on real cost/risk

- **A (restore dependency, fix upstream):** the "already realized" risk isn't hypothetical
  — S1 just proved it: `tracegauge==0.10.1` today would silently reprice every Sonnet-5
  call. Release coupling is brittle (`tracegauge`/`tes` is 7,540 lines total; pricing is
  4.3% of it).
- **B (keep the fork, status quo):** highest ongoing burden — two structurally different
  schemas, hand-synced forever, and divergence isn't theoretical (S1 proved it already
  happened, under one maintainer, mid-audit).
- **C (core + adapters):** highest one-time cost, lowest ongoing burden thereafter. Real
  cost: 2–3 coordinated releases per fix instead of 1. Best long-term portfolio optics if
  executed well; risk is a new package with no real second consumer yet.

### S2.3 recommendation

**Scoped Option C — with two honest corrections to this phase's own stated prior:**

1. **Premise correction**: R5 didn't fork "specifically citing" licensing — that was
   documented as one of six findings, and R5 itself already verified it was resolved (see
   S2.4). The load-bearing reasons were the other five (undocumented internal API, no
   schema, private resolver, dead code, unused fields) — weaker arguments once you own
   both packages, but real engineering reasons, not a licensing panic.
2. **Direction correction**: `adk-tracegauge`'s own pricing/statistics engine is now *more
   correct and more complete* than `tracegauge`'s own (staleness guard, promo-expiry,
   tiering, bootstrap regression gate — none of which `tracegauge` has). **The right
   migration is `tracegauge` absorbing `adk-tracegauge`'s superior code as its new core,
   not the reverse.** A brand-new third PyPI package is premature — no second real
   consumer exists yet; the core should live inside `tracegauge` itself.

### S2.4 licensing, resolved at its root

Read `LICENSE`, `LICENSE-APACHE`, every SPDX header in `tes/`, and PyPI's live metadata.
**The dual-license SPDX header is present in exactly the two files ever imported by
`adk-tracegauge` (`tes/cost.py`, `tes/_digest.py`), matching upstream HEAD, and
`tracegauge`'s own README already documents this by name, citing `adk-tracegauge` as the
intended beneficiary. R5's finding was correct, not stale — no fix needed, none made.**
Residual soft friction (not a bug): PyPI's package-level license classifier still reads
`AGPL-3.0-only`, standard for a majority-AGPL repo with a documented file-level exception —
a naive automated scanner would flag it regardless of the carve-out, which is itself an
argument for the Option C extraction (a dedicated core package could ship a clean,
unambiguous license), not something to patch in the monolith.

### S2.5 migration plan (not executed this phase)

**Phase M1** (`token-efficiency-scorer`, must land first): ship S1's urgent `0.10.2` patch
independently of this migration; promote `tes.cost`/`tes._digest` to real, documented
public API; port `adk-tracegauge`'s staleness/promo/tiering/regression-gate code up into
it; release `0.11.0`. **Phase M2** (`adk-tracegauge`, only after `0.11.0` is live): re-add
the dependency, delete the now-redundant in-house code, **rename its own `tracegauge`
console script** (see the standalone finding below — this is a real, separate,
already-existing collision), re-verify, ship `0.4.0`. Full breaking-change and
user-impact analysis for both user populations (a `tracegauge`-only user who's never heard
of ADK; an `adk-tracegauge` user who's never heard of `tracegauge`) is in `PLAN.md`'s
Phase 5 S2 entry.

**Standalone urgent finding, independent of the S2 decision**: both packages currently
install a console script literally named `tracegauge` — confirmed by reading both
`pyproject.toml` files directly. Whichever installs second silently clobbers the other's
executable. This needs its own fix regardless of which architecture option is eventually
chosen.

---

## S3 — Shared-feature parity matrix

`tracegauge` has its own CLI (`tes/cli.py`, 1,446 lines) but **zero regression-gate, zero
statistical machinery, zero eval-framework integration of any kind** — confirmed by
reading it directly, no duplication or conflict with anything `adk-tracegauge` built in
Phases 2–4.

| Capability | In `tracegauge` today | In `adk-tracegauge` today | Should live (S2.3) |
|---|---|---|---|
| Pricing/model resolution | Yes (own table, own resolver) | Yes (own table, own resolver) | **`tracegauge` core** |
| Promo/expiry handling | **No** | Yes (Phase 5 S1/B2) | **`tracegauge` core** — divergence row |
| Long-context tiering | **No** (not applicable to Claude) | Yes (Gemini) | **`tracegauge` core** (generalized) |
| Cache-discount handling | Yes | Yes | **`tracegauge` core** |
| Staleness guard + CI job | **No** | Yes | **`tracegauge` core** — divergence row |
| Multi-provider support | Claude only | Gemini + Claude + GPT + local | **`tracegauge` core** — divergence row |
| Snapshot format | **No** | Yes (schema v2) | **`tracegauge` core** |
| `check`/regression gate | **No** | Yes (bootstrap CI, paired mode) | **`tracegauge` core** — divergence row |
| Paired-comparison mode | **No** | Yes (`eval_case_id`-keyed) | **`tracegauge` core** — divergence row |
| Achieved-power reporting | **No** | Yes (Phase 4 R4) | **`tracegauge` core** — divergence row |
| OTel export | Not built in either | Not built in either (deferred) | Deferred, either layer |
| CLI | Own (`tes/cli.py`) | Own (`tracegauge` console script — **name collision**) | Both, distinct names |
| Docs/examples | Own | Own | Both, cross-linking |
| CI | Own, no price-freshness | Own, includes price-freshness + wheel-smoke-test | **`tracegauge` core** should adopt adk-tracegauge's CI patterns |

**8 of 13 rows show live divergence or a gap where one package has a capability the other
lacks entirely** — each is a real, already-demonstrated (not theoretical) risk per S1's
finding that this exact kind of drift already happened, silently, under one maintainer.

---

## S4 — FPR tuning across alpha levels

### S4.1 assessment

**Not acceptable.** A ~3.93% false-positive rate (Phase 4's independently-confirmed
measurement) means roughly 1 in 25 clean CI runs fails the build for no real reason. For a
tool whose entire value proposition is being a trustworthy gate, that trains users to
ignore or disable it — a product-credibility failure, not just a statistics footnote.

### S4.2 full 90-cell alpha grid (500 trials/cell; `confidence = 1 - 2·alpha`, verified
against the actual one-sided bootstrap-CI construction code, not guessed)

**alpha=0.025 (confidence=0.95, OLD default):**

| n\eff% | 0% | 5% | 10% | 25% | 50% |
|---|---|---|---|---|---|
| 10 | 0.036 | 0.136 | 0.364 | 0.952 | 1.000 |
| 25 | 0.032 | 0.240 | 0.646 | 1.000 | 1.000 |
| 30 | 0.020 | 0.254 | 0.728 | 1.000 | 1.000 |
| 50 | 0.028 | 0.358 | 0.912 | 1.000 | 1.000 |
| 100 | 0.016 | 0.630 | 0.994 | 1.000 | 1.000 |
| 250 | 0.024 | 0.960 | 1.000 | 1.000 | 1.000 |

**alpha=0.01 (confidence=0.98, NEW shipped default):**

| n\eff% | 0% | 5% | 10% | 25% | 50% |
|---|---|---|---|---|---|
| 10 | 0.022 | 0.084 | 0.276 | 0.888 | 1.000 |
| 25 | 0.012 | 0.142 | 0.514 | 1.000 | 1.000 |
| 30 | 0.012 | 0.162 | 0.584 | 1.000 | 1.000 |
| 50 | 0.016 | 0.248 | 0.834 | 1.000 | 1.000 |
| 100 | 0.004 | 0.484 | 0.990 | 1.000 | 1.000 |
| 250 | 0.006 | 0.894 | 1.000 | 1.000 | 1.000 |

**alpha=0.005 (confidence=0.99, evaluated and rejected):**

| n\eff% | 0% | 5% | 10% | 25% | 50% |
|---|---|---|---|---|---|
| 10 | 0.014 | 0.064 | 0.230 | 0.846 | 1.000 |
| 25 | 0.008 | 0.092 | 0.436 | 0.992 | 1.000 |
| 30 | 0.006 | 0.126 | 0.488 | 1.000 | 1.000 |
| 50 | 0.008 | 0.202 | 0.762 | 1.000 | 1.000 |
| 100 | 0.002 | 0.374 | 0.974 | 1.000 | 1.000 |
| 250 | 0.000 | 0.846 | 1.000 | 1.000 | 1.000 |

n_boot reduced 10,000→1,000 for this specific 90-cell sweep only (justified: validated
first at 4 risk-weighted cells against full n_boot=10,000, 96.7–100% verdict agreement),
906s wall-clock for all 45,000 simulated calls.

### S4.3 power cost at n=30/n=50

| n | effect | confidence=0.95 | confidence=0.98 | confidence=0.99 |
|---|---|---|---|---|
| 30 | 10% | 72.8% | 58.4% | 48.8% |
| 30 | 25%/50% | 100% | 100% | 100% |
| 50 | 10% | 91.2% | 83.4% | 76.2% |
| 50 | 25%/50% | 100% | 100% | 100% |

### S4.4 recommended default: `confidence=0.98`, implemented

Real shipped-config FPR (real practical floors, real `n_boot=10000`, n=30, 500 trials ×
2 seeds): 0.95 → 4.4% combined; **0.98 → 2.3% combined** (>45% reduction, within noise of
the ~2% target); 0.99 → 1.6% but fails the power floor. Power floor: n=50/10%-effect must
stay ≥80% — 0.98 clears it (83.4%), 0.99 does not (76.2%, hence rejected despite its lower
FPR). Implemented as the new `DEFAULT_CONFIDENCE` in `_regression.py`; README/CHANGELOG/
`docs/ci-snippet.md`/`examples/03_ci_regression_gate.py` all re-captured from real
subprocess re-runs against the new default, not hand-edited.

### S4.5 practical-significance floor, confirmed still independent

Re-read the code: `is_regression = statistically_significant and practically_significant`,
unchanged. Its own contribution measured directly: at n=30, both old and new confidence,
the statistical-only FPR is *identical* to the full-shipped-config FPR (23/500 & 21/500 at
0.95; 13/500 & 10/500 at 0.98) — **the practical floor contributes zero extra suppression
at this n/variance today**, now locked in as a permanent regression test so this doesn't
silently change unnoticed.

### Verifier's independent re-measurement

Own script, different seed: FPR at 0.95 = 2.20% (11/500), at 0.98 = 1.00% (5/500) — both
within expected sampling variance for a rare binomial event at n=500 trials, directionally
and magnitude-confirming the tuning decision.

---

## S5 — Regression check on Phase 4 work (final work item)

**Full 4-version suite against live google-adk 2.7.0**: 363/363 passing on Python
3.10.20/3.11.15/3.12.12/3.13.5, identical pass count and coverage on all 4, zero code
changes required.

**Fresh-wheel pass**: all 4 examples, both eval paths, `tracegauge snapshot`/`check`
(two-sample and paired), and every runnable README/docs code block reproduced exactly from
a genuinely fresh wheel-only install outside the repo — including confirming S4's new
`--confidence 0.98` default is live in the CLI's own `--help` text and produces the exact
CI bounds the README now claims. **The first fresh-wheel pass in this entire build's
history to find zero discrepancies** — the prior three (Phase 3 B7, Phase 4 R7, Phase 4
R3) each found a real bug.

**S5.3 byte-equivalence of the R5-ported arithmetic**: installed real `tracegauge==0.10.1`
into a separate scratch venv, fed 110 identical synthetic inputs (22 price-table models ×
5 token-count scenarios, each using a controlled per-case price dict — not either
package's own bundled table, which correctly isolates "does the ported arithmetic match"
from the already-separately-reported S1 finding that the two packages' *bundled data*
diverges) to both `adk_tracegauge._cost.compute_turn_cost` and the live external
`tes.cost.compute_turn_cost`. **All 110 matched bit-for-bit.** Extended into a permanent,
frozen test (`tests/test_cost_port_fidelity.py`) with no runtime dependency on the
external package.

**A note on verification, done transparently**: the independent verifier initially reported
this claim as CONTRADICTED, having read the comparison as using each package's own
divergent bundled price table (which would indeed invalidate it for the 6 models absent
from `tracegauge`'s table, including `claude-opus-5`/`claude-sonnet-5`). The orchestrator
resolved this discrepancy directly by reading the actual test harness source
(`test_every_price_table_entry_matches_live_tracegauge_arithmetic`,
`tests/test_cost_port_fidelity.py:461-497`): it constructs a synthetic, per-case `prices`
dict and passes the identical structure to both implementations, confirmed by
`claude-opus-4-8` (present in `tracegauge`'s table) and `claude-opus-5` (absent from it)
producing byte-identical results at the same injected $5/$25 rate — which would be
impossible if `tracegauge`'s own incomplete bundled table had actually been used for
lookup. **The original S5 claim stands; the verifier's concern was a reasonable-sounding
but ultimately mistaken reading of the methodology**, not a real defect. Recorded here
plainly rather than silently resolved, consistent with this project's standing practice of
not letting a disagreement between an agent and its verifier go unexamined.

---

## Before/after summary

| Metric | Phase 4 | Phase 5 |
|---|---|---|
| Tests | 357 | **363** |
| Live `tracegauge` (external package) pricing state | Unknown/unaudited | **Confirmed defective — patch recommended, not yet applied** |
| Fork justification | Licensing claim stated as one of six reasons | **Licensing confirmed correct (not the real issue); direction of a future merge corrected — `tracegauge` should absorb `adk-tracegauge`'s code, not vice versa** |
| Regression-gate default FPR at n=30 | ~3.93% (independently confirmed, unacceptable) | **~2.3% shipped, confidence=0.98, power floor preserved** |
| Console-script collision | Undiscovered | **Found: both packages install an identical `tracegauge` command name** |
| Port-fidelity confidence | Source-diff only (Phase 4 R5) | **Independently proven functionally equivalent across 110 cases, disagreement with verifier resolved transparently** |

---

## ROUTE-TO-GG list

1. **Ship `tracegauge` 0.10.2** (S1, urgent, independent of everything else in this phase): fix the missing `claude-opus-5`/`claude-sonnet-5` price entries and the server-tool-billing gap in `token-efficiency-scorer`, release per its own documented `RELEASING.md` process. This is a live correctness issue affecting real users today — recommend prioritizing this over the adk-tracegauge release itself.
2. **Decide on the S2.3 architecture recommendation** (Option C, scoped, with `tracegauge` absorbing `adk-tracegauge`'s superior pricing/statistics code as its new core) — a real decision only GG can make, not something to execute without sign-off given it spans two packages and two release cadences.
3. **Fix the console-script name collision** (both packages ship a `tracegauge` command) — needed regardless of the S2 decision's timeline; recommend picking a distinct name for one of them soon, independent of the larger migration.
4. **Continue the remaining Phase 2-4 ROUTE-TO-GG items** (push the branch, confirm CI green including the wheel-smoke-test job, trigger the canary, open the two prepared upstream `google/adk-python` PRs, push the `adk-docs` PR only after 0.3.0 is live on PyPI, optional remote branch cleanup, the version-bump/PR/publish sequence) — all still outstanding, full detail in `docs/audit/PHASE4_REPORT.md`'s ROUTE-TO-GG list, unchanged by this phase.
5. **When ready to execute the S2.5 migration plan** (not this phase): Phase M1 in `token-efficiency-scorer` first (ship 0.10.2, promote `tes.cost`/`tes._digest` to real public API, port the staleness/promo/tiering/regression-gate code up, release 0.11.0), Phase M2 in `adk-tracegauge` only after 0.11.0 is live (re-add the dependency, delete the redundant in-house code, rename the colliding console script, re-verify, ship 0.4.0).
