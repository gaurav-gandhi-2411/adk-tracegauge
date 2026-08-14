# CI snippet: `tracegauge check` as a GitHub Actions cost-regression gate

Produced by Phase 2 W4 (see `PLAN.md`). This is the canonical, tested
invocation shape for `adk-tracegauge`'s CI regression gate -- W5's README
rewrite should embed this verbatim, not re-derive it.

Requires `TraceGaugeUsagePlugin` to be wired into the agent/`App` your eval
entrypoint runs against (see README's "What this actually is") -- otherwise
the snapshot will be empty and `tracegauge check` will report
`insufficient_data`, not a false "pass".

Your own `my_eval_suite.run_eval_and_return_store` entrypoint is a
zero-argument callable you write: it drives your agent's real eval run
(e.g. calling `AgentEvaluator.evaluate()`, or your own hand-rolled `Runner`
harness) and either returns a `UsageStore` directly, or simply lets the
calls land in `adk_tracegauge.DEFAULT_USAGE_STORE` as a side effect (the
common case, since `TraceGaugeUsagePlugin` defaults to that store) --
`tracegauge snapshot` accepts either.

```yaml
# .github/workflows/cost-regression-gate.yml
name: cost-regression-gate

on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  cost-regression-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync --frozen

      - name: Snapshot current run's cost distribution
        run: >
          uv run tracegauge snapshot
          --entrypoint my_eval_suite:run_eval_and_return_store
          --output current.json

      # eval_baselines/cost_baseline.json is checked into the repo (or
      # restored via actions/cache / actions/download-artifact from a
      # previous main-branch run) -- a known-good cost distribution to
      # gate future runs against.
      - name: Compare against baseline (fails the build on regression)
        run: >
          uv run tracegauge check
          --baseline eval_baselines/cost_baseline.json
          --current current.json
          --confidence 0.95
          --min-effect-usd 0.0001
          --min-effect-pct 5.0
          --min-n 30

      # Optional: on main only, promote this run's snapshot to become the
      # new baseline for future PRs to compare against.
      - name: Update baseline on main
        if: github.ref == 'refs/heads/main' && success()
        run: cp current.json eval_baselines/cost_baseline.json
```

## Exit codes

`tracegauge check` distinguishes three outcomes by exit code -- a CI step
should treat all non-zero as build-failing, but a human reading job logs
should not conflate "regressed" with "couldn't tell":

| Exit code | Meaning |
|---|---|
| `0` | No significant regression -- gate passes. |
| `1` | Regression detected: the cost increase is BOTH statistically significant (95% bootstrap CI excludes zero) AND practically significant (clears `--min-effect-usd` or `--min-effect-pct`). |
| `3` | Insufficient data: either the baseline or current snapshot has fewer than `--min-n` (default 30) priced invocations. Refuses to emit a statistically meaningless verdict rather than silently passing or failing. |

(argparse itself uses exit code `2` for malformed CLI invocations, e.g. a
missing required flag -- `3`, not `2`, is used for insufficient-data to
keep it distinguishable.)

## Statistical methodology, in one paragraph

`tracegauge check` runs a percentile bootstrap (10,000 resamples by
default, seeded for reproducibility) on the difference in per-invocation
mean cost between the baseline and current snapshots -- never a naive
point-estimate delta. A regression requires BOTH: (1) the bootstrap CI's
lower bound is above zero (statistically significant increase), AND (2) the
observed effect size clears a configurable practical-significance floor in
absolute USD or relative percent (so a statistically-significant-but-tiny
delta from a huge sample doesn't fail a build on its own). Below 30
invocations per group, no verdict is emitted at all -- see
`adk_tracegauge._regression`'s module docstring for the full methodology
and the n>=30 rationale.
