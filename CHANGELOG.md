# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries for already-published versions are derived from this repo's actual
git history and GitHub Release notes (`gh release view <tag>`), not
invented — see each entry's linked PRs. Every entry states what changed and,
where relevant, *why* (per this project's honest-documentation convention —
see `CONTRIBUTING.md`).

## [Unreleased]

Phase 2 work (`feat/cost-regression-gate` branch) — not yet published to
PyPI, not yet tagged. `pyproject.toml`'s version remains `0.2.0` until this
ships. Recorded here now so the next release's notes don't have to be
reconstructed after the fact.

**Proposed next version: 0.3.0** (not 1.0.0 — this package's public API is
still young and marked Alpha; not a patch release either — see "Changed"
below for the breaking change that rules that out). Per this project's own
0.x convention (established at the 0.1.0 → 0.2.0 "honest repositioning"
bump): the middle number is where a breaking API change lands while still
pre-1.0, not the first (SemVer's major-version-zero clause: "anything MAY
change at any time," but this project still signals intent through the
middle digit rather than treating every 0.x release as equally unstable).

### Added
- `CostThresholdCriterion(BaseCriterion)` — a real, required max-USD-per-invocation
  threshold for `CostEfficiencyEvaluator`, with the opposite comparison
  direction from ADK's built-in `>=` convention (`PASSED` iff `cost <= threshold`).
- Claude and current-generation GPT price-table entries (`claude-opus-5`,
  `claude-sonnet-5`, `claude-haiku-4-5`, `claude-opus-4-8`, `gpt-5.6-sol`,
  `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.1`, `gpt-5`), reached through
  ADK's `LiteLlm` integration.
- Local/self-hosted model support (Ollama, vLLM prefixes) — resolves to a
  real, explicit zero-cost price-table entry, not a bypass.
- `ADK_TRACEGAUGE_PRICE_TABLE` environment variable for registering a
  custom/override price table.
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
- `examples/` directory: three runnable, independently-verified scripts
  covering the quickstart, sub-agent cost rollup, and the CI regression
  gate end to end.
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
