# Phase 9 — Gather (II1-II6)

**Status: GATHERING ONLY.** No feature code, no PRs, no decisions. Answers below feed HH2/HH3's design doc (`docs/audit/PHASE9_DESIGN.md`) and HH4's ranking.

---

## II1 — What does `tracegauge` actually need

### 1.1 What questions does `tes/` answer, where does it stop short?

Answers today (VERIFIED, read `tes/score.py`, `tes/report.py`, `tes/cli.py` directly, and exercised live via the HH1.2 quickstart command this session): for ONE session — was token usage efficient relative to a baseline (token economy axis), was the trajectory good (trajectory axis, requires a judge), did any deterministic waste pattern fire (waste axis), what did it cost (cost annotation). Every axis is explicitly labeled `UNAVAILABLE` rather than fabricated when the scope gate isn't cleared or no judge ran — confirmed live in the quickstart output this session.

**Stops short of**: any question spanning MORE than one session at a time via the CLI's primary path. `tes score` scores one file (or a directory, but still one-at-a-time, no cross-session synthesis in the printed report). `tes patterns` (ML clustering/anomaly detection, see 1.5) is the one exception — it does look across the whole stored corpus, but its output is archetype membership and anomaly flags, not a trend or "did this get worse" answer. Nothing answers "is my cost/waste per session going up or down" as a first-class printed verdict — the closest existing thing (`tes budget`) is a forward projection, not a backward-looking trend/shift detector (see 1.2).

### 1.2 Does anything currently do trend, drift, or over-time comparison?

**VERIFIED, partial capability exists, no statistical drift/changepoint detection anywhere.**

- `tes/budget.py`'s `compute_budget_projection` (read in full): a **linear self-trend projection** — total cost over a trailing window / days observed × window length, framed explicitly as "trending toward," never a forecast. No confidence interval, no significance test, no changepoint. Cites `research/13_coach_alarm_honesty_design.md` for its own honesty framing.
- `tes/web/server.py`'s `/trends` route (read in full): purely descriptive aggregation — `GROUP BY task_type`, `GROUP BY band_verdict`, and the last 50 sessions' waste-event counts, handed to `trends.html` for charting. No statistical test of any kind; this is a dashboard data feed, not an analysis.
- **Nothing in the codebase tests whether a metric has genuinely SHIFTED at some point in a session history** — no CUSUM, no binary segmentation, no bootstrap comparison of an early-window vs. late-window slice. `budget.py`'s projection and `/trends`'s aggregation are the full extent of "over time" capability today.

### 1.3 Do transcripts carry timestamps/session boundaries sufficient for changepoint detection over history?

**VERIFIED, per-session yes, per-turn no — but per-session is exactly the granularity a changepoint detector over run history needs.**

- `tes/adapt.py`'s `adapt_session` (read in full, grepped for `timestamp`): does **not** parse any per-message/per-turn timestamp field from the raw Claude Code JSONL at all. `session_id` is derived purely from the filename (`session_path.stem`), not a timestamp.
- `tes/store.py`'s `sessions` table (schema read directly): stores `source_mtime` (the source file's real filesystem modification time, `REAL NOT NULL`) and `scored_at` (when `tes` scored it, a processing-time artifact, not session-occurrence time) — `idx_sessions_scored_at` index confirmed. `source_mtime` is a genuine, real timestamp per session already captured and persisted for every scored session.
- **Conclusion**: session-level ordering over calendar time is already fully supported by the existing store schema — a changepoint detector operating on "cost per session, ordered by `source_mtime`, across the last N sessions" needs **zero extension to the data-capture layer**. It would need new ANALYSIS code (reading from the existing `sessions` table), not a new capture mechanism. Per-turn/within-session timestamps are absent and would need extending `adapt_session` if a future feature needed within-session temporal analysis — no such requirement exists today.

### 1.4 What would drift detection tell a user the current dashboard does not?

The dashboard (`/trends`, `tes budget`) answers "what's my rolling average/projection." Neither answers "did something change at a specific point" with a stated confidence level — e.g., "your redundant-read rate was stable at ~2% for 40 sessions, then jumped to ~9% starting around session 47, and that shift is statistically distinguishable from noise (not just a project-to-project mix change)." That is a genuinely different question from a rolling average, and the current codebase has no answer to it at any confidence level — UNVERIFIED whether any user has actually asked this exact question (see II6).

### 1.5 Waste/pattern detection and community baselines — mechanism, not description

**Waste detection** (`tes/_waste_detectors.py`, read in full — 409 LOC, two detectors):
- `detect_repeated_failed_retry`: fires on a run of **≥2 consecutive IDENTICAL shell (Bash/PowerShell) failures**, with **no state-changing operation** (no `Write`/`Edit`/`NotebookEdit` call, no shell command matching a "state mutation" pattern list, no human/user turn) between any pair. A curated, explicit exclusion list (`_TRANSIENT_PATTERNS`) unconditionally suppresses known transient conditions — `ZONE_RESOURCE_POOL_EXHAUSTED`, `QUOTA_EXCEEDED`, `rate.?limit.?exceeded`, `429 Too Many Requests`, `503 Service Unavailable`, and `gh CLI` CI-polling status text — so a legitimate retry-with-backoff against a real transient failure does not fire the detector, by construction, not by inference.
- `detect_redundant_read`: two paths — PATH A is tool-authoritative (Claude Code's own `Read` tool reported "File unchanged since last read"); PATH B is inferred (two `Read` results carry identical line-numbered content within ≤10 turns, with the same state-change-barrier logic as above). Every event is auditable — carries the exact proof turns.
- Both are **pure pattern-matching over the transcript's own recorded content**, zero LLM inference, zero statistical model.

**Community baselines** (`tes/data/cc_baselines.json`, read directly): `"baseline_population": "local Claude CC sessions only"`, `"total_baseline_sessions": 75` — **real, collected data from the maintainer's own actual Claude Code sessions**, not synthetic. The file documents its own circularity check (Spearman r=-0.08, p=0.34 between baseline tokens and judge scores — "sufficiently independent") and per-task-type scope floors (`p10_turns`) derived from that same real population. Confirmed consistent with the quickstart demo's own printed caveat this session ("Calibrated to a high-waste infra/ML-ops corpus (1 developer, 75 quality-gated sessions)").

---

## II2 — Shared statistics: has the Option C trigger fired?

### 2.1 Has it fired?

**No — not as originally stated.** Phase 8 FF4.3 named "`tracegauge` independently developing its own need for a regression-gate/statistics-engine feature" as the specific trigger. II1.2 found `tracegauge` has a linear-projection trend feature (`budget.py`) and descriptive aggregation (`/trends`), but **no regression-gate, no changepoint detector, no bootstrap-CI comparison anywhere in the current codebase** — the trigger condition (an actual, already-built or already-needed statistics engine inside `tracegauge`) has not occurred. What HAS changed since Phase 8: II1.3 establishes the DATA SUBSTRATE a changepoint feature would need (`sessions.source_mtime`) already exists with zero extension required — that's new information Phase 8 didn't have, but it's a capability GAP being newly scoped (HH3), not a fired trigger.

### 2.2 If it had fired: what would be shared, and how much of `_regression.py`'s 1,173 LOC is reusable

Answered hypothetically, since 2.1 found it hasn't fired — reported for completeness per the instruction to report both sides with numbers:

- **Bootstrap CI machinery** (`bootstrap_diff_of_means`, `bootstrap_mean_of_paired_deltas`, percentile-interval construction): fully provider-agnostic (confirmed Phase 8 FF1.1 — operates on plain float lists, no ADK import). This is the part MOST reusable unchanged — call-site only needs a list of floats per group/pair.
- **Changepoint/CUSUM** (proposed HH3, doesn't exist in `_regression.py` today): would be NEW code either way, not extracted from the existing 1,173 LOC — `_regression.py` compares exactly two groups (baseline vs. current), not an ordered sequence of N sessions. Sharing here means sharing a NEW module's design/tests across repos, not reusing existing LOC.
- **Outlier detection** (proposed HH3.3): same — new code, not extractable from the existing regression-gate engine.
- **Rough reusable-unchanged estimate**: the bootstrap-CI core (`bootstrap_diff_of_means`/`bootstrap_mean_of_paired_deltas`, roughly 80 LOC combined per Phase 8's own module read) is the only piece with a plausible "reusable unchanged" claim; the rest of `_regression.py`'s 1,173 LOC (percentile/CI-width calibration tuned to the two-sample/paired comparison shape, the `_paired_mode_viable` threshold logic, the achieved-power reporting) is specific to the baseline-vs-current comparison shape, not to an N-session changepoint problem — UNVERIFIED precise percentage without a line-by-line audit, but the bootstrap core is a small minority of the total.

### 2.3 Cost of two implementations by one maintainer — concrete divergence risk, same rigor as the price-table analysis

If `tracegauge` builds its own changepoint/statistics code independently of `adk-tracegauge`'s `_regression.py`, the concrete, specific risk (mirroring the FPR-anomaly and price-table incidents' shape): **the same bug class gets independently reintroduced and independently discovered twice.** Concrete precedent already on record this session: `tracegauge`'s price-freshness guard was independently rebuilt from adk-tracegauge's own guard concept, and in the interim `tracegauge`'s price table went 67 days stale with zero signal (GG1/Phase 8 FF2.2). The same shape applies to statistical code: if `tracegauge` writes its own bootstrap-CI or significance-test logic for a changepoint feature, an off-by-one or anti-conservatism bug (the FPR-anomaly investigation found percentile-bootstrap anti-conservatism at small n is a REAL, already-documented property of `_regression.py`'s own method) would need to be independently rediscovered in `tracegauge`'s code, on its own timeline, rather than being caught once and fixed in a shared core.

### 2.4 Cost of sharing — the removed dependency, and whether extras-scoping is clean

**Checked directly** (VERIFIED, `pyproject.toml` read): `tracegauge`'s core dependencies are `flask>=3.0,<4`, `httpx>=0.27,<1`, `numpy>=1.24,<3`, `scikit-learn>=1.3,<2` — all declared in the single, undifferentiated `dependencies` list, **not currently split into any extras group**. `[tool.setuptools.packages.find]` treats `tes*` as one package with no sub-package separation between the web dashboard (`tes/web/`, needs Flask) and the core scoring/cost engine (`tes/cost.py`, `tes/score.py`, needs none of Flask/sklearn/numpy for the pure cost-arithmetic path — `tes/cost.py` itself has zero imports from flask/sklearn/numpy, confirmed by its own import block).

**Can it be extras-scoped cleanly?** Plausibly yes for the specific modules a shared statistics engine would need (`tes/cost.py`, a hypothetical new `tes/regression.py` or similar) since they don't import Flask/sklearn/numpy directly — but `tracegauge`'s **packaging structure would need real work** to get there: `[tool.setuptools.package-data]`/`packages.find` currently ships the whole `tes` package as one unit, and `tes/cli.py` (the single entry point) imports from `tes.web.server`, `tes.self_baseline` (which likely pulls in the ML modules), etc. at module scope in places — UNVERIFIED whether a clean split exists without touching `cli.py`'s own import graph, which would need a real audit before claiming "clean," not assumed.

### 2.5 Both sides, no decision

**For sharing**: the FPR-anomaly-shaped divergence risk (2.3) is real and precedented, twice now (price-freshness guard, price-table data). **Against sharing**: the trigger (2.1) hasn't fired — no actual `tracegauge` statistics need exists yet to share against, only a hypothetical one (HH3's own proposal, not yet built or requested by any user — see II6). Sharing infrastructure that doesn't exist yet, to prevent a divergence that can't happen until it's built twice, inverts the sequencing that made GG1's targeted-test approach work for pricing (react to a real, narrow, already-measured risk, not a hypothetical one).

---

## II3 — Waste detection for ADK

### 3.1 What does `adk-tracegauge` capture today per invocation?

**VERIFIED, full field list**, `_store.py`'s `CapturedCall` dataclass, read directly: `model_version: str`, `prompt_token_count: int`, `candidates_token_count: int`, `cached_content_token_count: int`, `total_token_count: int`, `partial: bool = False`, `thoughts_token_count: int = 0`, `tool_use_prompt_token_count: int = 0`. All sourced from `LlmResponse.usage_metadata` via `after_model_callback`. **Nothing about tool identity, tool arguments, tool success/failure, or timestamps is captured today.**

### 3.2 Can loops/redundant calls/retries/dead-ends be detected from that, or is more capture needed?

**More capture is needed — but ADK's own event/plugin surface already exposes what's missing, confirmed by reading it directly, not assumed:**
- `LlmResponse.content` (checked via `LlmResponse.model_fields`, live this session) is available inside `after_model_callback` today and carries the actual response `Content`, whose `parts` include `function_call` entries (tool name + arguments) when the model calls a tool — this data already flows through the hook `adk-tracegauge` already uses, just isn't read.
- `BasePlugin`'s full method list (checked live via `dir(BasePlugin)` this session) includes `before_tool_callback`, `after_tool_callback`, and `on_tool_error_callback` — none of which `TraceGaugeUsagePlugin` currently implements. These are the hooks that would carry tool execution OUTCOME (success/failure), which `after_model_callback` alone cannot see (a tool's result flows back into the NEXT model call, not the current response).
- **Conclusion**: detecting loops/redundant calls/retries for ADK is feasible using ADK's EXISTING plugin surface (no ADK-side gap) — it requires new capture code (wiring `after_tool_callback`/`on_tool_error_callback`, and reading `function_call` parts from `LlmResponse.content`), which is real, unbuilt engineering work, not a research question.

### 3.3 How does `tracegauge`'s waste detection work, and is any of it transferable?

**The LOGIC is conceptually transferable; the CODE is transcript-shape-specific and not a drop-in port.** `tracegauge`'s detectors (II1.5) operate on Claude Code's own transcript JSON shape (`tool_names`, `content_snippet` truncated to 300 chars, specific tool names `Bash`/`PowerShell`/`Write`/`Edit`) — none of that shape exists in ADK's `Event`/`LlmResponse` objects, which have their own distinct structure (`function_call.name`, `function_call.args`, `function_response`). The **design principles** are directly transferable and should be reused, not the code: (1) conservative, under-detect-rather-than-over-detect posture; (2) an explicit, curated exclusion list for known-transient conditions (rate limits, quota, resource exhaustion — ADK/LiteLLM's own error surface would need its own equivalent list, likely different exact strings than Claude Code's); (3) auditable proof-turns attached to every event, never a bare verdict.

### 3.4 Competitor matrix re-check — current docs, not the Phase 1 snapshot

Checked live this session (VERIFIED for the two checked; UNVERIFIED for the two not reached):
- **Langfuse**: docs describe tracing, prompt management, and evaluation scoring; no dedicated "agent loop detection" or "redundant tool call" feature found in a targeted pass of their evaluation/scoring docs pages reached this session — UNVERIFIED as a complete negative (a full site crawl was not performed), but no such feature is advertised on the pages checked.
- **AgentOps**: same pattern — session replay and cost tracking are the advertised features; no loop/redundant-call detector found on the pages checked this session.
- **Phoenix (Arize) and Weave (W&B) and LangSmith**: **not independently re-checked live this session** — UNVERIFIED, carried forward from Phase 1's snapshot rather than re-confirmed, flagging this explicitly rather than silently reusing the old finding as if it were re-verified.

### 3.5 Measuring detection accuracy without hand-labeled data; distinguishing legitimate retry-with-backoff from a real loop

`tracegauge`'s own mechanism (II1.5, II3.3) already IS the answer used in production today, without any hand-labeled dataset: a **deterministic, rule-based exclusion list** for known-transient conditions, combined with a **state-change barrier** (any write/edit/mutating action between two "identical" events breaks the run) rather than a statistical or ML classifier. This sidesteps needing labeled data entirely — the detector doesn't try to guess "is this a loop," it defines "loop" narrowly enough (identical failure, no state change, not a known-transient pattern) that the definition itself is close to unambiguous, and accepts under-detection (rule 6's "conservative" design constraint) as the cost of avoiding false positives without labels. The equivalent for ADK would need its own curated transient-pattern list (rate limits, quota errors, ADK/LiteLLM's specific error surface — not yet audited, real work) and its own state-change-barrier definition (which ADK tool calls count as "mutating" vs. "read-only," not yet audited).

---

## II4 — Static HTML report

### 4.1 What would it contain that stdout cannot convey?

Visual, at-a-glance representations stdout can't render well: a cost-over-time or per-session-cost chart (line/bar), a distribution histogram of per-invocation costs (useful for spotting the shape of a regression, not just its mean), and — for `tracegauge` specifically — the three-axis report already exists as structured data (`ThreeAxisResult`) that could be laid out as a dashboard-style single page rather than a scrolling terminal block, useful for sharing with someone else (a PR reviewer, a teammate) who doesn't want to run the CLI themselves.

### 4.2 Stdlib-only, single file, no server — what's the actual constraint?

**Checked directly (VERIFIED):** `tracegauge` already depends on `jinja2` (a REAL, already-declared transitive dependency via `flask`, confirmed in the fresh isolated wheel-install log this session: `Installing collected packages: ... jinja2 ... flask ... tracegauge`) — so using Jinja2 to render a static HTML string (via `Environment(loader=...).get_template(...).render(...)`, no Flask app/server needed) would not add a NEW dependency for `tracegauge`, since it's already present. For `adk-tracegauge`, Jinja2 is **not** currently a dependency at all (confirmed: its only core dependency is `google-adk[eval]`) — a genuinely stdlib-only report there would mean hand-built HTML via plain string formatting/`html.escape`, or accepting a new dependency. The "single file, no server" constraint itself is straightforward either way: write the rendered string to one `.html` file with any charts as inline SVG (stdlib `xml`/manual SVG string-building) or a small embedded `<canvas>` + inline JS (no CDN, no external asset) — both packages' existing "zero-cost, no network call" ethos rules out pulling a charting library from a CDN.

### 4.3 Does either package already have report-rendering code?

**Yes, `tracegauge` only.** `tes/web/templates/*.html` (Jinja2 templates, confirmed in `pyproject.toml`'s `package-data` list) rendered via Flask's `render_template` inside `tes/web/server.py`'s route handlers (`/trends`, and others read earlier this session) — but every current template render is **coupled to a live Flask request/response cycle and live `get_db()`/`get_self_bl()` connections**, not designed to produce a standalone file today. Reusing the templates for a static export is plausible (Jinja2 itself doesn't require Flask) but would need new code to assemble the template context without a live request. `adk-tracegauge` has **zero** HTML-rendering code of any kind — pure CLI/stdout and JSON output only, confirmed by the absence of any `.html` file or Jinja2 import anywhere in `src/adk_tracegauge/`.

---

## II5 — Quality scores (carried from HH2.1)

### 5.1 Does ADK persist per-case quality scores keyed to the same `eval_case_id` cost is paired on?

**VERIFIED, yes — read directly from the installed `google-adk` package's source this session, not inferred:**
- `google/adk/evaluation/eval_result.py`'s `EvalCaseResult` class: carries `eval_id: str` (the stable, authored eval-case id — the SAME field `adk-tracegauge`'s own `_compat.load_eval_case_ids_by_session_id` already joins on for paired-mode cost comparison) and `eval_metric_result_per_invocation: list[EvalMetricResultPerInvocation]`.
- `EvalMetricResultPerInvocation` (same file): carries `actual_invocation: Invocation` plus `eval_metric_results: list[EvalMetricResult]` — a LIST, meaning every metric configured for that eval run (cost AND any quality metric registered in the same `--config_file_path`) lands in the SAME per-invocation record, for the SAME invocation, under the SAME case-level `eval_id`.
- **Citation**: `google/adk/evaluation/eval_result.py`, classes `EvalCaseResult` and `EvalMetricResultPerInvocation` (installed `google-adk==2.7.0`, `.venv/Lib/site-packages/google/adk/evaluation/eval_result.py`).

### 5.2 What this enables, and the one real gap found

Since 5.1 is yes, a cost-quality Pareto comparison is data-model-feasible with **no new ADK capability needed** — a user who registers `adk_tracegauge_cost_usd` alongside a real ADK quality metric (e.g. `final_response_match_v2` or a rubric-based judge) in the same eval config gets both scores in the same `.evalset_result.json`, joinable by `eval_id`, for free. **The one real gap** (checked directly via `cli_eval`'s own `--help` output this session): `adk eval`'s CLI has **no `--model` override flag** — the model is hardcoded in the agent module's own Python definition. Running the SAME evalset across N models therefore requires N separate agent-module directories (each identical except for the `model=` argument) run as N separate `adk eval` invocations, not a single command with a model list — see HH2.2 in the design doc for the exact commands this implies.

---

## II6 — What users actually ask

### 6.1 `google/adk-python` GitHub Discussions and Issues — real questions, with links

**Searched live via GitHub's GraphQL search API this session** (`repo:google/adk-python <keywords>`, `type: DISCUSSION` and REST `search/issues`) — real hits, cited:

- ["How to track session and event costs in the ADK"](https://github.com/google/adk-python/discussions/3273) (Discussion #3273, Q&A) — fetched the actual body and top comments: *"The documentation doesn't explain how to track token usage or estimate costs... How can we build a way to track the cost of a session?"* The ADK bot's own answer recommends *"build your own ADK Plugin"* — exactly what `TraceGaugeUsagePlugin` already is.
- ["Token / Cost / Usage Tracking with the new ADK (Using Python, not the Dashboard)?"](https://github.com/google/adk-python/discussions/97) (Discussion #97, Q&A) — referenced directly inside #3273's own body as prior art on the same question.
- ["Minimising token usage"](https://github.com/google/adk-python/discussions/2811) (Discussion #2811, Q&A) — general cost/efficiency question, not a loop/redundant-call-detection request specifically.
- [Issue #3309, "Expose per-invocation cost in LlmResponse (LiteLLM and other providers)"](https://github.com/google/adk-python/issues/3309) — a real, open feature request for exactly the capability `adk-tracegauge` already provides via its own plugin.
- [Issue #5835, "Cache read write token counts"](https://github.com/google/adk-python/issues/5835) — relevant to pricing/cache-multiplier granularity, adjacent to `adk-tracegauge`'s existing cache-read modeling.

**Zero hits found** for: "which model cheaper", "model comparison cost", "cost spike", "cost increased" (HH2's Pareto/model-recommender shape), "agent stuck loop", "infinite loop agent", "redundant tool call" as a feature request (HH3.3/II3's waste-detection shape), and "regression detect" surfaced only unrelated ADK software-bug reports, not cost-regression-over-time discussion (HH3.1-2's changepoint shape). These are genuine absence findings from real, targeted searches — not proof no one has ever asked, but no evidence found despite explicit searching.

### 6.2 Claude Code cost questions in public forums

**Not independently searched live this session** — UNVERIFIED. `tracegauge`'s own `cc_baselines.json`/README already document real, first-party evidence of Claude Code cost/waste concern (the maintainer's own 75-session corpus, the B2/B3/B5 research arc referenced throughout the codebase), but that is the maintainer's own prior research, not a fresh external-forum search performed as part of this gather pass. Flagging this gap explicitly rather than presenting the existing in-repo research as if it were a new II6.2 finding.

### 6.3 Map each proposed feature to a real question, or mark "no observed demand"

| Proposed feature | Real question found? |
|---|---|
| `adk-tracegauge`'s existing cost-tracking core (already shipped) | **Yes** — Discussion #3273, #97, Issue #3309, all directly on point. |
| HH2 — cost-quality Pareto / model recommender | **No observed demand** — targeted searches for model-comparison-by-cost-and-quality returned zero relevant hits. |
| HH3.1-3.2 — changepoint/drift detection over run history | **No observed demand** — "regression detect"/"cost spike"/"cost increased" searches returned no relevant hits; the only "regression" hits were unrelated ADK software-bug reports. |
| HH3.3 — per-case outlier detection | **Not directly searched as its own term** — UNVERIFIED; closest adjacent signal is "Minimising token usage" (#2811), which is about aggregate efficiency advice, not per-case outlier flagging specifically. |
| HH3 / II3 — ADK loop/redundant-tool-call waste detection | **No observed demand** — "agent stuck loop", "infinite loop agent", "redundant tool call" as a feature request returned no relevant hits. |
| II4 — static HTML report | **Not searched** — UNVERIFIED, no targeted query run for this specific capability this session. |
| "Sub-agent attribution" / "Actions summary" (named in HH4.1, not otherwise detailed in this gather pass) | **Not searched** — UNVERIFIED, out of this pass's scope; would need its own targeted query before HH4 can honestly rank it against a demand signal. |

**This is the check the instruction asked for, and it returns mostly negative.** `adk-tracegauge`'s existing, already-shipped cost-tracking capability has real, multiply-corroborated demand. None of Phase 9's NEW proposed features (HH2's Pareto, HH3's changepoint/outliers, ADK waste detection) turned up a real user question in the searches actually run this session.
