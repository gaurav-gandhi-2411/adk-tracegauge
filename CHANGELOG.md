# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries for already-published versions are derived from this repo's actual
git history and GitHub Release notes (`gh release view <tag>`), not
invented — see each entry's linked PRs. Every entry states what changed and,
where relevant, *why* (per this project's honest-documentation convention —
see `CONTRIBUTING.md`).

## [0.6.0] — 2026-08-21

### Changed — BREAKING CI BEHAVIOR CHANGE, read before upgrading

- **`EXIT_UNDERPOWERED_PASS` (exit code 4) now fires for PAIRED mode too, not
  only two-sample.** Real hosted-model measurement
  (`docs/audit/AD2_REAL_CV_MEASUREMENT.md`, AN1) found the shipped `--mode
  auto` DEFAULT — paired mode — returns a clean `status="pass"` at only
  **37.85% power** to detect a true 10% cost regression at `n=30` (real
  measured within-case CV=0.1307 on a real `gemini-3.5-flash-lite` call, not
  a synthetic worst case), far below the 80%-power bar this exact exit code
  already existed to flag for two-sample mode since `0.5.0`. The
  `power_warning`/`min_detectable_effect_usd` mechanism was already computed
  identically for both modes (`evaluate_regression`/`evaluate_regression_paired`
  share `_below_floor_warning`) — only `RegressionCheckResult.underpowered_pass`
  restricted the exit-code escalation to `method == "two_sample"`, silently
  withholding this signal from the mode most users actually run.
  **If your CI treats `exit code == 0` as "safe, no regression" for a
  `check` run in paired mode (the default whenever a pairing key resolves),
  a run that previously exited `0` under low-power conditions will now exit
  `4`.** This is intentional — see `EXIT_UNDERPOWERED_PASS`'s and
  `underpowered_pass`'s docstrings (`_cli.py`/`_regression.py`) for the full
  reasoning, and `README.md`'s power section for what `n` paired mode
  actually needs at realistic hosted-model variance to clear 80% power.
  No fix is required if you're already treating exit code 4 as "pass with a
  caveat, read the log" per `0.5.0`'s original design — this is a widening
  of an existing, already-adopted convention, not a new one.

### Added
- **Real hosted-model CV/skew measurement**, closing the AD2.3 gap left open
  when the package's power tables were only ever validated against local
  Ollama (`docs/audit/AD2_REAL_CV_MEASUREMENT.md`, §2.6/2.7). Measured
  against a real `gemini-3.5-flash-lite` call on the identical 36-case
  evalset: across-case CV **1.2326** (+25.4% vs. Ollama's 0.9831 — worse for
  the two-sample fallback path) and within-case CV **0.1307** (-16.6% vs.
  Ollama's 0.1566 — better for the shipped paired default). The two moved in
  *opposite* directions from Ollama, not uniformly one way.
- **README's Regime B CV-sweep tables extended to CV ∈ {1.5, 2.0}** (both
  two-sample and paired), since the real measured across-case CV (1.2326)
  exceeded the table's original top row (1.0). Both modes collapse to
  near-random-chance power (2–5%) at CV≥1.5 regardless of `n` — the
  extension confirms there's no recoverable regime at high variance, not
  that one exists.
- **The `n` paired mode actually needs at real measured variance, published
  plainly**: swept `n` ∈ {50,75,100,150,200} at CV=0.1307 — 10%-effect power
  crosses 80% between `n=75` (74.75%) and `n=100` (87.20%); 25%-effect power
  is ≥98% at every swept `n`, including the shipped `min_n=30`. `min_n=30`
  re-examined again against this and kept unchanged — raising it would
  refuse every real 30–99-invocation eval set outright; the runtime
  achieved-power signal (now firing in both modes, this release) carries
  that burden instead.
- **README's power section reframed to lead with what the gate reliably
  catches** (98.00% detection of a 25% regression at `n=30`, the class of
  regression that actually happens — a model swap, a new tool call) before
  the 10%-effect limitation, and states once, plainly, that no competitor
  publishes a power number for its own gate at all.
- **Model retirement tracking** for the bundled price table
  (`src/adk_tracegauge/data/gemini_prices.json`). Two distinct classes, not
  one: full removal (`gemini-2.0-flash`, confirmed absent from Gemini's free
  `models.list()` catalog — a new automated check,
  `scripts/check_model_retirement_gemini.py`, catches this class for free,
  wired into the weekly `price-freshness.yml` workflow behind a
  `GOOGLE_API_KEY` secret that does not exist yet, so it currently SKIPS
  with a visible `::warning::` annotation rather than passing silently) and
  account-eligibility gating (`gemini-2.5-flash-lite`'s "no longer available
  to new users" 404 — genuinely not free-verifiable; documented via a new
  `new_user_availability_warning` field instead of a blanket `retired: true`,
  since an existing key predating the cutoff may still work).

## [0.5.1] — 2026-08-18

### Fixed
- **The published `0.5.0` PyPI page's README never mentioned exit code `4`
  (`EXIT_UNDERPOWERED_PASS`)** — a real, breaking CI behavior change shipped in that
  same release (see `[0.5.0]`'s own "Changed" entry below). The headline exit-code
  summary only listed `0`/`1`/`3`, and "Known limitations" never mentioned the exit-code
  consequence of the achieved-power WARNING it documents in detail. Docs-only release,
  shipped promptly rather than bundled with the next functional change: a PyPI page
  permanently missing documentation of a breaking CI-behavior change is exactly the
  failure this project's own `RELEASING.md` incident note (the `0.11.0`/`tes cost` gap
  in the sibling `tracegauge` package) warns against, and PyPI does not allow
  re-uploading a version's metadata once published — the `0.5.0` page's gap is
  permanent regardless of when this ships, so shipping it now rather than later closes
  it for every user who installs from this point forward.

## [0.5.0] — 2026-08-18

### Changed — real exit-code behavior change, read this before upgrading in CI
- **`adk-tracegauge check` can now exit `4`, a code that did not exist before this
  release.** New exit code `EXIT_UNDERPOWERED_PASS=4`: fires for two-sample mode
  specifically, when `status="pass"` AND this run's own observed variance/`n` means the
  configured practical-significance floor could not be reliably (80% power) detected —
  the exact condition the existing `WARNING` line already reported in text, now also
  visible to a CI job that only checks the exit code. **If your CI treats "any nonzero
  exit code" as failure (not just `exit != 0` meaning "no regression"), a real-variance
  two-sample run that previously exited `0` cleanly may now exit `4` and fail your
  build** — even though nothing about your workload changed and no regression was
  found. This is intentional (a "pass" at 5% power was misleading by omission — see
  below), not a bug, but it is a real behavior change worth checking your own CI
  config against before upgrading. Paired mode is unaffected (never returns exit `4`,
  even though the same underlying `power_warning` mechanism also applies to it — see
  `RegressionCheckResult.underpowered_pass`'s docstring in `_regression.py` for why
  this was deliberately scoped to two-sample only). Full exit-code table in `_cli.py`'s
  module docstring.

### Added
- **The published power grid was found to depend heavily on an assumed cost-variance
  level nobody had ever measured against real data** — replaced with a series of real
  measurements instead of one more assumption:
  - **CV-swept power table** (`scripts/measure_power_by_cv_grid.py`): both modes,
    CV ∈ {0.1, 0.2, 0.4, 0.6, 1.0} × `n` ∈ {30, 50, 100}, confidence=0.98, ≥2,000
    trials/cell, Wilson 95% CIs — replaces the single "99.22% at n=30" figure this
    README used to publish as if it applied universally.
  - **A real (not assumed) across-case CV**, measured zero-cost via a 36-case evalset
    run against local Ollama (`ollama_chat/qwen2.5:7b`) through the shipped capture
    pipeline: CV=0.983, skewness=0.742 — landing at the high end of the swept grid
    (two-sample power only 4-9% at that CV across n=30-100).
  - **A real within-case CV** (the quantity that actually governs paired mode, the
    shipped default — not the across-case CV above), measured by running the same
    36 cases TWICE: CV=0.1566. Located directly on the paired-mode power grid at
    n=30/36: **28.45%/32.25% power to detect a true 10% regression** — both far below
    80%, and about 1/3 of the previously-published unqualified figure.
  - **The above finding was independently reconciled against the originally-published
    grid, not left as an unexplained contradiction**: the two figures describe two
    different, both-legitimate noise regimes (fixed absolute dollar noise vs.
    proportional CV), not a bug in either measurement. README now publishes BOTH
    regimes' power tables side by side, labeled, with a one-sentence test for which
    applies to a given evalset. See `docs/audit/Q1A_RECONCILIATION.md`.
  - The runtime "achieved power" line (unchanged, already shipped) is correct under
    either regime, since it is computed from the caller's own real observed data every
    time, never from an assumed constant.
- Local-model representativeness (does `qwen2.5:7b`'s output-length distribution
  resemble a real hosted model's?) is flagged UNVERIFIED, with exact steps and a
  sub-cent cost estimate for a small paid flash-tier validation run, not run —
  see `docs/audit/AD2_REAL_CV_MEASUREMENT.md`.

### Not changed
- `DEFAULT_CONFIDENCE=0.98` and `MIN_N_DEFAULT=30` stay as shipped. Every finding above
  is a measurement-and-transparency change, not a new tuning decision.

Full investigation: `docs/audit/AC1_SKEW_SENSITIVITY.md`, `docs/audit/AD2_REAL_CV_MEASUREMENT.md`,
`docs/audit/Q1_WITHIN_CASE_CV.md`, `docs/audit/Q1A_RECONCILIATION.md`.

## [0.4.1] — 2026-08-17

### Fixed (documentation only)
- **`0.4.0` shipped with zero README mentions of `--agent`, `cost_by_agent`,
  or `agent_name`, despite a correct CHANGELOG entry** — permanently baked
  into `0.4.0`'s published PyPI page, since PyPI does not allow re-uploading
  a version's metadata. Added a "Scoping the gate to one agent (`check
  --agent`)" README section with real captured output from the published
  `0.4.0` artifact (a genuine two-agent regression, the correctly-passing
  flat agent, and the schema-2 `insufficient_data` backward-compat case).
- Added a short README note that Python 3.14 support (shipped `0.3.1`) was
  explicitly tested (full suite + all `examples/` clean on 3.14.4), not
  just admitted by the open-ended `requires-python` bound.
- `RELEASING.md` gained a new release-checklist step: README must document
  any user-facing command/flag before a release ships — CHANGELOG alone is
  insufficient, since it tells existing users what changed, not
  prospective users what exists. This exact incident is recorded as the
  reason the step exists.

No code changes in this release.

## [0.4.0] — 2026-08-17

### Added
- **Sub-agent cost attribution (`agent_name`).** `CapturedCall` now records
  which ADK agent made each priced call (sourced from
  `callback_context.agent_name` inside `after_model_callback` — the one hook
  proven to fire through every integration path this package supports,
  including `adk eval`; deliberately not `invocation_context.agent.name`,
  which never fires there). Surfaced in three places:
  - the `check`/eval rationale text now prefixes each call line with
    `agent=<name>`;
  - `adk-tracegauge snapshot` records a new `cost_by_agent: dict[str, float]`
    field per invocation;
  - `adk-tracegauge check --agent <name>` scopes the regression gate to one
    agent's own cost (works in both two-sample and paired mode).

  Tested against the real two-agent `AgentTool` delegation case, agents
  sharing a name (documented collapsed behavior, not a crash), a
  three-level nested delegation chain, and the single-agent backward-compat
  case. Verified from a fresh wheel install against google-adk 2.7.0.

### Changed
- **Snapshot `schema_version` bumped 2→3** for the new `cost_by_agent`
  field. **Backward compatible, plainly stated:** every snapshot file
  written by 0.3.x (`schema_version` 1 or 2) still reads correctly under
  0.4.0 — `cost_by_agent` simply defaults to `{}` for every record in an
  old file (the same additive-field pattern this package has used for every
  prior schema bump: `session_id` and `eval_case_id` before it). The
  concrete effect for someone mid-comparison across the upgrade: a baseline
  snapshot captured on 0.3.x and a current snapshot captured on 0.4.0 still
  compare correctly under plain `adk-tracegauge check` (unscoped) — nothing
  changes there. The one thing that does NOT work across that mix is
  `check --agent <name>` against the OLDER (0.3.x-captured) side: since that
  snapshot has no `cost_by_agent` data at all, every record reports zero
  cost for any agent, which `--agent` correctly reports as
  `insufficient_data` rather than fabricating a comparison against absent
  data (see `tests/test_sub_agent_attribution.py::test_cli_check_agent_flag_on_old_schema_file_reports_zero_cost_not_a_crash`).
  Re-running `adk-tracegauge snapshot` on 0.4.0 for BOTH sides resolves
  this — a one-time re-capture, not a required migration for anyone not
  using `--agent`.

## [0.3.2] — 2026-08-16

### Added
- **`adk-tracegauge quickstart`**: a zero-config, one-command demo. No ADK app of
  your own, no API key, no live model call, no files to create. Runs a
  deterministic, in-memory demo agent (bundled with the package — nothing
  read from the user's machine) through a real `InMemoryRunner`, twice, with
  a deliberate cost regression injected into the second run, then fires the
  real `adk-tracegauge check` gate against it. Reuses the exact,
  already-proven mechanism `examples/05_hand_rolled_session_id_pairing.py`
  demonstrates (a deterministic fake `BaseLlm` + `TraceGaugeUsagePlugin` via
  the documented `after_model_callback` wiring), packaged as an installed
  console subcommand instead of a script requiring the repo cloned. Measured
  live from a genuine fresh Windows user-site install: 78.2s wall-clock,
  well under the 5-minute target. README now leads with this command.

  This release exists specifically to get the command onto PyPI:
  `0.3.1`'s published wheel (verified by downloading and inspecting the
  actual artifact from `files.pythonhosted.org`, not the repo) does not
  contain it — the command was merged into `main` after `0.3.1` was already
  tagged and published.

## [0.3.1] — 2026-08-16

### Fixed
- **GG's first-run failure: `adk-tracegauge --help` raised `CommandNotFoundException` on
  Windows.** Root cause: a default (non-venv) `pip install` performs a user-site install,
  and the console script lands in a per-user `Scripts` directory Windows does not add to
  PATH by default. Added `src/adk_tracegauge/__main__.py` so `python -m adk_tracegauge`
  always works regardless of PATH (only needs `python` itself on PATH, which every Python
  install guarantees). README and the adk-docs integration page now state this fix and the
  PATH workaround directly in the install step, not buried in troubleshooting.
- **Python 3.14 support**: `requires-python` had no upper bound but was untested past 3.13.
  Verified clean on Python 3.14.4 in an isolated venv — full 395-test suite and all 5
  `examples/` scripts pass, against both `google-adk==2.6.3` and `google-adk==2.7.0` (the
  two versions the unpinned `>=2.6.0,<2.8.0` range admits). No code changes required. Added
  to the CI matrix and `Programming Language :: Python :: 3.14` classifier.
- **Corrected a published-but-never-significance-tested claim: paired mode's FPR was
  reported as exceeding two-sample's at 4 of 6 grid cells (Phase 7 U2,
  `reports/confidence_grid_u2.json`) — an audit found this comparison was never actually
  tested for significance before being written up, and does not hold up when tested: a
  two-proportion z-test on the ORIGINAL grid's own counts finds no cell significant
  (largest z=1.80, p=0.07), and an independent 5,000-trial re-measurement with a new seed
  base confirms the ranking does not reproduce (largest z=1.29, p=0.20). No code defect
  was found in either mode's bootstrap implementation or null-data generator — both
  modes instead show the SAME already-documented small-`n` percentile-bootstrap
  anti-conservatism, at comparable magnitude, not a paired-specific issue.
  `scripts/measure_regression_confidence_grid.py`'s `N_TRIALS` raised 2,000 → 5,000
  (demonstrated, not assumed, to stabilize the cross-mode comparison) and a
  `two_proportion_z_test` significance check added to the harness so this gap can't
  recur silently. Corrected figures in `README.md` and
  `_regression.py`'s `DEFAULT_CONFIDENCE` docstring. See `docs/audit/FPR_ANOMALY.md` for
  the full investigation. No shipped default (`DEFAULT_CONFIDENCE`, `min_n`, mode
  auto-selection) was changed — this is a documentation/measurement-methodology
  correction, not a behavior change.

## [0.3.0] — 2026-08-15

Phases 2–7 combined (`feat/cost-regression-gate` branch), merged into
`main` via PR #6, with PR #7 fixing a version-single-source bug found
pre-release (see that PR's own entry below and `docs/audit/RELEASE_0_3_0.md`
for the full incident). `src/adk_tracegauge/__init__.py`'s `__version__` is
now the single source of truth for the package version (Phase 6 bumped it;
`pyproject.toml` derives its version dynamically from it since PR #7). This
entry consolidates the full branch, not just whatever happened to still be
sitting under `[Unreleased]` at the end — cross-checked against
`git log main..HEAD` and all phase reports
(`docs/audit/PHASE{1,2,3,4,5,6,7}_REPORT.md`) so nothing significant is
missing.

### Changed (Phase 7 U1)
- **Behavior-affecting: `adk-tracegauge check`'s default `--mode auto` now
  PREFERS `paired` over `two-sample`.** Previously, `auto` picked paired only
  as an opt-in bonus once enough pairing keys happened to overlap; paired is
  now the preferred path whenever a pairing key (`eval_case_id`, preferred,
  via `--eval-history`; else `session_id`) resolves with overlap `>=
  --min-n` — `two-sample` is the fallback, used only when no key resolves or
  the overlap is below that bar. The auto-selection threshold itself is
  UNCHANGED (`--min-n`, default 30) — re-examined explicitly, not lowered:
  a full 20-cell paired-mode power grid (`scripts/measure_paired_power_grid.py`,
  `n` ∈ {10, 25, 50, 100} × effect ∈ {0, 5, 10, 25, 50}%, 1,000 trials/cell,
  confidence=0.98) found paired mode's own false-positive rate is *higher*,
  not lower, than two-sample's at every measured `n` (e.g. 4.1% vs. 2.2% at
  `n=10`) — paired is dramatically more powerful at a given `n` (97.8% vs.
  51.4% detection at `n=25`/10%-effect), not more reliable, so there is no
  statistical basis for trusting it at a smaller `n` than two-sample itself
  requires. `--mode paired` requested explicitly with insufficient overlap
  still fails loud (`SystemExit`, unchanged, Phase 4 R2) rather than
  silently downgrading. The resolved mode and key (or the specific reason
  two-sample was used instead — "no pairing key available" vs. "a key
  resolved but too few pairs", now distinguished in the printed message) are
  always printed on every run. See `PLAN.md`'s Phase 7 U1 entry for the full
  grid, measured overlap rate on a real evalset, and the fresh-wheel
  end-to-end proof.

### Verified unchanged (Phase 7 U2)
- **`DEFAULT_CONFIDENCE` re-examined now that paired mode is the DEFAULT
  `--mode auto` preference (U1), and CONFIRMED to stay at `0.98`** — Phase 5
  S4's original tuning used two-sample data only, before paired-by-default
  existed. Re-measured the deciding cells (confidence ∈ {0.95, 0.98, 0.99} ×
  `n` ∈ {30, 50} × true effect ∈ {0%, 10%, 25%}) at 2,000 trials/cell (vs.
  S4/T4's 500) with real Wilson score CIs on every cell, for BOTH modes side
  by side (`scripts/measure_regression_confidence_grid.py`, 72,000 total
  simulated bootstrap evaluations, 902.8s wall-clock). Finding: paired
  mode's power for a 10% effect is already near-ceiling at 0.98 and barely
  moves at 0.99 (99.45% → 98.80% at n=30; 100.00% → 100.00% at n=50) — no
  real headroom to buy by tightening further on the now-default path.
  Two-sample's power drops sharply over the same tightening (57.80% →
  49.10% at n=30; 81.25% → 74.20% at n=50) and crosses BELOW the project's
  own 80%-power "reliable detection" bar at n=50 — reproducing S4's original
  criterion-2 rejection of 0.99, on the fallback path that is still real and
  live whenever no pairing key resolves. Since one `DEFAULT_CONFIDENCE` is
  shared by both modes, tightening it would optimize for the path that needs
  it least at the expense of the path that needs it most — value unchanged,
  reasoning now paired-mode-aware. See `_regression.py`'s `DEFAULT_CONFIDENCE`
  docstring ("Phase 7 U2, 2.3") and `PLAN.md`'s Phase 7 U2 entry for the
  full 18+18-cell grid and reasoning; `README.md`'s "Known limitations"
  audited so every power/FPR/detection-rate figure in the doc now states its
  trial count and a Wilson 95% CI, not a bare point estimate.

Per this project's own 0.x convention (established at the 0.1.0 → 0.2.0
"honest repositioning" bump): the middle number is where a breaking API
change lands while still pre-1.0, not the first (SemVer's major-version-zero
clause: "anything MAY change at any time," but this project still signals
intent through the middle digit rather than treating every 0.x release as
equally unstable). Not `1.0.0` — the public API is still young and marked
Alpha.

### Added
- `CostThresholdCriterion(BaseCriterion)` — a real, required max-USD-per-invocation
  threshold for `CostEfficiencyEvaluator`, with the opposite comparison
  direction from ADK's built-in `>=` convention (`PASSED` iff `cost <= threshold`).
- Claude and current-generation GPT price-table entries (`claude-opus-5`,
  `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`, `gpt-5.6-sol`,
  `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.1`, `gpt-5`), reached through
  ADK's `LiteLlm` integration.
- Local/self-hosted model support (Ollama, vLLM prefixes) — resolves to a
  real, explicit zero-cost price-table entry, not a bypass, but **only
  after an explicit `ADK_TRACEGAUGE_ASSUME_LOCAL` opt-in** (Phase 3 B1):
  Ollama Cloud, a real paid product, shares the identical `ollama_chat/`/
  `ollama/` LiteLlm prefix with local Ollama, and nothing this package
  captures can tell the two apart, so a recognized prefix alone is no
  longer sufficient — without the opt-in, a local-prefixed call reports
  `NOT_EVALUATED` with an actionable message, never a silently-possibly-wrong
  `$0.00`.
- `ADK_TRACEGAUGE_PRICE_TABLE` environment variable for registering a
  custom/override price table.
- Promotional/introductory pricing now expires automatically, not silently
  (Phase 3 B2): price-table entries can carry `promo_until` (ISO date) and
  a published `standard_rate`; the resolver switches to `standard_rate` on
  its own once `promo_until` passes, and the per-call rationale states
  plainly whether a promo is still active (with its expiry date) or has
  already ended. If a promotional entry's post-promo rate isn't published
  yet, this package now warns loudly starting 14 days before expiry rather
  than silently freezing at a rate that may no longer apply.
- `.github/workflows/price-freshness.yml` — a weekly, schedule-triggered CI
  job (not just push-triggered) that fails if any price entry is stale
  (`is_stale`, `STALE_THRESHOLD_DAYS`) or has a promotional rate expiring
  within 14 days / already expired (Phase 2 W1, extended Phase 3 B2.4).
- `adk-tracegauge check --mode {auto,two-sample,paired}` (Phase 3 B4,
  re-keyed Phase 4 R2): a paired-comparison bootstrap, keyed on
  `EvalCase.eval_id` (recovered post-hoc via `adk-tracegauge snapshot
  --eval-history <path>`, joining ADK's own persisted
  `*.evalset_result.json`) with a fallback chain to `session_id` then
  plain `two-sample` — measured dramatically more sensitive than
  two-sample at the same `n` whenever real per-case cost variance exists
  (0/200 vs. 200/200 detection on a case-correlated +10%-effect fixture at
  `n=25`). `auto` (the default) picks paired automatically whenever enough
  pairing keys overlap; the resolved mode and key are always printed, never
  silently chosen. See README "Known limitations" for the full measured
  power comparison and honest caveats (paired's own FPR at this `n`, 5.5%,
  is flagged as worth a larger confirmatory run).
- Real-time "achieved statistical power" reporting on every
  `adk-tracegauge check` run (Phase 4 R4): the minimum effect size the
  bootstrap test could reliably (80% power) detect given THIS run's own
  observed variance and `n` — printed unconditionally, pass or fail or
  insufficient-data — plus an explicit `WARNING` whenever the configured
  `--min-effect-usd`/`--min-effect-pct` floor is smaller than that
  achievable floor. A normal-approximation to the (closed-form-free)
  bootstrap CI power, validated against a real measured power grid to
  within 2–8 percentage points; see `_regression.py`'s "Achieved
  statistical power" section for the full derivation and accuracy table.
- A real runtime `warnings.warn` (Phase 3 B3) firing when this metric is
  evaluated under a real `AgentEvaluator.evaluate()` call — names the
  known ADK-side polarity bug (see "Known limitations" below) and the
  installed `google-adk` version, so the caveat isn't documentation-only.
  Known gap, documented: the very first `AgentEvaluator.evaluate()` call in
  a process can miss the warning if `adk_tracegauge` is imported for the
  first time as a side effect of that same call.
- `.github/workflows/ci.yml` gained a wheel-only install smoke-test job
  (Phase 4 R7): builds the real wheel, installs it into a fresh venv with
  no repo on `sys.path`, and runs the literal installed `adk-tracegauge`
  console script from a directory outside the repo — catches the class of
  bug ("works in my editable dev checkout, breaks for a real user") that a
  test suite run against the repo checkout alone cannot.
- `adk-tracegauge` console script with two subcommands: `adk-tracegauge snapshot`
  (persist a `UsageStore`'s priced invocations to JSON) and `adk-tracegauge check`
  (percentile-bootstrap cost-regression gate against a baseline, real exit
  codes 0/1/3) — the CI differentiator this package is now positioned
  around. See `docs/ci-snippet.md` for a full GitHub Actions workflow. Named
  `adk-tracegauge`, not the bare `tracegauge` originally used during
  development (Phase 6 T3): the sibling `tracegauge` PyPI package
  (`token-efficiency-scorer`) already installs a console script under that
  exact name, and whichever package installed second would silently
  clobber the other's executable. `0.2.0` (the last published release)
  shipped no console script at all, so this is a new capability, not a
  breaking rename of anything a real user ever depended on.
- `adk_tracegauge._compat.convert_events_to_eval_invocations` — a
  version-guarded wrapper around ADK's private
  `EvaluationGenerator.convert_events_to_eval_invocations` internal, used
  only by the optional hand-rolled sub-agent-rollup harness (not the
  primary `adk eval` quickstart path, which never calls it).
- `examples/` directory: four runnable, independently-verified scripts
  covering the quickstart, sub-agent cost rollup, the CI regression gate
  end to end, and paired-mode against the real `adk eval` CLI (Phase 4 R2).
- `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/troubleshooting.md`, and GitHub
  issue templates (bug report, price-table correction).
- CI test matrix across Python 3.10–3.13 (previously only 3.11 was
  exercised despite `requires-python = ">=3.10"` claiming broader support).
- A GitHub Release is now created automatically after every successful
  PyPI publish (previously: tags only, no Release notes).

### Changed
- **Behavior-affecting: `adk-tracegauge check`'s default `--confidence` tightened `0.95` → `0.98`
  (one-sided alpha `0.025` → `0.01`), Phase 5 S4.** Measured, not guessed: the real
  shipped-configuration false-positive rate at `min_n=30` under the OLD default was
  ~4.4% (combined across two independent 500-trial runs, real `n_boot=10,000`,
  real practical-significance floors) — well above the ~2.5% nominal one-sided
  expectation and too high for a CI gate whose whole value proposition is being
  trustworthy (a ~1-in-23 false-alarm rate on every clean run trains users to
  ignore or disable it). A full 90-cell grid (one-sided alpha × `n` × true effect,
  ≥500 trials/cell, `scripts/measure_regression_alpha_grid.py`) was measured to
  choose the new default: `confidence=0.98` cuts the real shipped FPR to ~2.3%
  combined (within sampling noise of the ~2% target) while keeping detection
  power for a realistic 10% cost regression at `n=50` at 83.4% — still above this
  project's own 80%-power "reliable detection" bar (`ACHIEVED_POWER_TARGET`).
  `confidence=0.99` was considered and rejected — it drives FPR even lower (1.6%)
  but drops that same `n=50`/10%-effect power to 76.2%, below the 80% bar. Any
  caller not overriding `--confidence` explicitly will see a real change in
  behavior: slightly wider bootstrap CIs, slightly fewer false-positive
  `status="regression"` verdicts, and slightly lower detection power at small
  effect sizes. Callers who want the old behavior back can pass `--confidence
  0.95` explicitly. See `_regression.py`'s `DEFAULT_CONFIDENCE` docstring and
  `PLAN.md`'s Phase 5 S4 entry for the complete 90-cell grid and rationale.
- **Breaking:** `CostEfficiencyEvaluator` now requires a max-USD-per-invocation
  threshold at construction time (`criterion=CostThresholdCriterion(...)`,
  preferred, or the deprecated `EvalMetric.threshold=`) — raises `ValueError`
  if neither is set. Previously, constructing it without a threshold
  succeeded, but the metric always reported `NOT_EVALUATED` regardless
  (the Phase 1 P0 finding: `AgentEvaluator.evaluate()` raised
  `AssertionError` unconditionally whenever this metric was registered).
  Any caller relying on the old always-`NOT_EVALUATED` behavior must now
  supply a real threshold.
- Long-context tiering (>200k token prompts) is now modeled for
  `gemini-2.5-pro` and `gemini-3.1-pro-preview` (price-table
  `schema_version` 2) — previously unmodeled, a real undercount for
  long-context calls.
- `thoughts_token_count` (Gemini "thinking" tokens) is now billed as
  output — previously silently dropped, undercounting reasoning-heavy
  calls.
- `tool_use_prompt_token_count` (server-side built-in tool tokens, e.g.
  Google Search grounding) now refuses to price a call rather than
  silently ignore billed tokens — no verified billing rate exists for
  this category.
- `gemini-3.6-flash`'s price corrected from the post-promotion rate to the
  actual rate in effect through 2026-12-31 (was mispriced by 2x).
- `STALE_THRESHOLD_DAYS` tightened from 180 to 90, after a live finding
  that a promotional rate on a fixed calendar-date schedule was
  stale-by-construction under the old window.
- `google-adk[eval]` pin widened `<2.7.0` → `<2.8.0`, after confirming the
  full test suite passes clean against the live `2.7.0` release with zero
  code changes required.
- `unknown_model_message` rewritten to name the exact failing model,
  distinguish "should have auto-resolved as local" from "routed through a
  platform whose pricing can diverge" from "genuinely unknown," and point
  at the `ADK_TRACEGAUGE_PRICE_TABLE` mechanism.
- **(Phase 4 R5) The `tracegauge` PyPI dependency is removed entirely.**
  An audit found it was used for exactly one thing across this whole
  package (~55 lines of dollar-cost arithmetic plus two internal-only
  dataclasses, grep-confirmed — none of `tracegauge`'s own actual
  differentiators were ever touched); that arithmetic is now ported
  in-house at `src/adk_tracegauge/_cost.py` (behavior-preserving — proven
  via the full existing test suite plus hand-computed spot checks). No
  observable behavior change for any caller. See `_cost.py`'s module
  docstring for the full audit and license-attribution note.

### Fixed
- 3 Gemini models missing from the price table entirely
  (`gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`)
  — invocations on these models previously reported `score=None`
  unconditionally.
- `datetime.UTC` (Python 3.11+-only) replaced with `timezone.utc`
  (stdlib since 3.2) in `snapshot.py` — a real `ImportError` on Python
  3.10 despite `requires-python = ">=3.10"`, caught by adding the 3.10 CI
  leg in this same work item.
- `[tool.ruff] target-version` corrected `"py311"` → `"py310"`, so ruff's
  pyupgrade rules stop suggesting 3.11+-only syntax on a package that
  claims 3.10 support.
- 2 shallow `is not None` test assertions in `test_registration.py`
  strengthened to real identity/type checks.
- **(PR #7) Version single-source bug, caught pre-release by a release gate.**
  PR #6's squash-merge bumped only `pyproject.toml`'s `version = "0.3.0"`,
  leaving `src/adk_tracegauge/__init__.py`'s `__version__` stale at
  `0.2.0` — two independently hand-maintained literals with no mechanism
  keeping them in sync. `0.3.0` had not been tagged/published yet, so this
  was fixable pre-release. Fix: `pyproject.toml` now declares
  `dynamic = ["version"]`, resolved from `__init__.py`'s `__version__` via
  setuptools' static-AST `attr =` reader at build time — `__init__.py` is
  now the single source of truth. New guard test
  `tests/test_version_consistency.py` asserts installed distribution
  metadata and the runtime `__version__` attribute always agree. See
  `docs/audit/RELEASE_0_3_0.md` for the full incident writeup.

## [0.2.0] — 2026-08-14

["Honest repositioning plus two correctness fixes"](https://github.com/gaurav-gandhi-2411/adk-tracegauge/releases/tag/v0.2.0)
([PR #4](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/4),
[PR #5](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/5)).

### Changed
- README repositioned to lead with the package's real, honest limitation
  (`AgentEvaluator.evaluate()`/`adk eval` could not surface this metric's
  output — the P0 defect Phase 2 W2 later fixed) instead of overstating
  zero-config integration.
- Two correctness fixes to the cost-computation path (see the release's
  linked PR for specifics).

### Added
- `RELEASING.md`, documenting the `0.1.0rc1`/`0.1.0` automated release
  process end to end.

## [0.1.0] — 2026-08-13

[Version bump from `0.1.0rc1`](https://github.com/gaurav-gandhi-2411/adk-tracegauge/releases/tag/v0.1.0)
([PR #3](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/3)) — no
functional changes from `0.1.0rc1`, dropping the release-candidate suffix
after the RC was confirmed working.

## [0.1.0rc1] — 2026-08-13

[Initial release](https://github.com/gaurav-gandhi-2411/adk-tracegauge/releases/tag/v0.1.0rc1)
([PR #1](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/1),
[PR #2](https://github.com/gaurav-gandhi-2411/adk-tracegauge/pull/2)).

### Added
- `TraceGaugeUsagePlugin` and `CostEfficiencyEvaluator` — the initial
  per-invocation cost-in-USD evaluator for custom Google ADK eval
  harnesses, built on `tracegauge`'s `tes.cost` engine.
- Bundled Gemini price table (`src/adk_tracegauge/data/gemini_prices.json`).
- PyPI Trusted Publishing (OIDC) via `release.yml` — no API token/password
  in any workflow.
- Structural pricing guard (`tests/test_pricing_call_site.py`, asserting
  `compute_session_cost` has exactly one sanctioned call site), staleness
  detection, and a real end-to-end test.
