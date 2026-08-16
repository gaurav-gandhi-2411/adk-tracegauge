# Releasing adk-tracegauge

Tag-triggered, published by CI over PyPI Trusted Publishing (OIDC) from the very first
release — this package never had a manual-token publish era to migrate away from. Wired up
before `0.1.0rc1`, the first thing ever published.

This document describes the actual flow, written after running it twice for real:
`0.1.0rc1` (verification pass) and `0.1.0` (2026-08-13) — not a plan for how it should work.

## The flow

1. **Bump the version** in exactly one place: `src/adk_tracegauge/__init__.py`'s
   `__version__` literal.

   Until the fix below, this was two hand-maintained places (`pyproject.toml`'s
   `[project].version` and `__init__.py`'s `__version__`) with no mechanism keeping them in
   sync — and that gap caused a real bug: the PR that bumped `0.2.0` → `0.3.0` bumped only
   `pyproject.toml`'s literal, squash-merged into `main`, and left `__init__.py` stale at
   `0.2.0`. `0.3.0` had not been tagged/published yet, so it was fixable pre-release; caught
   by a release gate, not by any test that existed at the time (none did).

   The fix: `pyproject.toml` now declares `dynamic = ["version"]` and resolves it via
   `[tool.setuptools.dynamic]`'s `version = { attr = "adk_tracegauge.__version__" }` —
   setuptools reads `__init__.py`'s `__version__` literal via static AST analysis at build
   time (no import, so none of that module's `google-adk` import-time side effects run
   during a build). `__init__.py` is now the single source of truth; `pyproject.toml` has no
   version literal of its own to drift.

   A guard test, `tests/test_version_consistency.py`, asserts
   `importlib.metadata.version("adk-tracegauge") == adk_tracegauge.__version__` — this is
   the regression gate for the exact bug above: it fails whenever the installed package's
   metadata and the runtime `__version__` attribute disagree, which is now structurally only
   possible if the single-source mechanism itself breaks (e.g. a future setuptools change
   that can't resolve `attr =`), not from a second hand-edit being forgotten. Verified by
   deliberately reproducing the two-hardcoded-literals mismatch and confirming this test
   fails against it, then confirming it passes once the fix is applied — see the
   `fix/version-single-source` PR description for the fail-then-pass proof.
   - Regenerate the lockfile: `uv lock`
2. **README must document any user-facing command or flag this release adds.** The
   CHANGELOG alone is not sufficient — it tells *existing* users what changed since their
   last install; the README is what a *prospective* user (or PyPI's own rendered project
   page) reads to learn the tool exists at all. A feature with a CHANGELOG entry but no
   README section is invisible to anyone who hasn't already installed the package.

   **Real incident, not hypothetical:** `0.4.0` (sub-agent cost attribution — `--agent`,
   `cost_by_agent`, `agent_name`) shipped to PyPI with a correct, detailed CHANGELOG entry
   and zero mentions of `--agent` anywhere in the README — caught only after the release
   was already published and permanently locked into that version's PyPI page (PyPI does
   not allow re-uploading a version's metadata). Fixed in the next release, `0.4.1`, but the
   gap in `0.4.0`'s own published page is permanent. Checklist for every release:
   - Every new subcommand and flag has a README section with **real captured output from
     the published artifact**, not an invented example.
   - Any documented backward-compatibility claim (a schema bump, a changed default) is
     verified against a real old-format input, not asserted from memory.
   - Verify the *published* README (`curl -s https://pypi.org/pypi/<pkg>/<version>/json`,
     string-search the `description` field) contains the new command/flag names — not just
     that the local `README.md` file does; a build/publish step could in principle diverge.
3. **Commit and open a PR.** CI (`ci.yml`) runs the full test suite (including the version
   guard test above), ruff, and mypy strict against the version-bumped code.

   One friction point encountered on every version-bump PR so far: this repo's merge-gate
   hook (rule 70a, gate 3b) requires the PR body to explicitly declare the reviewable/
   generated line-count split whenever `uv.lock` is touched — e.g. "2 reviewable lines
   (`pyproject.toml`, `__init__.py`) + 1 generated line (`uv.lock`)". Missing this blocks
   the merge even when CI is green; add it up front rather than discovering the block after
   opening the PR.

   **Every PR into `main`, no exception, is routed to GG for merge — CC never self-merges,
   regardless of triviality, docs-only content, or all-gates-green (rule 70d).** This applies
   to the version-bump PR itself and to any PR opened during the release process, including
   a same-session follow-up fix discovered while verifying a prior PR. Incident: a 1-line,
   docs-only `CHANGELOG.md` date fix (PR #9) was self-merged one turn after direct-to-main
   commits on this exact repo were flagged as the process gap branch protection (this
   document's own prerequisite) exists to close — the reasoning ("trivial, docs-only, CI
   green") was identical to the incident it followed, not a different judgment (2026-08-16).
   Prepare the PR, confirm every gate passes, then stop and wait for GG.
4. **Merge, then tag the merged commit:**
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
5. **`release.yml` builds and publishes** — same pattern as tracegauge's own `release.yml`
   (mirrored byte-for-byte): `uv build`, `twine check`, then
   `pypa/gh-action-pypi-publish@release/v1` over OIDC via the `pypi` GitHub Environment.
   Confirm the actual upload, not just the green checkmark:
   ```bash
   gh run list --repo gaurav-gandhi-2411/adk-tracegauge --workflow release.yml --limit 1
   gh run view <run-id> --repo gaurav-gandhi-2411/adk-tracegauge --log | grep -A1 "View at"
   ```
   Expect a Sigstore `Successfully verified SCT...` line and
   `View at: https://pypi.org/project/adk-tracegauge/X.Y.Z/`.
6. **Post-publish verify from a fresh environment against the real index** — see below.
   This package's verification is stricter than a typical library's: it must also confirm
   the *dependency* resolution (`tracegauge`'s pinned range) and that the dual-licensed
   files' Apache grant actually shipped in what got installed — see "What to check" below.
   Also confirm (step 2's checklist) that the published README actually rendered the new
   commands/flags, via PyPI's JSON API — not just that CI succeeded.
7. **Never yank a published version**, including a superseded pre-release. `0.1.0rc1`
   stays live and installable even after `0.1.0` shipped — a pinned `adk-tracegauge==0.1.0rc1`
   install must keep working indefinitely. If a release has a real problem, ship a new
   version; don't delete the old one.

## The tag-must-match-source gotcha (real incident, not hypothetical)

The git tag is only a trigger for `release.yml`, never a version source — the published
version comes entirely from whatever `src/adk_tracegauge/__init__.py`'s `__version__` reads
on `main` at the moment the tag is pushed (as of the `dynamic = ["version"]` fix,
`pyproject.toml` itself carries no version literal of its own to check; it derives one from
`__init__.py` at build time — see "Bump the version" above). **This bit this exact repo's
`0.1.0rc1` release before the tag was ever pushed**, back when the version lived as a static
literal directly in `pyproject.toml`: it still said `0.1.0` (the final version) when the
plan was to tag `v0.1.0rc1` for the resolver verification pass. Tagging as-is would have
silently published `0.1.0` — skipping the rc entirely, and unable to be un-published
afterward (see "never yank" above). Caught in review before the tag was pushed.

The discipline this incident established — verify what will actually get read *before*
tagging, not after — still applies exactly the same way today, just against one file instead
of two:
```bash
git show origin/main:src/adk_tracegauge/__init__.py | grep '__version__ ='
```
Do this every time, no exceptions — it costs one command and the failure mode it prevents is
permanent. (The single-source fix and its guard test, `tests/test_version_consistency.py`,
prevent the *two-places-disagree* failure mode this document originally described; they do
not prevent tagging against the wrong commit or a not-yet-bumped `main`, which is what this
check still catches.)

## Statistical comparisons require a significance test before they become a documented claim

Any change to README/docs that compares two *measured* rates against each other (not a rate
against a fixed nominal target — comparing two independently-measured empirical numbers) must
carry a significance test result (e.g. a two-proportion z-test, with its z/p reported) before
the comparison ships as a stated finding. A visible gap between two point estimates is not
evidence of a real difference on its own — small-count binomial proportions (as few as a few
dozen successes out of a few thousand trials, exactly the regime this project's own FPR
grids run at) are noisy enough that a real, reproducible-sounding ranking can flip on
re-measurement.

**Real incident, not hypothetical:** the published README claim "paired mode's FPR exceeds
two-sample's at 4 of 6 shared grid cells" (Phase 7 U2, 2,000 trials/cell) reached
`main`, a tagged release, and PyPI's published `0.3.0` README without ever being
significance-tested. It does not survive testing — a two-proportion z-test on the same
published counts finds no cell significant (largest z=1.80, p=0.07), and an independent
5,000-trial re-measurement confirms the ranking does not reproduce (0/6 cells significant,
one cell's ranking flips outright). See `docs/audit/FPR_ANOMALY.md` for the full
investigation. No production code defect caused this — the numbers themselves were measured
correctly; the defect was publishing a cross-measurement *comparison* as a finding without
testing whether the gap was distinguishable from noise.

**The rule this establishes:** before writing "X's rate is higher/lower/different than Y's
rate" anywhere that ships (README, docs site, a PR description asserting a regression), run
the significance test and cite its result alongside the claim — the same discipline this
project already applies to the rates themselves (Wilson CIs, stated trial counts). A rate
reported alone (no comparison implied) needs only its own CI, as before; this rule applies
specifically to comparative claims between two measured quantities.

## Post-publish verification

Manual today (see the backlog note in this session's memory: an automated post-publish step
across `release.yml` in this repo, `tracegauge`, and `agentgauge` is planned as one
dedicated change, not built yet).

```bash
# Fresh venv at a SHORT path -- see the MAX_PATH trap below, this is load-bearing
uv venv --python 3.11 C:\adk-tg-verify
uv pip install --no-cache adk-tracegauge==X.Y.Z --index-url https://pypi.org/simple/ --python C:\adk-tg-verify\Scripts\python.exe

C:\adk-tg-verify\Scripts\python.exe -c "
import adk_tracegauge
print(adk_tracegauge.__version__)
from google.adk.evaluation.metric_evaluator_registry import DEFAULT_METRIC_EVALUATOR_REGISTRY
names = [m.metric_name for m in DEFAULT_METRIC_EVALUATOR_REGISTRY.get_registered_metrics()]
assert 'adk_tracegauge_cost_usd' in names
print('registered OK')
"

# Historical (through the release that shipped Phase 3): this package depended on
# `tracegauge` as a library, and this rc-before-final process existed specifically to
# confirm tracegauge>=0.10.1 (the version carrying the Apache-2.0 grant) actually resolved,
# not an older cached/pinned version -- checked via:
#   python -c "import importlib.metadata as m; print(m.version('tracegauge'))"
#   find <venv>\Lib\site-packages\tracegauge-*.dist-info -iname "*LICENSE*"
#   (expect: .../licenses/LICENSE and .../licenses/LICENSE-APACHE)
# As of Phase 4 R5, `tracegauge` is no longer a dependency at all -- the cost arithmetic
# this package used from it was ported in-house (src/adk_tracegauge/_cost.py, which
# carries its own attribution note). This check is obsolete for any release from here on;
# left here, not deleted, as the reason this package's release process still ships an rc
# before a final on any packaging-relevant change -- that discipline predates and outlives
# this one specific check.

rm -rf C:\adk-tg-verify
```

## The MAX_PATH trap (Windows) — presents as a package defect, isn't one

The first `0.1.0rc1` verification attempt, run from a venv created under a deeply-nested
scratch/temp directory, failed on `import adk_tracegauge` with:
```
ModuleNotFoundError: No module named 'google.cloud.aiplatform_v1.services.deployment_resource_pool_service.transports.grpc_asyncio'
```
for a file that demonstrably existed on disk. Root cause: `google-adk[eval]`'s transitive
dependency on `google-cloud-aiplatform` has notoriously deep package/file paths, and the
full resolved path came out to 264 characters — one past Windows' classic 260-character
`MAX_PATH` limit. Re-running the identical install and import from `C:\tg-rc1-test` (a short
path) passed cleanly on the same machine, same Python, same package versions. Not a defect
in this package, `tracegauge`, or `google-adk` — an artifact of where the verification venv
happened to be created.

**Lesson, same as tracegauge's own `RELEASING.md`:** always verify from a short path
(`C:\<name>`) on Windows, never a deeply nested temp directory — especially for this
package specifically, since `google-adk[eval]` pulls in `google-cloud-aiplatform` as a hard
dependency (see README, "Install" section, for why the `[eval]` extra is required at all).
If a fresh-venv verification fails with a `ModuleNotFoundError` for a module that
demonstrably exists on disk, check the full resolved path length before concluding the
package itself is broken.
