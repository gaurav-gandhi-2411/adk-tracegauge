# Phase 9 — Design (HH2-HH4)

**Status: DESIGN ONLY for HH2/HH3.** No feature code written for these sections. HH1 (build) is reported separately — see PRs #13 (`adk-tracegauge`) and #10 (`tracegauge`), both draft, both green, routed to GG. This document leans on `docs/audit/PHASE9_GATHER.md`'s findings (II1-II6) throughout — cited inline, not re-derived.

---

## HH2 — Cost-quality Pareto: feasibility only

### 2.1 Does ADK persist per-case quality scores keyed to the same `eval_case_id`?

**Yes — see PHASE9_GATHER.md II5.1.** `google/adk/evaluation/eval_result.py`'s `EvalCaseResult.eval_id` + `EvalMetricResultPerInvocation.eval_metric_results: list[EvalMetricResult]` carries every configured metric (cost and quality both) for the same invocation under the same case-level id. VERIFIED by reading the installed `google-adk==2.7.0` source directly.

### 2.2 Can a user run one evalset across N models and get cost+quality per model, no custom harness?

**No — one real gap, see PHASE9_GATHER.md II5.2.** `adk eval`'s CLI (`cli_eval --help`, checked live) has no `--model` override. The model lives inside the agent module's own `LlmAgent(model=...)` definition. Running N models means N agent-module directories, each identical except the `model=` line, each run as its own `adk eval` invocation:

```bash
adk eval agent_dir_gemini_flash/   my_evalset.json --config_file_path cfg.json
adk eval agent_dir_gemini_pro/     my_evalset.json --config_file_path cfg.json
adk eval agent_dir_claude_haiku/   my_evalset.json --config_file_path cfg.json
```

Each with `--eval-history`-style snapshot capture wired the same way `examples/04` already does, then a NEW `compare`-style command (not built) reads all N `.evalset_result.json` files. This is mechanical, scriptable (a shell loop over model names against a templated agent directory), not literally "zero setup" — real, if modest, friction remains even after building the comparison statistic itself.

### 2.3 The statistic — two-dimensional, CIs on both axes, N models, multiple comparisons, minimum n

**Design, not implemented:**
- For each model: bootstrap CI on cost (reusing `_regression.py`'s existing `bootstrap_diff_of_means`/paired machinery, keyed on the same `eval_case_id` this package already resolves) and an identical bootstrap CI on the quality metric's per-invocation scores.
- **Joint correlation, not two independent bars**: since cost and quality are paired on the same invocation, the bootstrap resample must draw INVOCATION INDICES (not cost and quality separately) so the real per-invocation cost/quality correlation is preserved in the resulting 2D confidence region — an independent-axis resample would silently discard information this package's own data already carries.
- **Multiple comparisons**: comparing M models (star topology, each vs. one incumbent) is 2M hypothesis tests (M cost + M quality). Design choice: **Bonferroni correction** (α/M per test) — conservative, simple to verify, consistent with this project's own established "err conservative, state the tradeoff plainly" posture (matches `_regression.py`'s own documented anti-conservatism-awareness) rather than a more powerful but harder-to-verify FDR method.
- **Minimum n**: the SAME `MIN_N_DEFAULT=30` floor `_regression.py` already uses — n's statistical job (bootstrap/CLT coverage validity) doesn't change because a second axis was added, exactly the reasoning Phase 7 U1 already established for paired-vs-two-sample. Not a new number invented for this feature.
- **This statistic has NOT been built or false-positive-rate-tested.** Per this project's own new rule (`RELEASING.md`, added this session): no comparative claim ships without a significance test measured, not assumed. Before this feature could ship, its multiple-comparisons-corrected FPR would need the same `>=2,000-trial`, Wilson-CI discipline the FPR-anomaly investigation already established as this project's own bar.

### 2.4 Sketch: CLI output and the recommendation sentence

```
adk-tracegauge compare --evalset my_evalset.json \
  --snapshots gemini-flash=flash.json,gemini-pro=pro.json,claude-haiku=haiku.json \
  --quality-metric final_response_match_v2 --baseline gemini-flash

Model             n    Cost (98% CI)                 Quality (98% CI)
gemini-flash      32   $0.0042 [$0.0038, $0.0046]     0.81 [0.76, 0.86]
gemini-pro        32   $0.0198 [$0.0189, $0.0207]     0.89 [0.85, 0.93]
claude-haiku      32   $0.0067 [$0.0061, $0.0073]     0.83 [0.78, 0.88]

Bonferroni-corrected pairwise vs. gemini-flash (alpha=0.02/2=0.0100 per test, 2 comparisons):
  gemini-pro:    +371% cost (significant), +0.08 quality (significant)
  claude-haiku:  +60% cost (significant), +0.02 quality (NOT significant at corrected alpha)

RECOMMENDATION: claude-haiku's quality gain over gemini-flash is not statistically
distinguishable from noise at this sample size (n=32) after correcting for 2
comparisons -- gemini-flash remains cost-efficient unless a larger evalset changes
this. gemini-pro is a real quality improvement at a real, large cost increase --
worth it only if that specific delta matters for your use case.
```

### 2.5 Where ADK falls short

It doesn't, for the data model (2.1) — the one real gap is CLI ergonomics (2.2), not a missing capability.

---

## HH3 — Changepoint + outliers: feasibility only

### 3.1 Does either package's format support accumulating runs over time?

**`tracegauge`: yes, already, zero extension needed — see PHASE9_GATHER.md II1.3.** The `sessions` SQLite table already persists `source_mtime` (a real, per-session timestamp) for every scored session; a changepoint analysis over "cost per session, ordered by `source_mtime`" needs new ANALYSIS code only.

**`adk-tracegauge`: no — real extension required.** `snapshot.py`'s format is architected around exactly-two-point comparison (baseline vs. current) — a deliberate design choice for the CI-gate use case, not an oversight. Supporting changepoint-over-history would need a genuinely new accumulation mechanism (e.g. an appended, timestamped history file or a small SQLite store mirroring `tracegauge`'s own pattern) — real, unbuilt work, not a config flag.

### 3.2 Method, stdlib-only, and FPR measurement standard

**Proposed: binary segmentation, not CUSUM.** Recursively split the ordered session series at the point maximizing a two-sample test statistic, then recurse on each half. Chosen over CUSUM specifically because it can **reuse this project's existing, already-validated bootstrap two-sample comparison** (`_regression.py`'s `evaluate_regression`) as its splitting criterion, rather than requiring a new statistical primitive (CUSUM needs its own threshold calibration, an independent piece of new, unvalidated machinery). Stdlib-only is achievable the same way `_regression.py` already is (no numpy/scipy dependency, confirmed by that module's own existing zero-scientific-dependency design).

**FPR measurement — "same standard as the regression gate, no exceptions" (explicit, non-negotiable per this instruction):** build a dedicated measurement script mirroring `scripts/measure_regression_confidence_grid.py`'s own methodology — generate null-hypothesis session histories (no true changepoint), run the binary-segmentation detector, measure the empirical false-positive rate at `>=2,000` trials/cell with Wilson 95% CIs, across a real n/effect-size grid. **This must happen before any changepoint capability publishes a claim about its own reliability** — the exact discipline this session's `RELEASING.md` amendment (added after the FPR-anomaly incident) now requires for any comparative statistical statement.

### 3.3 Per-case outliers

**Proposed: MAD-based (median absolute deviation) robust z-score.** Flag an invocation whose cost is more than `K` MADs from the session/run's median cost. `K=3` is a common, defensible default (robust-statistics convention) but must be empirically validated (same FPR-measurement discipline as 3.2) before shipping as a default, not simply asserted. **N needed**: usable at smaller n than a full bootstrap CI (roughly n>=10-15 is the common floor for MAD-based methods, since it doesn't rely on CLT-based bootstrap coverage) — but this number is a starting estimate, not yet measured against this project's own data shape. **What a user does with the result**: identifies WHICH specific invocation(s) are driving an aggregate cost shift — e.g., distinguishing "every invocation got slightly more expensive" from "one invocation cost 8x everything else and is skewing the mean" — a genuinely different, complementary signal to the existing aggregate regression gate, not a duplicate of it.

### 3.4 Which package, and why not the other

- **Changepoint (over session/run history)**: `tracegauge`. The accumulated-history substrate already exists there (3.1) and is genuinely absent in `adk-tracegauge`, whose snapshot format is deliberately two-point. Building this in `adk-tracegauge` first would mean building the accumulation mechanism from scratch for a capability `tracegauge` already has the data for.
- **Per-case outliers (within one run)**: **both, symmetrically — the evidence doesn't support picking one over the other.** `adk-tracegauge` already has per-invocation cost data within a single `check` run (the baseline/current cost lists themselves); `tracegauge` already has per-invocation costs via `SessionCost.turn_costs`. This is a natural extension of each package's EXISTING single-run analysis, not a cross-run history question — stating this plainly rather than forcing an artificial single-package answer.

---

## HH4 — Rank and recommend

### 4.1 Rank by (value × differentiation) / effort, checking the competitor matrix

**Value, from PHASE9_GATHER.md II6.3 — the demand check, taken at face value:**

| Item | Feasibility | Competitor status (re-checked live where marked) | Real demand found (II6) |
|---|---|---|---|
| HH2 — cost-quality Pareto / model recommender | High (2.1-2.4) | Not re-verified live this session for any named competitor — UNVERIFIED, carried forward from Phase 1's snapshot | **None found** |
| HH3.1-2 — changepoint (`tracegauge`) | Medium (data ready, algorithm + FPR-validation work remains) | Not checked this session | **None found** |
| HH3.3 — per-case outliers | High (both packages have the raw data, MAD is cheap) | Not checked this session | **None explicitly found**, but directly extends an ALREADY-demanded feature (Discussion #3273/#97 validate the regression gate itself) |
| ADK loop/redundant-call waste detection | Medium (ADK's plugin surface supports it, II3.2; but needs real new capture code + a from-scratch transient-pattern list, II3.5) | Not re-verified live for Phoenix/Weave/LangSmith this session — UNVERIFIED (II3.4) | **None found** |
| Sub-agent attribution | — | — | **Not searched this pass** — cannot rank defensibly without doing the II6-style check first |
| Actions summary | — | — | **Not searched this pass** — same gap |

**Ranking, given the above**: every item this pass actually gathered real demand evidence for (HH2, HH3.1-2, ADK waste detection) shows **zero observed demand**. HH3.3 is the sole exception — not because a user explicitly asked for outlier detection by name, but because it's cheap, high-feasibility, and directly extends a feature with real, multiply-corroborated demand (the cost-regression gate itself). Sub-agent attribution and Actions summary cannot be honestly ranked in this pass — the demand check specified for II6 was never run against them.

### 4.2 Recommend: what ships in 0.4.0, what waits

**Nothing from HH2 or HH3.1-2 ships in 0.4.0 on current evidence.** Both are technically real (feasible, well-specified, statistically sound as designed) but neither has a validated demand signal, and both would be genuine, non-trivial engineering investments (a new multi-model CLI workflow plus a Bonferroni-corrected two-axis statistic for HH2; a new accumulation mechanism plus a from-scratch FPR-validated changepoint detector for HH3.1-2). Building either now would be building for a hypothesis, not a request — exactly the pattern Phase 8 flagged and killed for Option C.

**HH3.3 (per-case outliers) is the one candidate worth prototyping**, specifically because it's cheap relative to the other three (no new accumulation mechanism, no multi-model CLI friction, no new hypothesis-testing framework — just a MAD statistic over data both packages already have) and extends a feature with real demand rather than answering a question nobody has asked. Even this should ship with its own FPR/false-discovery measurement (3.3) before being presented as reliable, not asserted from the `K=3` convention alone.

**What waits, and what would trigger revisiting**: HH2 and HH3.1-2 wait for either (a) a real, external, unprompted user request matching their shape (mirroring the exact revisit trigger Phase 8 set for Option C), or (b) sub-agent-attribution/Actions-summary's own demand check actually being run and coming back positive, which would change the relative ranking. Re-run II6's exact search methodology against those two named items before this document's ranking can be treated as complete.

### 4.3 Architecture for its own sake — applying Phase 8's own test

**HH2 and HH3.1-2 currently read as architecture for their own sake, by the identical test Phase 8 applied to kill Option C**: real technical feasibility (verified, cited, cleanly designed) combined with zero validated demand (II6, searched live, not assumed). Phase 8's own words apply without modification: *"a bet on future demand, not a response to present demand."* The honest position, matching Phase 8's discipline rather than defending these designs out of the effort already spent specifying them: don't build HH2 or HH3.1-2 now. HH3.3 clears the bar only narrowly, and only because it rides on an already-validated feature rather than standing alone.
