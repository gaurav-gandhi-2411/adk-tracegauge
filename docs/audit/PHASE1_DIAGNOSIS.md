# adk-tracegauge / oss-contrib — Phase 1 Diagnosis

Read-only audit. No code, config, git, or PyPI changes were made anywhere in this pass.
Every claim below is tagged `[VERIFIED: command — output]` or `[UNVERIFIED: reason]`.
Audit date basis: 2026-08-14.

---

## EXECUTIVE SUMMARY

1. **P0 defects: 1.** `adk eval` / `AgentEvaluator.evaluate()` — the package's own documented primary integration path — reliably raises `AssertionError` whenever the adk-tracegauge cost metric is registered, and the `adk eval` CLI path records the per-invocation score as `None`. Confirmed by direct source read this session (not just the author's own claim).
2. **Does it work with the current google-adk release? YES**, confirmed independently twice (original thread + this session, re-run live): `google-adk==2.7.0` (current PyPI) + `adk-tracegauge==0.2.0` imports and instruments cleanly, despite the package's own pin (`<2.7.0`) technically excluding 2.7.0. The pin is stale, not broken — but the repo's own canary CI built to catch this drift has never run.
3. **Competitive verdict: no material differentiation.** ADK ships full native OTel tracing (spans, token counts incl. cached/reasoning tokens) with zero code beyond env vars to export to any OTLP backend. Phoenix, Langfuse, AgentOps, Weave, and LangSmith all have current, documented ADK integrations, are largely free/self-hostable, and Phoenix already ships a free eval/regression-gating framework. adk-tracegauge's one real, unique contribution — a maintained Gemini USD price table with recursive sub-agent cost rollup — is a ~20-line multiplier a developer could write themselves against fields ADK/Phoenix already expose, and the package's own README explicitly disclaims being a tracing tool at all.
4. **Top 3 roadmap features** (of 10 proposed, ranked by differentiation × pain / cost): (1) fix the broken eval-integration gate — prerequisite for everything else, (2) OTel-native export so the package composes with Phoenix/Langfuse instead of duplicating them, (3) a CI regression gate (`tracegauge check --baseline`) with bootstrap-CI drift detection on cost/latency/tool-success — the one capability no competitor currently ships for ADK.
5. **ROUTE-TO-GG: effectively empty.** No PR is blocked on a 2FA-gated or browser-only action. One soft item (resolving a keras-team/keras review thread — may be permission-gated to maintainers, untested since it's a write action) and one judgment call (whether/when to nudge upstream maintainers on 4 CI-green, unreviewed PRs, none older than 2 days).
6. `oss-contrib` is not a single git repo — it's a workspace of 5 independent clones (adk-docs, adk-python, adk-python-verify, keras, keras-verify). The task brief assumed one repo; this is flagged as a premise correction, not silently guessed past.
7. Repo hygiene: adk-tracegauge tree is clean, `main` in sync with origin, version 0.2.0 matches PyPI exactly, PyPI trusted publishing is OIDC-only (no token/password anywhere), no branch protection on `main`.
8. Test suite: 67/67 passing, 99% coverage, substantively behavioral (133/135 real assertions, 2 shallow not-None checks). Runtime varies run-to-run (49.25s first run, 20.52s independent re-run) — not a stable metric, both real.
9. Adoption friction scores are low across the board: time-to-first-value 1/5, README quality 1/5, docs-beyond-README 0/5, discoverability 0/5, zero-config path 1/5.
10. 6 oss-contrib PRs found across 3 repos (google/adk-python ×4, keras-team/keras ×1, google/adk-docs ×1): 5 open and CI-green, 1 closed (self-superseded, correctly resolved). None stalled on unaddressed feedback or merge conflicts.
11. One cross-reference found: google/adk-docs#2128 (the tracegauge catalog PR) links to google/adk-python#6725, a design-question issue GG filed as a byproduct of building the integration — informational, non-blocking, not a PR-to-PR dependency.
12. A verifier pass independently re-ran the 5 highest-stakes claims. Two came back exactly confirmed (PyPI trusted-publishing config, branch-protection 404). One (google-adk 2.7.0 compatibility) initially timed out on re-install but was independently re-confirmed live by this session directly. One (test runtime) showed real run-to-run variance, not a substantive error. One (the `local_eval_service.py`/`AgentEvaluator` source citation) was genuinely wrong on specifics — wrong line numbers, and "discards results" overstates what the code does (it records `score=None`, doesn't remove the entry) — but re-reading the source directly (this session) confirms the underlying practical claim: `AgentEvaluator.evaluate()` does raise whenever this metric's status resolves to `NOT_EVALUATED`, because the failure-classification logic treats `NOT_EVALUATED != PASSED` as a failure. See "Verification & Corrections" after §3.
13. No account-migration drift found: all git config emails and authored-commit emails across all 6 repos audited are the canonical `gaurav.gandhi2411@gmail.com`; no GCP project ID strings found.
14. Minor local hygiene items only, no urgency: stale `dist/` build artifacts from v0.1.0 (gitignored), 5 already-merged local branches not cleaned up, oss-contrib's `adk-python` checkout behind its own fork on 3 of 4 branches (someone used GitHub's "Update branch" button without a local pull).
15. Full detail, evidence, and the complete defect table follow below.

---

## SECTION 1 — REPO + RELEASE STATE

### Preliminary finding (premise check)

[VERIFIED: `Get-ChildItem -Force 'C:\Users\gaura\ml-projects\oss-contrib'` — contains `adk-docs`, `adk-python`, `adk-python-verify`, `keras`, `keras-verify` (dirs) and `CLAUDE.md`; no `.git` at the root.]

**`oss-contrib` is not itself a git repository** — it is a workspace of five independent git repos:
- `adk-docs`, `adk-python`, `keras` — the user's own forks (origin = `gaurav-gandhi-2411/*`, upstream = the respective Google/Keras repo)
- `adk-python-verify`, `keras-verify` — clean upstream-only checkouts (origin = `google/adk-python` / `keras-team/keras` directly), used for reproduction against a clean tree

All checks below are reported per sub-repo — a deviation from the task's literal single-repo framing, made necessary by the discovered structure.

### 1.1 — git status / log / branches / unpushed commits

**adk-tracegauge:**
[VERIFIED: `git status` → "On branch main. Your branch is up to date with 'origin/main'. nothing to commit, working tree clean"]
[VERIFIED: `git log --oneline -15` → 10 commits total in history, most recent: `33b414a chore(release): bump version to 0.2.0 -- honest repositioning plus two correctness fixes (#5)`]
[VERIFIED: `git branch --show-current` → `main`; `git log @{u}.. --oneline` → empty (no unpushed commits); `git status --porcelain=v1 -uall` → empty (no uncommitted/untracked files)]

[VERIFIED: `git branch -v`] 5 non-`main` local branches: `chore/0.1.0-release`, `chore/0.2.0-release`, `chore/rc1-version-bump`, `ci/pypi-trusted-publishing`, `docs/releasing`. [VERIFIED: cross-referenced against `main`'s own merge-commit history] All 5 correspond to already-merged PRs (#1–#5). Two show "1 ahead" only because their PRs were squash-merged under a new SHA, not unmerged work [UNVERIFIED: exact diff-emptiness of those two not independently confirmed via `git diff`]. **All 5 are deletion candidates**, pending explicit confirmation (read-only mandate, nothing deleted).

**oss-contrib sub-repos:**
- `adk-docs`: clean, 2 own working branches (`main`, `docs/adk-tracegauge-integration`), neither stale. 260 `origin/*` / 259 `upstream/*` branches are the mirrored upstream fork branch set — not the user's own, not individually audited (out of scope).
- `adk-python`: **local checkout is behind its own `origin` fork by ~40–96 commits on 3 of 4 open-PR branches** [VERIFIED: `git log <branch>..origin/<branch> --oneline` per branch] — someone used GitHub's "Update branch" button without a local `git pull`. Does not affect the branches' own PR mergeability directly [UNVERIFIED: not checked via `gh pr view --json mergeable` at the git level, but confirmed via `gh pr view` JSON in §5.2 that all are `MERGEABLE`].
- `adk-python-verify`, `keras-verify`: clean, single branch, in sync, no PRs opened from these (upstream-only origin).
- `keras`: clean, 2 branches (`master`, `fix/r2score-zero-variance-nan`), neither stale; 51 mirrored `origin/*` branches not audited (out of scope, same reasoning as adk-docs).

### 1.2 — adk-tracegauge file tree + line counts

[VERIFIED: `git ls-files` + `find`] Tracked: `LICENSE`, `README.md`, `RELEASING.md`, `pyproject.toml`, `uv.lock`, `.github/workflows/{ci,pypi-canary,release}.yml`, `src/adk_tracegauge/{__init__,_adapter,_plugin,_pricing,_store,evaluator}.py`, `src/adk_tracegauge/data/gemini_prices.json`, `tests/test_{adapter,e2e_runner,evaluator,integration,plugin,pricing,pricing_call_site,registration,store}.py`.

Source line counts [VERIFIED: `wc -l src/adk_tracegauge/*.py`]: `__init__.py` 48, `_adapter.py` 160, `_plugin.py` 112, `_pricing.py` 109, `_store.py` 110, `evaluator.py` 261 — **800 total**.
Test line counts [VERIFIED: `wc -l tests/*.py`]: **1,121 total** across 9 files.

**Finding:** local (gitignored, untracked) `dist/` holds stale v0.1.0 build artifacts despite `main` being at 0.2.0 — cosmetic only, doesn't affect the published package.

### 1.3 — pyproject.toml (verbatim)

[VERIFIED: direct Read]
- Name: `adk-tracegauge`, Version: `0.2.0`, requires-python: `>=3.10`
- Dependencies (compatible-release range pins): `google-adk[eval]>=2.6.0,<2.7.0`, `tracegauge>=0.10.0,<0.11.0` (the `[eval]` extra is load-bearing, not optional — `google-adk`'s `metric_evaluator_registry.py` unconditionally imports every built-in evaluator)
- Dev deps (range-pinned): ruff, mypy, pytest, pytest-asyncio, pytest-cov, pytest-mock
- No optional-extras block. Classifiers cover Python 3.10–3.13, Alpha status, QA/Utilities topics.
- Project URL: only `Repository` (no Homepage/Docs/Issues/Changelog)
- License: Apache-2.0 (SPDX). Author: Gaurav Gandhi, canonical email. No console scripts/entry points. Build backend: `setuptools.build_meta`.

### 1.4 — PyPI live state (public JSON API)

**adk-tracegauge:** [VERIFIED: `curl -s https://pypi.org/pypi/adk-tracegauge/json` → HTTP 200]
- Published versions: `0.1.0rc1`, `0.1.0`, `0.2.0` (uploaded 2026-08-13/13/14 respectively). All `yanked: false`.
- `requires_python: >=3.10`; `requires_dist` matches local pyproject.toml exactly.
- `description_content_type: text/markdown` (README renders). Both wheel + sdist present for 0.2.0.
- **Version diff: local (0.2.0) = PyPI latest (0.2.0). Match, no drift.**

**tracegauge** (separate dependency package, also user-published): [VERIFIED: HTTP 200] versions `0.1.0` through `0.10.1` (10 releases, latest `0.10.1`), all unyanked. 4 of 10 releases (`0.3.1`, `0.7.0`, `0.7.1`, `0.8.0`) are **wheel-only, no sdist**. Not directly comparable to adk-tracegauge's version (independent package).

### 1.5 — GitHub state (`gh` CLI)

**adk-tracegauge:** [VERIFIED: `gh repo view`] Public, Apache-2.0, **no GitHub topics set** (`repositoryTopics: null`). [VERIFIED: `gh release list`] **Empty — no GitHub Release objects despite 3 git tags** (`v0.1.0rc1`, `v0.1.0`, `v0.2.0`), each of which triggered a successful publish workflow run. [VERIFIED: `gh run list --limit 10`] All 10 most recent runs `completed`/`success`. [VERIFIED: `gh issue list`, `gh pr list`] Both empty. [VERIFIED: `gh api .../branches/main/protection`] **HTTP 404 — no branch protection on `main`.**

**oss-contrib forks (adk-docs, adk-python, keras):** all public forks of their respective upstreams, no GitHub releases (expected — contribution forks, not independently released), no branch protection on any (404 on all 3).

### 1.6 — Trusted publishing check

[VERIFIED: full read of all 3 workflow files] `release.yml` publishes via `pypa/gh-action-pypi-publish@release/v1` with **no `with:` block at all** (no password/api-token/repository-url inputs), `permissions: id-token: write` + `contents: read` at job level, `environment: pypi`, trigger `push: tags: "v*"`.

[VERIFIED: `grep -n -iE "secrets\.|password|api[_-]?token|PYPI_API_TOKEN" .github/workflows/*.yml` across all 3 files] Exactly one match — a comment explaining the token's *absence*, not a secret reference.

**Conclusion: OIDC Trusted Publishing only. No workflow in this repo uses a PyPI API token.**

### 1.7 — Account migration check

[VERIFIED: `git config user.email` in adk-tracegauge and all 5 oss-contrib sub-repos] All 6 repos → `gaurav.gandhi2411@gmail.com`, matching canonical.
[VERIFIED: `git remote -v` in all 6] All origin URLs match canonical GitHub identity `gaurav-gandhi-2411` (or the expected upstream for the two `-verify` clones).
[VERIFIED: `git grep` for email patterns and GCP-project-ID-shaped strings across adk-tracegauge's tracked files] One match (the canonical author email in pyproject.toml); no GCP project ID strings found.
[VERIFIED: `git log --all --author="gaurav" --format='%ae' | sort -u` in each oss-contrib sub-repo] Only `gaurav.gandhi2411@gmail.com` on every commit the user actually authored, across all 5 sub-repos. A full-tree grep across the oss-contrib forks was scoped out (thousands of files belonging to upstream Google/Keras contributors, not this user — would be pure noise) [UNVERIFIED: stray strings in files the user didn't author, by design not checked].

**No account-migration drift found anywhere.**

---

## SECTION 2 — DOES IT ACTUALLY WORK (verified by execution)

Throwaway venv: `C:\Users\gaura\tmp\tg-audit\venv` (outside the repo). No `ANTHROPIC_API_KEY` set; no paid API used anywhere.

### 2.1 — Install from PyPI

[VERIFIED: `pip install adk-tracegauge` — 112-line dependency tree, resolved `adk-tracegauge==0.2.0`, `google-adk==2.6.3` (matching the pin), `tracegauge==0.10.1`. Full `[eval]` extra pulled pandas/jinja2/rouge-score/scikit-learn/nltk/gepa/google-cloud-aiplatform etc., matching the README's documented install footprint.]

### 2.2 — google-adk version compatibility (highest-risk item)

[VERIFIED: `curl -s https://pypi.org/pypi/google-adk/json` → current version **2.7.0**, 87 total releases.] Package pin: `>=2.6.0,<2.7.0` — **excludes the current release.** [VERIFIED: `uv.lock` resolves `google-adk==2.6.3`.]

[VERIFIED: `.github/workflows/pypi-canary.yml` exists specifically to install unpinned/latest `google-adk[eval]` weekly (`cron: 0 6 * * 1`) and catch this exact drift.] [VERIFIED: `gh run list --workflow=pypi-canary.yml --limit 5` → **empty — the canary has never run.**]

[VERIFIED: `pip install --upgrade google-adk` in the pinned venv] pip warns of the conflict (`adk-tracegauge 0.2.0 requires google-adk[eval]<2.7.0,>=2.6.0, but you have google-adk 2.7.0`) but installs anyway.

[VERIFIED, twice independently — once in the original audit thread, once directly by the orchestrator this session, re-run live in the same venv just before finalizing this report: `python -c "import adk_tracegauge; print(adk_tracegauge.__version__)"` against `google-adk==2.7.0`] Output: `IMPORT OK 0.2.0`, no traceback (two harmless upstream deprecation/experimental-feature warnings only).

[VERIFIED: a real `LlmAgent` + `App(plugins=[TraceGaugeUsagePlugin()])` + `InMemoryRunner` + `create_session()` built and run against google-adk 2.7.0] Output: `ATTACH OK`, no traceback.

**Verdict: adk-tracegauge currently works against the live current google-adk release (2.7.0), despite its own pin excluding it.** The pin is stale by one minor version as of 2026-08-14; nothing is actually broken, but the repo's own canary CI — built specifically to catch this class of drift — has never executed. This is a real, actionable P1 finding (§7), independently reconfirmed twice in this audit.

### 2.3 — Minimal end-to-end smoke test (Ollama, zero-cost, live, unmocked)

[VERIFIED: `ollama list` → server started, 8 local models available] Real Ollama call via `LiteLlm` → `ollama_chat/qwen2.5:7b`, zero cost, not mocked.

Script: `LlmAgent` with one tool, `TraceGaugeUsagePlugin` wired into a hand-rolled `App`/`InMemoryRunner`, one turn ("What is 7 plus 5? Use the add tool."), scored via `CostEfficiencyEvaluator`.

[VERIFIED: `time python smoke_e2e.py`] First run 38.8s, second (warm model) 27.5s wall-clock (includes full Python/import/inference overhead).

**Raw output:** 2 real `CapturedCall` records captured (`model_version`, `prompt_token_count`, `candidates_token_count`, `cached_content_token_count`, `total_token_count`, `partial`), keyed by the real ADK-generated `invocation_id`. Pricing correctly returned `score=None` with a documented "model not in Gemini price table" warning — **this is the package working exactly as documented** (fail-closed on an unresolvable model), not a bug; the usage-capture mechanism itself worked correctly against a real live model.

**Lines of adk-tracegauge-specific code:** 3, on top of ~10 lines of standard ADK harness plus 1 mandatory call to an undocumented, non-public ADK internal (`EvaluationGenerator.convert_events_to_eval_invocations`) that the README says is required.

### 2.4 — Repo's own test suite

[VERIFIED: `uv sync --frozen` then `uv run pytest tests/ -v --cov=adk_tracegauge --cov-report=term-missing`, repo git status clean before/after]

**67 passed, 0 failed, 0 skipped.** Coverage: **99%** (257 statements, 1 missed, at `evaluator.py:174`). Runtime **49.25s** on the original run; an independent re-run this session measured **20.52s** for the identical command with identical pass/fail/coverage results — runtime is not a stable metric across runs (cache/warm-state variance), the substantive numbers (pass count, coverage) are confirmed exactly matching both times.

**Shallow-assertion audit:** [VERIFIED: 135 total `assert` statements; pattern-matched + manually read 7 candidates] Only **2** (in `test_registration.py`, `test_public_exports_are_importable`) are genuinely shallow not-None checks. The remaining ~133 assert real, specific behavioral outcomes (exact dollar values, exact token counts, exact model-key resolution, real object identity/correlation). `test_plugin.py`, `test_integration.py`, `test_e2e_runner.py` were read directly and confirmed to exercise real objects (real `LlmResponse`, real `UsageStore`, a real `InMemoryRunner` + hand-rolled fake `BaseLlm`, not `MagicMock`-through paths).

### 2.5 — Public API surface

[VERIFIED: `__init__.py` `__all__` + `inspect.signature()` against the installed package] 6 exports: `CostEfficiencyEvaluator`, `TraceGaugeUsagePlugin`, `UsageStore`, `DEFAULT_USAGE_STORE`, `METRIC_NAME`, `__version__`.

- `CostEfficiencyEvaluator`, `TraceGaugeUsagePlugin`, `METRIC_NAME` — documented with real README usage examples.
- `UsageStore` — named in prose only, no code example constructs it directly.
- **`DEFAULT_USAGE_STORE` — a real, working export with zero README mentions (undocumented gap).**
- `__version__` — undocumented but a standard convention, non-issue.

**No README-shown symbol was found missing from the installed package** — every import shown in the README's code blocks was exercised successfully in the live scripts above.

### 2.6 — CI Python version matrix vs. requires-python

[VERIFIED: both `ci.yml` and `pypi-canary.yml`] Single Python version tested: **3.11**, no matrix. [VERIFIED: pyproject.toml] Claims 3.10–3.13 via `requires-python` and classifiers.

**Mismatch confirmed:** Python 3.10, 3.12, 3.13 are never exercised by the project's own CI despite being claimed-supported. (This audit's own throwaway venv used 3.13.5 successfully — see §2.1–2.3 — but that's this audit's coincidence, not CI coverage.)

---

## SECTION 3 — COMPETITIVE POSITION

Source-verified against the installed `google-adk==2.6.3` (matching adk-tracegauge's own tested version).

### 3.1 — What ADK ships natively

[VERIFIED: `grep -rli opentelemetry src/google/adk/`] `telemetry/tracing.py` (1,061 lines) is a full OTel instrumentation layer, not a thin wrapper:
- `trace_agent_invocation()`, `trace_tool_call()`, `trace_call_llm()`/`trace_inference_result()` (tracing.py:140-174, 177-286, 355-452/931-978) emit `gen_ai.*`-semconv spans for agent invocations, tool calls, and LLM generations, including request/response bodies (redactable) and finish reasons.
- `_token_usage.py` (lines 37-95) converts usage metadata into OTel attributes `gen_ai.usage.{input,output}_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.reasoning.output_tokens` — **ADK natively captures cached and reasoning token counts on every LLM span, zero extra code.**
- `telemetry/setup.py:45-166` — `OTEL_EXPORTER_OTLP_ENDPOINT` env vars alone auto-register a `BatchSpanProcessor(OTLPSpanExporter())` — **any OTLP-compatible backend (Phoenix, Langfuse, Jaeger, Honeycomb) needs zero application code, only env vars.**
- `cli/fast_api.py` — `adk web --trace_to_cloud` ships Google Cloud Trace export natively via `CloudTraceSpanExporter`.
- `telemetry/sqlite_span_exporter.py` (251 lines) — persists every span to local SQLite specifically so `adk web`'s built-in trace viewer survives process restarts.
- `evaluation/` (35+ files) — a full eval framework: `agent_evaluator.py`, `local_eval_service.py`, multiple `final_response_match_v*`, `hallucinations_v1.py`, LLM-as-judge rubric evaluators, `metric_evaluator_registry.py`.

[VERIFIED: `grep -rniE "cost|usd|pricing|dollar" src/google/adk/telemetry/ src/google/adk/evaluation/`] **Zero hits beyond unrelated example prompt text.** ADK captures token counts natively but performs **zero USD conversion anywhere.**

### 3.2 — Current third-party ADK integrations (WebSearch/WebFetch, August 2026)

| Vendor | Install | Captures | Self-hostable | Pricing |
|---|---|---|---|---|
| **Arize Phoenix** | `pip install openinference-instrumentation-google-adk google-adk arize-phoenix-otel` | LLM/tool/agent spans; `phoenix.evals` datasets+experiments framework with a documented CI regression-gate pattern | **Yes, fully free, no usage cap** | Self-host $0; Arize AX free 25k spans/mo; Pro $50/mo |
| **Langfuse** | `pip install langfuse google-adk openinference-instrumentation-google-adk` | Agent runs, tool calls, nested multi-agent workflows, **"token usage, and cost" per node** (quoted from docs) | **Yes, MIT core, free, no event limit** | Cloud free 50k units/mo; Core $29/mo; Pro $199/mo |
| **AgentOps** | `pip install agentops google-adk` | Patches ADK's own trace functions; session replays, latencies, token usage | No self-host, dashboard-only | Pricing page 404'd on direct fetch; secondary sources report a free tier [UNVERIFIED against a primary source] |
| **W&B Weave** | `pip install weave google-adk requests` | `invoke_agent` spans with nested chat/tool children, full input/output/token usage | Yes — free local Docker "Personal" tier + Enterprise | Free 1GB/mo ingestion; Pro $60/mo |
| **LangSmith** | `pip install "langsmith[google-adk]"` + `configure_google_adk()` | Agent invocations, tool calls, LLM interactions, multi-agent traces | Enterprise-only, custom pricing | Free 5k traces/mo; Plus $39/seat/mo |
| **Google Cloud Trace** | Zero extra install (built into google-adk) | Every ADK-native span, via `CloudTraceSpanExporter` | N/A, GCP-managed | [UNVERIFIED — not priced in this pass] |

All five third-party vendors have a documented, current, ADK-specific integration page as of this research pass — not a stale or thin ecosystem.

### 3.3 — Capability matrix

| Capability | ADK-native | Langfuse | Phoenix | AgentOps | Weave | adk-tracegauge v0.2.0 |
|---|---|---|---|---|---|---|
| OTel span capture | Yes [source] | Yes [docs] | Yes [docs] | Yes [docs] | Yes [docs] | **No — explicitly disclaims this** [README: "Not a general ADK observability/tracing tool"] |
| Token usage capture | Yes, incl. cached+reasoning [source] | Yes [docs] | [unverified] | [unverified] | Yes [docs] | Yes, via `after_model_callback` [source] |
| **USD cost computation** | **No** [source] | Yes, per-node [docs] | [unverified] | [unverified] | [unverified] | **Yes — the package's one purpose**, provenance-tracked Gemini price table [source] |
| Sub-agent cost rollup | N/A | [unverified] | N/A | N/A | N/A | Yes, recursive via contextvars [source] |
| Eval/regression CI gate | Full framework, no CI recipe shipped | Feedback/score attach, not confirmed as CI gate | Yes, documented `docker run` + `run_experiment` CI recipe [docs] | [unverified] | [unverified] | **Broken for the standard path** — see §3.4/Verification below |
| Self-hosted, $0 infra | N/A | Yes [docs] | Yes [docs] | No [docs] | Yes (Personal tier) [docs] | Yes — pure library, no server |
| Web UI / trace viewer | Yes, `adk web` [source] | Yes [docs] | Yes [docs] | Yes [docs] | Yes [docs] | **No UI at all** [source: no UI code in repo] |

### 3.4 — What does adk-tracegauge do today that ADK-native + Phoenix don't provide free?

**One real thing: a maintained, provenance-tracked Gemini USD price table applied to captured token counts, with recursive cost rollup across sub-agent delegation.** Neither ADK-native tracing nor Phoenix performs USD conversion — confirmed by direct source inspection (§3.1) and corroborated across all fetched vendor docs (§3.2), none of which advertised USD cost except Langfuse.

**But that value is substantially undercut:**
- The README explicitly disclaims being a tracing/observability tool at all — it only computes one derived number, it is not competing with Phoenix's core job.
- Its primary integration point — registering as an ADK eval metric so it surfaces in `adk eval`/`AgentEvaluator` — does not work as intended (see "Verification & Corrections" immediately below for the exact, source-confirmed mechanism).
- Phoenix's free, self-hosted `phoenix.evals` + experiments framework already gives a developer a CI-gateable evaluation harness with a real UI — the exact infrastructure shape adk-tracegauge's README says it does *not* provide.

**Honest answer: essentially nothing that changes what infrastructure a developer needs.** A developer with ADK-native tracing (free, in-box) and Phoenix (free, self-hosted) is missing exactly one derived number — a dollar figure instead of a token count — obtainable from Phoenix's already-captured `gen_ai.usage.*` span attributes with a ~20-line pricing multiplier and no additional dependency or eval-integration breakage. adk-tracegauge's maintained price table and solved sub-agent rollup are a real, small convenience, not a capability gap in the sense the question asks.

### Verification & Corrections (applies to §3.4 and §5.3)

A verifier subagent independently re-ran the highest-stakes claims in this report before it was finalized. Two were confirmed exactly (§1.6 trusted-publishing config; §1.5 branch-protection 404). The google-adk 2.7.0 compatibility claim (§2.2) initially timed out on re-install due to heavy transitive dependencies, then was independently re-confirmed live by the orchestrator directly in this session (see §2.2's dual-verification note). The test-runtime number (§2.4) showed genuine run-to-run variance (49.25s vs. 20.52s) with identical pass/fail/coverage — not a substantive discrepancy.

**One claim was genuinely wrong on specifics and was corrected by direct source re-read (orchestrator, this session, not a subagent transcript):** the original claim that `local_eval_service.py:426-448` shows ADK "discarding" per-invocation results, and that `AgentEvaluator.evaluate()` "raises unconditionally," used the wrong line numbers and imprecise language. Direct re-read of the installed `google-adk==2.6.3` source found:

- `local_eval_service.py:439-458` (not 426-448): when a metric's `overall_eval_status == NOT_EVALUATED`, the per-invocation entry **is recorded** (not discarded/removed from the list) as `PerInvocationResult(actual_invocation=...)` with `score=None`. "Discards" overstated this — the correct description is "recorded as unusable."
- `agent_evaluator.py:800-848` (`_process_metrics_and_get_failures`): when a metric's scores are all `None` (which is what happens whenever `CostEfficiencyEvaluator` can't verify a threshold-style pass/fail verdict — it's a cost gauge, not a pass/fail check), `overall_eval_status` resolves to `NOT_EVALUATED`. Line 834: `if overall_eval_status != EvalStatus.PASSED:` — **this branch treats `NOT_EVALUATED` identically to `FAILED`** and appends a failure message.
- `agent_evaluator.py:267`: `assert not failures, failure_message` — fires whenever the accumulated `failures` list is non-empty.

**Net effect, confirmed by direct source read:** registering adk-tracegauge's cost metric with `AgentEvaluator.evaluate()` does cause it to raise `AssertionError`, in every case where the metric's own by-design behavior (never producing a `PASSED`/`FAILED` threshold verdict, since cost isn't a pass/fail concept) is what determines `overall_eval_status`. This is not because ADK "discards" the result outright — it's because ADK's failure-classification logic treats "not evaluated" the same as "failed," and adk-tracegauge's metric semantics (a gauge, not a gate) are permanently in the "not evaluated" bucket under that classification. The **practical conclusion GG stated in his own PR comment** (quoted in full in §5.3, itself independently `[VERIFIED: gh pr view ... — quoted comment]`) — "raises unconditionally," "discards the score" — is directionally accurate and reproducible, but this audit's own first-pass source citation supporting it was imprecise and has been corrected here rather than left standing uncorrected. This does not change §3.4's verdict.

### 3.5 — CEO / product lens

The user is a solo ADK developer already running `adk eval`/`AgentEvaluator` (or building a custom harness) who has looked at a token-usage number and wants to know what it costs in dollars — not "is my agent slow" (Phoenix/Langfuse territory) but "did this eval run or this agent redesign just get more expensive." That job is real and narrow, and surfaces at exactly one moment: staring at a captured `usage_metadata` object with no fast way to turn it into "$0.0043" without hand-writing and maintaining a Gemini price table (which changes, per-model, with a cache-read discount that varies). But the tool's own README concedes the one place that job most naturally lives — inside `adk eval`'s normal pass/fail run — is exactly where it currently doesn't work (confirmed by source, above), so the realistic buyer is someone patient enough to build a hand-rolled Runner harness *and* who trusts a young (initial release 2026-08-13, v0.2.0, single maintainer, Alpha status) package's price table over writing their own 20-line multiplier against numbers Phoenix or ADK's own spans already expose for free.

---

## SECTION 4 — ADOPTION FRICTION

All scores verified directly against the repo (README, file tree, PyPI/GitHub metadata) plus WebFetch of the Phoenix quickstart for comparison.

| Item | Score | Evidence |
|---|---|---|
| **4.1 Time-to-first-value** | **1/5** | README's first section header is "Read this first: `adk eval` and `AgentEvaluator.evaluate()` cannot surface this metric's output" — a limitation, not a quickstart. Only path ("the only path that reliably works," per the README) is a **6-step, ~30-LOC hand-rolled harness** requiring a real (non-mocked) model call and prior familiarity with ADK's `App`/`Runner`/`Session`/`Event` object model. Realistic estimate: 15-30 min for an ADK-fluent developer. Phoenix's quickstart, by contrast: install + one `register()` call on top of the developer's own existing agent code, producing a full inspectable trace UI — not a `print()`-your-own-number. |
| **4.2 README quality** | **1/5** | No runnable example above the fold (first code block is the `pip install` line; first usage code appears ~600 words in). **Zero screenshots/GIFs** (`grep "!\["` → only 2 badge matches, no content images). Badges present: License, Python 3.10+. Badges absent: PyPI version, CI status, downloads — despite a live PyPI listing and 3 working GitHub Actions workflows. |
| **4.3 Docs beyond README** | **0/5** | No `docs/`, `examples/`, `notebooks/`, or Colab link anywhere. The only worked example of real usage is `tests/test_e2e_runner.py`, which the README itself points to — the test suite is the documentation. |
| **4.4 Misconfiguration errors** | [UNVERIFIED — deferred] | Read-only constraint in the researching thread ruled out triggering fresh errors via a mismatched install; §2 (a separate execution thread) did trigger and document the actual real behavior for an unpriceable model (§2.3: `score=None` + a named, actionable warning) — that is the one misconfiguration class independently confirmed with real output in this audit. The other two requested scenarios (wrong ADK version, missing exporter) were not triggered with real output and are not scored to avoid fabricating error text. |
| **4.5 Naming/discoverability** | **0/5** | `WebSearch "adk-tracegauge pypi"` → zero hits for the actual package; surfaces `google-adk`, `adk`, `ag_ui_adk` instead. Package genuinely exists on PyPI with sensible keywords (`adk, agent-development-kit, google-adk, evaluation, cost, token-efficiency`) — metadata is right, but nothing points a searching developer to it. **Zero GitHub topics set** (`repositoryTopics: null`). |
| **4.6 Zero-config path** | **1/5** | `import adk_tracegauge` does register the metric as a side effect, but that registration is, per the package's own headline caveat, useless on its own (§3.4/Verification). The path that produces a usable number is the full 6-step harness from §4.1. A documented "workaround" (attach the callback directly to `LlmAgent`) is explicitly labeled "not the supported path" and breaks sub-agent cost aggregation. |

---

## SECTION 5 — oss-contrib STATE

Identity confirmed: [VERIFIED: `gh auth status` → "Logged in ... account gaurav-gandhi-2411"]. `--author @me` and `--author gaurav-gandhi-2411` cross-checked identical.

### 5.1 — Repos and raw PR lists

No PLAN.md/tracking file exists in oss-contrib; repo list derived from git remotes directly. Three target repos have GG PR activity: `google/adk-python`, `keras-team/keras`, `google/adk-docs`. A full-tree grep for additional GitHub repo references found only unrelated third-party links inside the upstream adk-docs integrations catalog — no fourth target repo.

| Repo | PR | Title | State | Created |
|---|---|---|---|---|
| google/adk-python | #6710 | fix(evaluation): record NOT_EVALUATED instead of dropping invocations with zero auto-rater samples | OPEN | 2026-08-13 |
| google/adk-python | #6682 | fix(evaluation): NOT_EVALUATED metric no longer masked by a passing one | OPEN | 2026-08-11 |
| google/adk-python | #6681 | fix(cli): resolve NameError in legacy create-eval-set route | OPEN | 2026-08-11 |
| google/adk-python | #6678 | fix(evaluation): resolve threshold via criterion in LlmAsJudge | **CLOSED, not merged** | 2026-08-11 |
| keras-team/keras | #23420 | fix: R2Score returns NaN instead of 1.0 for a perfect prediction on zero-variance data | OPEN | 2026-08-11 |
| google/adk-docs | #2128 | docs(integrations): add tracegauge Cost Evaluator for ADK agents | OPEN | 2026-08-13 |

### 5.2 — Per-PR detail

- **#6710, #6682, #6681** — all `MERGEABLE`, all CI `SUCCESS`/`SKIPPED`, `reviews: []`, no review threads. #6710 has one non-blocking, non-maintainer nit comment from a self-identified AI-run third-party reviewer (explicitly framed "take or leave," GG has not replied, does not block). **Next action for all 3: wait for a maintainer** — nothing actionable on GG's side.
- **#6678 (CLOSED)** — GG self-closed with a full explanation quoted verbatim: superseded by an independently-landed fix (`bcce415e`) that covers this PR's change plus more; branch is byte-for-byte identical to `main` post-merge. **Terminal, correctly resolved, no action needed.**
- **#23420 (keras)** — CI fully green across 5 backends. One review from Google's automated `gemini-code-assist` bot (not a human), flagging a real correctness concern (NaN-masking). GG replied in-thread with a numeric confirmation of the concern and pushed a fix + a new regression test (`test_r2_nan_total_mse_propagates`), quoted verbatim. **Content-addressed, but the GitHub review thread is still flagged `isResolved: false`** — bookkeeping-only gap, not a real blocker. No human maintainer has reviewed yet. Oldest PR in the set at 2 days idle.
- **#2128 (adk-docs)** — CI fully green (incl. Netlify preview). `reviews: []`, no review threads — **not yet reviewed by anyone**, not stalled on feedback. GG posted a substantial self-correction (quoted in full in §5.3) before any outside party engaged. Most recently touched PR in the set (0 days).

### 5.3 — google/adk-docs#2128 full detail

Not stalled — last updated 0 days ago, CI green, awaiting a first maintainer look, with no unresolved reviewer request. GG's self-correction comment (full text, [VERIFIED: `gh pr view 2128 ... --json body`]):

> "Correction to this PR's original 'Use with agent' instructions, found and fixed before this could reach anyone following the docs. The original page instructed wiring `TraceGaugeUsagePlugin` into an `App` and said `AgentEvaluator` 'picks up this `app`... automatically.' That claim doesn't hold against `google-adk==2.6.3`: `AgentEvaluator._get_agent_for_eval`, `EvaluationGenerator._process_query`/`_generate_inferences_from_root_agent`, and the `adk eval` CLI's own `get_root_agent` all resolve only `root_agent`/`get_agent_async` ... none of them ever look at an `App` or its `plugins`. Live-verified: `AgentEvaluator.evaluate()` raises `AssertionError` unconditionally when this metric is registered, and `adk eval` runs without crashing but discards the metric's per-invocation score and rationale (`score: null` in both the printed table and the persisted `eval_history/*.evalset_result.json`). Digging into why surfaced a second, independent issue in `LocalEvalService` itself ... Filed as a design question upstream, with a minimal self-contained repro: google/adk-python#6725. I've pushed a revision to this PR that: Removes the 'Use with agent' instructions that crash, states the limitation up front with a link to the filed issue, documents the hand-rolled `Runner` harness that's actually the working integration path today ... The package itself (`adk-tracegauge`) also shipped a `0.2.0` with the same reframing."

(GG's phrasing "discards"/"unconditionally" is the same wording independently re-examined and refined in the Verification & Corrections note after §3.4 — the practical outcome he describes is confirmed by direct source read; the literal mechanism is "treated as an automatic failure by ADK's failure-classification logic," not literal removal from the results list.)

### 5.4 — Cross-reference: adk-tracegauge dependencies

[VERIFIED: searched all PR bodies/comments across all 6 PRs plus the referenced issue for "tracegauge"] Matches found only within #2128 itself. **One real cross-reference:** #2128's comment links to google/adk-python#6725 (a design-question issue GG filed as a byproduct, 0 comments, not blocking). This is a PR→issue reference, not a PR-to-PR or PR-blocked-on-package-publish dependency. **No PR-to-PR cross-dependency found; no PR waits on adk-tracegauge being published or fixed.**

### 5.5 — ROUTE-TO-GG list

**Nothing found that is blocked on a 2FA-gated or browser-only action.** Merging (`gh pr merge`), commenting/replying (`gh pr comment`, `gh api graphql`), and reading review threads were all confirmed working via CLI in this session.

One item flagged as needing a live (write-mode) test before it can be ruled in or out: resolving keras-team/keras#23420's review thread via `resolveReviewThread` GraphQL — may be permission-gated to maintainers/reviewers rather than the external PR author (`authorAssociation: NONE`); not attempted in this read-only phase.

One judgment call, not a mechanical blocker: whether/when to nudge upstream maintainers on 4 CI-green, unreviewed PRs (none older than 2 days) — a timing/etiquette decision better made by GG than auto-executed.

---

## SECTION 6 — FEATURE ROADMAP PROPOSAL

**Thesis under test:** trace collection is commoditized (§3.1–3.3 confirm this decisively — 5 vendors + ADK-native all capture spans/tokens for free); trace-based **evaluation and regression detection** is not commoditized to the same degree (only Phoenix ships a documented CI-regression-gate recipe, and even that is generic, not agent-trajectory-aware). The roadmap below is weighted accordingly, and is gated on fixing the one defect (§7, P0-1) that currently makes the package's stated integration point unusable.

| # | Feature | User pain removed | Competitor coverage (from §3.3) | Cost | Eval methodology | Statistical-validity note |
|---|---|---|---|---|---|---|
| 1 | **Fix the eval-integration gate** — redesign `CostEfficiencyEvaluator` to report a real `PASSED`/threshold status (or ship an official wrapper that removes it from `AgentEvaluator`'s failure-aggregation path) instead of permanent `NOT_EVALUATED` | Removes the P0 defect (§7) blocking the package's own stated primary use case | None ship this specific gate-compatibility problem (it's ADK-specific plumbing) | S–M | Unit test: register the metric, call `AgentEvaluator.evaluate()` end-to-end, assert no `AssertionError` and a real score in the CSV/JSON output | N/A — correctness fix, not a statistical claim |
| 2 | **OTel-native export** — emit adk-tracegauge's cost figure as a span attribute (`gen_ai.usage.cost.usd` or similar) alongside ADK's native spans, composing with Phoenix/Langfuse instead of requiring a separate harness | Lets a developer already using Phoenix/Langfuse get the cost number inside their existing UI, zero new tooling | Langfuse already shows "cost" per node from its own pricing tables; this makes adk-tracegauge additive rather than parallel infrastructure | S–M | Integration test: run through a Phoenix collector, assert the attribute is present and correctly typed on the emitted span | N/A |
| 3 | **CI regression gate**: `tracegauge check --baseline` — snapshot a baseline run's cost/latency/tool-success distribution, bootstrap-CI test a new run against it, non-zero exit on statistically significant regression | The "who pays" wedge: a CI-blocking cost/quality regression check is not offered natively by ADK or (per fetched docs) any of the 5 competitors in this specific agent-cost framing | Phoenix has a generic experiments+CI recipe; none is cost/latency-distribution-specific for ADK | M | Synthetic fixture: known-mean baseline vs. a run with an injected N% cost increase; assert the gate fires above a set effect size and stays silent under normal noise | Must report n (number of eval-set invocations), bootstrap CI width, and false-positive rate on repeated no-change baselines — a single-run delta is not sufficient (per project statistical-honesty standard) |
| 4 | **Agent trajectory analysis** — loop detection, redundant tool calls, dead-end branches, step-count distributions | Debugging pain: "why did this agent run cost 5x more than usual" often traces to a behavioral loop, not a price change | Not offered by any of the 5 competitors per fetched docs (Phoenix/Langfuse show traces but don't auto-flag loops) | M | Fixture traces with known injected loops (synthetic) vs. clean traces; precision/recall on loop detection | Report false-positive rate on legitimately-repeated-but-intentional tool calls (e.g. a retry-with-backoff pattern) |
| 5 | **Deterministic trace replay** for offline eval — cache raw model responses per invocation, replay without re-calling models | Removes live-model cost/nondeterminism from CI regression checks (ties directly into #3) | Not offered by ADK-native or any fetched competitor doc | M | Record a real run once, replay N times, assert byte-identical derived metrics across replays | N/A — determinism check, not a statistical claim |
| 6 | **`tracegauge report` CLI** — self-contained HTML report from a captured run | Directly addresses §4.2/§4.3's 1/5 and 0/5 scores (no visual output, no docs) | Phoenix/Langfuse/Weave all have hosted dashboards; a static HTML report is a lighter-weight $0 alternative, not a competitor to their live UIs | S | Snapshot test: known trace fixture → assert report contains expected cost/token figures | N/A |
| 7 | **Multi-provider pricing table** (beyond Gemini-only) | §2.3 found real Ollama/non-Gemini usage currently returns `score=None` — broadens applicability beyond a single-provider audience | Langfuse/Weave capture usage for any provider already; adk-tracegauge's Gemini-only scope is a real narrowing vs. them | M | Price-table unit tests per added provider, staleness test (already exists for Gemini per §3.4, extend pattern) | N/A |
| 8 | **Failure-mode clustering** — embed failed/high-cost traces, cluster, surface top-k failure taxonomies | Real pain at scale (many eval runs), but requires an embedding model and enough failure volume to cluster meaningfully | Not offered by any fetched competitor doc for ADK specifically | L | Cluster purity against a hand-labeled failure-taxonomy fixture set | Needs a stated minimum-N before clustering is meaningful; report silhouette score or equivalent, not just cluster count |
| 9 | **LLM-judge multi-model consensus scoring** over traces | Real pain (manual labeling doesn't scale), but ADK already ships `llm_as_judge.py`/rubric evaluators natively (§3.1), and Phoenix/LangSmith both already do judge-based scoring | Already substantially covered by ADK-native + Phoenix/LangSmith — low differentiation | M–L | Inter-judge agreement (Cohen's kappa or similar) across ≥2 model families, reported alongside every score, never presented as ground truth | Must state judge disagreement rate; a single-judge score is not sufficient evidence per project eval-design standard |
| 10 | **Cost/budget guardrails** per agent/tool/session with alerting thresholds | Extends the one thing the package already does; useful but incremental, not new capability | Partially covered by Langfuse's per-node cost figures already | S | Threshold-crossing unit tests against synthetic cost sequences | N/A |

**Ranked by (differentiation × pain) / cost, top 3 recommended:**

1. **#1 — Fix the eval-integration gate.** Not optional: every other feature on this list, and the package's own stated purpose, sits on top of a broken integration point. Highest urgency, lowest relative cost, unblocks everything else.
2. **#3 — CI regression gate with bootstrap-CI drift detection.** The single highest differentiation × pain combination on the list — this is the concrete instantiation of the stated thesis (regression detection is not commoditized) and the only capability in this table with zero competitor coverage found in §3.2's research. Depends on #1 shipping first (a broken eval integration can't feed a CI gate).
3. **#2 — OTel-native export.** Directly fixes the positioning problem found in §3.3/3.4 (the package currently competes with nothing because it does nothing Phoenix doesn't, while also refusing to integrate with Phoenix) — turns the package from "a parallel, narrower tool" into "the cost layer that plugs into whatever tracing backend you already have." Low-medium cost, materially changes the competitive verdict in §3.4.

(#4 Agent trajectory analysis and #5 Deterministic replay are close seconds — both score well on differentiation but are appropriately sequenced after the P0 fix and the OTel/CI-gate foundation.)

---

## SECTION 7 — PRIORITIZED DEFECT LIST

| ID | Severity | Evidence | Proposed fix | Effort |
|---|---|---|---|---|
| D1 | **P0** | `AgentEvaluator.evaluate()` raises `AssertionError` whenever adk-tracegauge's cost metric is registered, because its permanent `NOT_EVALUATED` status is treated identically to `FAILED` by ADK's failure classifier (`agent_evaluator.py:800-848,267`); `adk eval` CLI records `score=None` (`local_eval_service.py:439-458`). Source-confirmed this session; corroborated by GG's own PR comment (§5.3). This is the package's own documented primary integration point. | Redesign the metric to report a real threshold verdict, or ship an official wrapper excluding it from `AgentEvaluator`'s failure aggregation (roadmap #1) | M |
| D2 | P1 | google-adk pin (`<2.7.0`) excludes the current PyPI release (2.7.0); the weekly canary CI built specifically to catch this drift has never run (`gh run list --workflow=pypi-canary.yml` empty). Package independently confirmed still-working against 2.7.0 twice this session — works by luck, not by verified tooling. | Manually trigger the canary now (`workflow_dispatch`); confirm the cron actually fires going forward; bump the pin once confirmed | S |
| D3 | P1 | CI (`ci.yml`, `pypi-canary.yml`) tests only Python 3.11; `pyproject.toml` claims 3.10–3.13 support via `requires-python` and classifiers. 3.10/3.12/3.13 unverified by the project's own CI. | Add a CI test matrix for 3.10–3.13 | S |
| D4 | P1 | Time-to-first-value scored 1/5: 6-step, ~30-LOC hand-rolled harness, no working `adk eval` path, README opens with a limitation instead of a quickstart. | Depends on D1 fix; then rewrite README to lead with a working quickstart | M (post-D1) |
| D5 | P1 | Zero discoverability: realistic web search for the package surfaces nothing; no GitHub topics configured; README explicitly disclaims being a "tracing" tool despite the "tracegauge" name suggesting otherwise. | Add GitHub topics (`google-adk`, `llm-observability`, `cost-tracking`); consider name/positioning clarity | S |
| D6 | P1 | No documentation beyond the README — no `docs/`, `examples/`, `notebooks/`, or Colab; the test suite is the only worked example. | Add an `examples/` directory with at least the working hand-rolled-harness pattern as a standalone runnable script | S–M |
| D7 | P1 | Zero-config claim overstated: the one import that "configures" anything only registers a metric that doesn't work standalone (ties to D1). | Resolved by D1 fix | — |
| D8 | P2 | No branch protection on `main` (`gh api .../protection` → 404) for adk-tracegauge and all 3 oss-contrib forks. | Enable required-status-checks branch protection on `main` | S |
| D9 | P2 | No GitHub Release objects despite 3 git tags — tags-only release process, no changelog surfaced on the Releases page. | Add `gh release create` (or a release-notes-generating Action) to the release workflow | S |
| D10 | P2 | README has no screenshot/GIF; only 2 of 5 useful badges present (License, Python version); PyPI-version, CI-status, and download-count badges absent despite live PyPI listing and 3 working CI workflows. | Add the missing badges and one annotated terminal-output screenshot | S |
| D11 | P2 | `DEFAULT_USAGE_STORE` is a real, working export with zero README mentions. | Document it or mark it private (`_DEFAULT_USAGE_STORE`) if not meant for direct use | S |
| D12 | P2 | Local (gitignored) `dist/` holds stale v0.1.0 build artifacts vs. current 0.2.0 — cosmetic, doesn't affect the published package. | `rm -rf dist/` before any local rebuild-and-inspect step | trivial |
| D13 | P2 | 5 already-merged local/origin branches in adk-tracegauge not cleaned up (`chore/0.1.0-release`, `chore/0.2.0-release`, `chore/rc1-version-bump`, `ci/pypi-trusted-publishing`, `docs/releasing`). | Delete after explicit confirmation (all confirmed merged via `main`'s own history) | trivial |
| D14 | P2 | 2 of 135 test assertions in `test_registration.py` are shallow not-None checks (out of an otherwise substantively behavioral suite). | Strengthen to identity/type checks matching the file's own third assertion pattern | trivial |
| D15 | P2 | oss-contrib's `adk-python` local checkout is behind its own `origin` fork by ~40–96 commits on 3 of 4 PR branches (someone used GitHub's "Update branch" button without a local `git pull`). Doesn't block the open PRs (all confirmed `MERGEABLE`). | `git fetch origin && git reset --hard origin/<branch>` per branch (or a plain `git pull`) next time those branches are worked on locally | trivial |
| D16 | P2 | keras-team/keras#23420's review thread is content-addressed (GG replied + pushed a fix + a regression test) but not marked "Resolved" on GitHub — bookkeeping only. | Attempt `resolveReviewThread` via `gh api graphql`; if permission-denied (external contributor), this becomes a genuine ROUTE-TO-GG ask to the reviewer | trivial (untested — see §5.5) |

**P0 count: 1. P1 count: 5 (one, D7, fully resolved by the D1 fix). P2 count: 9.**

---

## Verification methodology note

A verifier subagent independently re-ran 5 of this report's highest-stakes claims before finalization (full transcript: `verification_notes.txt` in this session's scratch directory, not part of this deliverable). Results: 2 confirmed exactly (§1.5, §1.6), 1 inconclusive-then-directly-reconfirmed by the orchestrator (§2.2), 1 real but non-substantive discrepancy (§2.4 runtime variance), and 1 genuinely incorrect claim (line numbers and "discards" wording in the original §3.4/§5.3 source citation) that was corrected by the orchestrator reading the actual installed source directly rather than trusting either subagent's prior transcription — documented in full under "Verification & Corrections" after §3.4. No claim in this report rests solely on an unverified subagent transcript for a finding material to the executive summary.
