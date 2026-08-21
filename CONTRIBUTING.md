# Contributing

`adk-tracegauge` is a solo-maintained, T1-tier portfolio project (see
`PLAN.md`) — contributions are welcome, but the bar is the same one the
maintainer holds their own commits to: real tests, honest documentation,
no fabricated numbers.

## Dev setup

```bash
git clone https://github.com/gaurav-gandhi-2411/adk-tracegauge.git
cd adk-tracegauge
uv sync --frozen   # installs the exact locked dependency set, incl. dev deps
```

`uv` is the only supported package manager for this repo — see
`pyproject.toml`'s `[dependency-groups]`. `uv.lock` is committed; never
hand-edit it, and never run `uv sync` without `--frozen` in CI or when
verifying a change (an unfrozen sync can silently re-resolve and mask a
real dependency issue).

## Running tests, lint, and type checks

```bash
uv run pytest tests/ -v --cov=adk_tracegauge --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

All four must pass clean before a PR is reviewable.

**Set up the pre-commit hook once, so `ruff` findings surface before you
commit, not after CI runs them.** CI's `lint-and-test` matrix runs the
exact `ruff` version locked in `uv.lock` — three separate PRs this
project shipped got a red Python-3.11 leg from `ruff check`/`ruff format`
findings a local run never caught, because nothing ran `ruff` locally
before the commit landed. One-time setup:

```bash
uv run pre-commit install
```

After that, every `git commit` runs `ruff check --fix` and `ruff format`
automatically, via `uv run ruff` (`language: system` in
`.pre-commit-config.yaml`, deliberately not the `astral-sh/ruff-pre-commit`
mirror repo — that mirror pins its own `ruff` version separately from
`uv.lock`, a second source of truth that can silently drift; `uv run ruff`
always resolves to the exact version this repo has locked, no separate
pin to keep in sync). If a file needs reformatting, the hook fixes it in
place and the commit is aborted so you can review the diff and re-commit
— it will not silently commit a reformatted file you haven't seen.

**Windows note**: if `pre-commit install`/`pre-commit run` fails with a
traceback mentioning `pip._vendor.rich.markup` or similar during "Installing
environment," that's a corrupted `virtualenv` seed-wheel cache, not a
problem with this repo's config — clear
`%LOCALAPPDATA%\pypa\virtualenv\Cache` and `%USERPROFILE%\.cache\pre-commit`
and retry.

The test suite is
substantively behavioral (real objects, real ADK `Runner`/`Event`
machinery in the `*_e2e_runner.py`/`*_agent_evaluator_integration.py`
files — not `MagicMock`-through paths) — a new feature needs a real test
that would fail if the feature were wrong, not a shallow `is not None`
check (see `tests/test_registration.py`'s history for what that looked
like before it was fixed).

If you're adding or changing anything in `examples/`, run it for real
before committing — `uv run python examples/<file>.py` — and paste the
real output into your PR description. A "runnable" example that was never
actually run is not verified.

## Branch and commit conventions

- Branches: `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, or `wave-N-*`
  for phased work — short-lived, single-purpose.
- Commits: [Conventional Commits](https://www.conventionalcommits.org/)
  (`type(scope): message`). Types: `feat`, `fix`, `docs`, `chore`, `style`,
  `test`, `refactor`, `perf`. The commit body explains *why*, not what —
  the diff already shows what. If a fix addresses a specific defect ID
  from an audit or issue, reference it (`fix(pricing): correct gemini-3.6-flash
  rate -- audit W1/P0`).
- No AI-co-author trailers in commits authored through an AI coding
  assistant — see this repo's own git history for the convention; the
  maintainer documents LLM usage transparently in ADRs/READMEs instead of
  in commit metadata.
- PRs stay small and reviewable (~400 lines of reviewable diff; generated
  artifacts like `uv.lock` are excluded from that count but must be
  called out explicitly in the PR body if present).

## Price-table corrections

If you're reporting a stale or incorrect price, use the "Price table
correction" issue template (`.github/ISSUE_TEMPLATE/`) — it asks for the
exact model key, the source URL, and the date you checked it, which is
what `src/adk_tracegauge/data/gemini_prices.json`'s own schema requires
per entry (`source_url`, `fetched_on`). See README, "Pricing" and
"Updating the price table" for the full mechanics — briefly: edit the
JSON entry directly, bump `fetched_on` to today, and run
`uv run pytest tests/test_pricing.py` to confirm the staleness test goes
green. There's no automated price-scraping in this repo by design — a
human should look at the actual pricing page before a dollar figure
changes.

## Why the price-freshness and canary CI jobs exist

Two scheduled (not just push-triggered) CI jobs matter specifically for
this package, and are worth understanding before you touch either:

- **`.github/workflows/price-freshness.yml`** (weekly cron +
  `workflow_dispatch`) re-checks every bundled price-table entry's
  `fetched_on` date against `STALE_THRESHOLD_DAYS` (currently 90, see
  `_pricing.py`), even with zero new commits. The commit-time equivalent
  test (`tests/test_pricing.py::test_bundled_table_is_not_currently_stale`)
  only re-checks staleness when someone happens to push — a table that
  goes stale during a quiet period (no commits, but real-world prices
  changed) would otherwise go unnoticed until the next unrelated commit.
  Pure date arithmetic against the JSON file, zero network/API calls.

- **`.github/workflows/pypi-canary.yml`** (weekly cron +
  `workflow_dispatch`) installs the *latest*, unpinned `google-adk[eval]`
  release and runs the full test suite against it — independent of this
  package's own `google-adk` pin in `pyproject.toml`. This package
  registers into `google.adk.evaluation.metric_evaluator_registry`, which
  google-adk itself marks `@experimental` ("may change or be removed ...
  at any time"). Canary is what catches that class of break on a schedule,
  rather than via a user bug report months after a new google-adk release
  ships. If canary goes red, the fix is either a code change (if the
  registry API changed in a compatible-but-different way) or tightening
  the pin's upper bound (if it broke outright) — never silently ignoring
  a red canary run.

Both jobs are read-only and side-effect-free (no releases, no pushes) —
safe to re-run manually (`gh workflow run <file> --ref main`) any time you
want to check current status without waiting for the next scheduled run.
