# Releasing adk-tracegauge

Tag-triggered, published by CI over PyPI Trusted Publishing (OIDC) from the very first
release — this package never had a manual-token publish era to migrate away from. Wired up
before `0.1.0rc1`, the first thing ever published.

This document describes the actual flow, written after running it twice for real:
`0.1.0rc1` (verification pass) and `0.1.0` (2026-08-13) — not a plan for how it should work.

## The flow

1. **Bump the version** in exactly two places (confirmed by grep — these are the only two
   places the version string appears in this repo):
   - `pyproject.toml`: `[project].version`
   - `src/adk_tracegauge/__init__.py`: `__version__`
   - Regenerate the lockfile: `uv lock`
2. **Commit and open a PR.** CI (`ci.yml`) runs the full test suite (47 tests), ruff, and
   mypy strict against the version-bumped code.

   One friction point encountered on every version-bump PR so far: this repo's merge-gate
   hook (rule 70a, gate 3b) requires the PR body to explicitly declare the reviewable/
   generated line-count split whenever `uv.lock` is touched — e.g. "2 reviewable lines
   (`pyproject.toml`, `__init__.py`) + 1 generated line (`uv.lock`)". Missing this blocks
   the merge even when CI is green; add it up front rather than discovering the block after
   opening the PR.
3. **Merge, then tag the merged commit:**
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. **`release.yml` builds and publishes** — same pattern as tracegauge's own `release.yml`
   (mirrored byte-for-byte): `uv build`, `twine check`, then
   `pypa/gh-action-pypi-publish@release/v1` over OIDC via the `pypi` GitHub Environment.
   Confirm the actual upload, not just the green checkmark:
   ```bash
   gh run list --repo gaurav-gandhi-2411/adk-tracegauge --workflow release.yml --limit 1
   gh run view <run-id> --repo gaurav-gandhi-2411/adk-tracegauge --log | grep -A1 "View at"
   ```
   Expect a Sigstore `Successfully verified SCT...` line and
   `View at: https://pypi.org/project/adk-tracegauge/X.Y.Z/`.
5. **Post-publish verify from a fresh environment against the real index** — see below.
   This package's verification is stricter than a typical library's: it must also confirm
   the *dependency* resolution (`tracegauge`'s pinned range) and that the dual-licensed
   files' Apache grant actually shipped in what got installed — see "What to check" below.
6. **Never yank a published version**, including a superseded pre-release. `0.1.0rc1`
   stays live and installable even after `0.1.0` shipped — a pinned `adk-tracegauge==0.1.0rc1`
   install must keep working indefinitely. If a release has a real problem, ship a new
   version; don't delete the old one.

## The tag-must-match-pyproject gotcha (real incident, not hypothetical)

`pyproject.toml` has no dynamic versioning — the published version comes entirely from
`[project].version`, and the git tag is only a trigger, not a version source. **This bit
this exact repo's `0.1.0rc1` release before the tag was ever pushed**: `pyproject.toml`
still said `0.1.0` (the final version) when the plan was to tag `v0.1.0rc1` for the resolver
verification pass. Tagging as-is would have silently published `0.1.0` — skipping the rc
entirely, and unable to be un-published afterward (see "never yank" above). Caught in review
before the tag was pushed by explicitly checking:
```bash
git show origin/main:pyproject.toml | grep "^version"
```
before every tag, not after. Do this every time, no exceptions — it costs one command and
the failure mode it prevents is permanent.

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
