"""adk_tracegauge/_cli.py — `tracegauge` console entry point.

Two subcommands:

    tracegauge snapshot --entrypoint <module:callable> --output <path>
    tracegauge check --baseline <path> --current <path> [options]

**Why `snapshot` takes an `--entrypoint`, not a bare `--store` path.** A
``UsageStore`` only exists as live in-process state, built up by
``TraceGaugeUsagePlugin`` during an actual eval/agent run -- there is
nothing on disk to point a fresh CLI process at. ``--entrypoint`` names a
zero-argument callable (``module.path:function_name``, importable on
``sys.path``/``PYTHONPATH``) that this command imports and calls; that
callable is expected to run your eval (e.g. call
``AgentEvaluator.evaluate()`` or drive your own ``Runner``), which
populates ``adk_tracegauge.DEFAULT_USAGE_STORE`` as a side effect via the
plugin exactly as it would in a real eval run. The entrypoint may instead
return a ``UsageStore`` directly (e.g. if you built one explicitly with
``store=`` overrides in tests) -- if it does, that returned store is
snapshotted instead of the default one.

**Exit codes for `check`** (distinct on purpose -- see the work item's own
requirement that "regression detected" and "insufficient data" not be
conflated):

    0 -- no significant regression (gate passes)
    1 -- regression detected (statistically AND practically significant)
    3 -- insufficient data (either group has fewer than --min-n invocations
         -- refuses to emit a statistically meaningless verdict)

(argparse itself uses exit code 2 for malformed CLI invocations -- e.g. a
missing required flag -- so 3, not 2, is used for insufficient-data to keep
it distinguishable from an argument-parsing error, not just from the other
two verdicts.)

**GitHub Actions usage** -- a full, copy-pasteable workflow demonstrating
run-eval -> snapshot -> compare -> fail-build-on-regression lives at
``docs/ci-snippet.md`` (single canonical source -- W5's README rewrite pulls
from it verbatim rather than re-deriving it).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from ._regression import (
    DEFAULT_CONFIDENCE,
    DEFAULT_MIN_EFFECT_PCT,
    DEFAULT_MIN_EFFECT_USD,
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    MIN_N_DEFAULT,
    evaluate_regression,
)
from ._store import DEFAULT_USAGE_STORE, UsageStore
from .snapshot import read_snapshot, write_snapshot

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_INSUFFICIENT_DATA = 3


def _resolve_entrypoint(spec: str) -> UsageStore:
    """Imports and calls a `module.path:callable` entrypoint, returning the
    UsageStore to snapshot -- either the callable's own return value (if it
    returned a UsageStore) or DEFAULT_USAGE_STORE (if the callable populated
    the shared singleton as a side effect instead, the common case for a
    real eval run using TraceGaugeUsagePlugin's own default wiring).
    """
    if ":" not in spec:
        raise SystemExit(
            f"--entrypoint must be of the form 'module.path:callable_name', got {spec!r}"
        )
    module_name, _, func_name = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise SystemExit(f"--entrypoint: could not import module {module_name!r}: {e}") from e
    try:
        func = getattr(module, func_name)
    except AttributeError as e:
        raise SystemExit(
            f"--entrypoint: module {module_name!r} has no attribute {func_name!r}: {e}"
        ) from e
    if not callable(func):
        raise SystemExit(f"--entrypoint: {spec!r} is not callable")

    result = func()
    if isinstance(result, UsageStore):
        return result
    return DEFAULT_USAGE_STORE


def _cmd_snapshot(args: argparse.Namespace) -> int:
    store = _resolve_entrypoint(args.entrypoint)
    snapshot = write_snapshot(store, args.output)
    skip_note = f", {len(snapshot.skipped)} skipped (unpriceable)" if snapshot.skipped else ""
    print(
        f"tracegauge snapshot: wrote {len(snapshot.records)} record(s) to {args.output}{skip_note}"
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    baseline = read_snapshot(args.baseline)
    current = read_snapshot(args.current)

    result = evaluate_regression(
        baseline.costs(),
        current.costs(),
        confidence=args.confidence,
        min_effect_usd=args.min_effect_usd,
        min_effect_pct=args.min_effect_pct,
        min_n=args.min_n,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    print(result.report())

    if result.status == "insufficient_data":
        return EXIT_INSUFFICIENT_DATA
    if result.status == "regression":
        return EXIT_REGRESSION
    return EXIT_PASS


def build_parser() -> argparse.ArgumentParser:
    """Builds the `tracegauge` argument parser -- factored out from `main`
    so tests can exercise argument parsing in isolation, without invoking
    a subcommand's actual side effects."""
    parser = argparse.ArgumentParser(
        prog="tracegauge",
        description="adk-tracegauge's CI cost-regression gate for ADK evals.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_snapshot = subparsers.add_parser(
        "snapshot",
        help="Run an eval entrypoint and write its captured cost distribution to a JSON file.",
    )
    p_snapshot.add_argument(
        "--entrypoint",
        required=True,
        help="'module.path:callable_name' -- a zero-arg callable that runs your eval.",
    )
    p_snapshot.add_argument(
        "--output", required=True, type=Path, help="Path to write the snapshot JSON."
    )
    p_snapshot.set_defaults(func=_cmd_snapshot)

    p_check = subparsers.add_parser(
        "check",
        help="Compare a current run's snapshot against a baseline; fail on cost regression.",
    )
    p_check.add_argument(
        "--baseline", required=True, type=Path, help="Path to the baseline snapshot JSON."
    )
    p_check.add_argument(
        "--current", required=True, type=Path, help="Path to the current run's snapshot JSON."
    )
    p_check.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help=f"Bootstrap CI confidence level, in (0, 1). Default {DEFAULT_CONFIDENCE}.",
    )
    p_check.add_argument(
        "--min-effect-usd",
        type=float,
        default=DEFAULT_MIN_EFFECT_USD,
        help=(
            "Minimum absolute USD mean-cost increase to count as a practically "
            f"significant regression (OR'd with --min-effect-pct). Default {DEFAULT_MIN_EFFECT_USD}."
        ),
    )
    p_check.add_argument(
        "--min-effect-pct",
        type=float,
        default=DEFAULT_MIN_EFFECT_PCT,
        help=(
            "Minimum relative mean-cost increase (percent) to count as a practically "
            f"significant regression (OR'd with --min-effect-usd). Default {DEFAULT_MIN_EFFECT_PCT}."
        ),
    )
    p_check.add_argument(
        "--min-n",
        type=int,
        default=MIN_N_DEFAULT,
        help=(
            "Minimum invocations required in EACH of baseline/current before a verdict "
            f"is emitted (else insufficient_data, exit {EXIT_INSUFFICIENT_DATA}). Default {MIN_N_DEFAULT}."
        ),
    )
    p_check.add_argument(
        "--n-boot",
        type=int,
        default=DEFAULT_N_BOOT,
        help=f"Bootstrap resample count. Default {DEFAULT_N_BOOT}.",
    )
    p_check.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Bootstrap RNG seed, for reproducible verdicts across runs. Default {DEFAULT_SEED}.",
    )
    p_check.set_defaults(func=_cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    """`tracegauge` console entry point (see [project.scripts] in pyproject.toml)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["EXIT_INSUFFICIENT_DATA", "EXIT_PASS", "EXIT_REGRESSION", "build_parser", "main"]
