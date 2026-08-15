"""adk_tracegauge/_cli.py — `tracegauge` console entry point.

Two subcommands:

    tracegauge snapshot --entrypoint <module:callable> --output <path>
    tracegauge check --baseline <path> --current <path> [options]

**Why `snapshot` takes an `--entrypoint`, not a bare `--store` path.** A
``UsageStore`` only exists as live in-process state, built up by
``TraceGaugeUsagePlugin`` during an actual eval/agent run -- there is
nothing on disk to point a fresh CLI process at. ``--entrypoint`` names a
zero-argument callable (``module.path:function_name``, importable on
``sys.path``/``PYTHONPATH``) that this command imports and calls -- your
current working directory is inserted onto ``sys.path`` first if it isn't
already there (Phase 3 B7: found and fixed because the installed
``tracegauge`` console script, unlike ``python -m adk_tracegauge._cli``,
does not get cwd on ``sys.path`` for free), so a module sitting next to
where you run the command resolves without any extra ``PYTHONPATH`` setup;
that callable is expected to run your eval (e.g. call
``AgentEvaluator.evaluate()`` or drive your own ``Runner``), which
populates ``adk_tracegauge.DEFAULT_USAGE_STORE`` as a side effect via the
plugin exactly as it would in a real eval run. The entrypoint may instead
return a ``UsageStore`` directly (e.g. if you built one explicitly with
``store=`` overrides in tests) -- if it does, that returned store is
snapshotted instead of the default one.

**`--mode` for `check`** (Phase 3 B4, re-keyed Phase 4 R2): `auto` (default),
`two-sample`, or `paired`. `two-sample` is the original Phase 2 W4 method
(two independent samples, `evaluate_regression`) -- always available, works
with any snapshot. `paired` (`evaluate_regression_paired`) is substantially
more statistically powerful at the same n, but requires a real pairing key
matched between both snapshots' records -- resolved by `snapshot.py`'s
`resolve_pairing` via a fallback chain: (1) `eval_case_id`, populated when
`tracegauge snapshot --eval-history <path>` was used for both runs (works
with the DEFAULT `adk eval` CLI flow, no .evalset.json changes needed); (2)
`session_id`, when a hand-rolled harness pinned `runner.run_async(session_id=
<stable-per-eval-case-id>, ...)` in both runs (B4's original mechanism); (3)
neither -- `check` refuses with a clear error naming the actual overlap
count on whichever key was attempted if `--mode paired` is requested
explicitly but fewer than `--min-n` keys overlap. `auto` uses `paired` when
the best-available key's overlap is >= `--min-n`, else transparently falls
back to `two-sample` -- either way, the ACTUAL mode AND the ACTUAL key used
are always printed, never silently assumed.

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
import os
import sys
from pathlib import Path

from ._compat import load_eval_case_ids_by_session_id
from ._regression import (
    DEFAULT_CONFIDENCE,
    DEFAULT_MIN_EFFECT_PCT,
    DEFAULT_MIN_EFFECT_USD,
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    MIN_N_DEFAULT,
    evaluate_regression,
    evaluate_regression_paired,
)
from ._store import DEFAULT_USAGE_STORE, UsageStore
from .snapshot import PairingKey, Snapshot, read_snapshot, resolve_pairing, write_snapshot

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
    # `python -m adk_tracegauge._cli` gets the caller's cwd on sys.path[0]
    # automatically (Python's own `-m` behavior); the installed `tracegauge`
    # console-script entry point does NOT -- its sys.path[0] is the venv's
    # Scripts/ dir instead. Without this, the exact README quickstart
    # command (`tracegauge snapshot --entrypoint my_eval_suite:...` run from
    # a plain project directory) fails with "could not import module" even
    # though the file is right there in cwd -- a real gap found during
    # Phase 3 B7's fresh-pip-install verification (running from a source
    # checkout or via `uv run` already has cwd on sys.path one way or
    # another, which is why this was never observed before that test).
    # Inserted explicitly so --entrypoint resolution behaves identically
    # regardless of how `tracegauge` was invoked.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
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
    eval_case_ids_by_session: dict[str, str] | None = None
    if args.eval_history is not None:
        eval_case_ids_by_session = load_eval_case_ids_by_session_id(args.eval_history)
    snapshot = write_snapshot(store, args.output, eval_case_ids_by_session=eval_case_ids_by_session)
    skip_note = f", {len(snapshot.skipped)} skipped (unpriceable)" if snapshot.skipped else ""
    resolved_note = ""
    if args.eval_history is not None:
        n_resolved = sum(1 for r in snapshot.records if r.eval_case_id is not None)
        resolved_note = (
            f", {n_resolved}/{len(snapshot.records)} record(s) resolved to a real eval_case_id "
            f"via --eval-history {args.eval_history}"
        )
    print(
        f"tracegauge snapshot: wrote {len(snapshot.records)} record(s) to "
        f"{args.output}{skip_note}{resolved_note}"
    )
    return 0


def _resolve_check_mode(
    mode: str, baseline: Snapshot, current: Snapshot, min_n: int
) -> tuple[str, list[float], list[float], list[str], PairingKey]:
    """Decides which comparison method `_cmd_check` actually runs, and
    returns (resolved_mode, paired_baseline_costs, paired_current_costs,
    matched_keys, resolved_key) -- resolved_mode is always "two-sample" or
    "paired", never "auto" (auto is resolved here, once). ``resolved_key``
    (Phase 4 R2) is the ``PairingKey`` ``resolve_pairing`` actually picked
    (``"eval_case_id"``, ``"session_id"``, or ``"none"``) -- always computed
    and returned, even in two-sample mode, so `_cmd_check` can report it in
    an `auto`-mode fallback message without a second call.

    "paired" is requested explicitly: returns "paired" regardless of overlap
    size -- `_cmd_check` itself refuses with SystemExit if the overlap is
    too small, rather than this function silently downgrading a request the
    caller was explicit about.

    "auto" (the default): uses "paired" iff the number of keys matched on
    the best-available pairing key is >= min_n (enough for
    evaluate_regression_paired to actually emit a verdict rather than
    insufficient_data); otherwise "two-sample". Always deterministic given
    the two snapshots' contents.
    """
    paired_baseline, paired_current, matched, resolved_key = resolve_pairing(baseline, current)
    if mode == "paired":
        return "paired", paired_baseline, paired_current, matched, resolved_key
    if mode == "two-sample":
        return "two-sample", paired_baseline, paired_current, matched, resolved_key
    # mode == "auto"
    resolved_mode = "paired" if len(matched) >= min_n else "two-sample"
    return resolved_mode, paired_baseline, paired_current, matched, resolved_key


def _cmd_check(args: argparse.Namespace) -> int:
    baseline = read_snapshot(args.baseline)
    current = read_snapshot(args.current)

    resolved_mode, paired_baseline, paired_current, matched_keys, resolved_key = (
        _resolve_check_mode(args.mode, baseline, current, args.min_n)
    )

    if resolved_mode == "paired":
        if args.mode == "paired" and len(matched_keys) < args.min_n:
            key_note = (
                f"key={resolved_key}"
                if resolved_key != "none"
                else "no overlapping eval_case_id or session_id found at all"
            )
            raise SystemExit(
                f"--mode paired requires >= {args.min_n} overlapping pairing keys between "
                f"--baseline and --current, but only {len(matched_keys)} matched ({key_note}). "
                "Pass --eval-history <path-to-adk-eval's-.evalset_result.json> to `tracegauge "
                "snapshot` for both runs (resolves the stable, authored eval case id -- works "
                "with the default `adk eval` CLI flow), or pin a stable session_id via "
                "runner.run_async(session_id=...) in a hand-rolled harness, or pass "
                "--mode two-sample."
            )
        print(
            f"tracegauge check: mode=paired (key={resolved_key}, {len(matched_keys)} overlapping "
            f"{resolved_key}s matched between baseline and current)"
        )
        result = evaluate_regression_paired(
            paired_baseline,
            paired_current,
            confidence=args.confidence,
            min_effect_usd=args.min_effect_usd,
            min_effect_pct=args.min_effect_pct,
            min_n=args.min_n,
            n_boot=args.n_boot,
            seed=args.seed,
        )
    else:
        print(
            "tracegauge check: mode=two-sample"
            + (
                f" (--mode auto: best-available pairing key ({resolved_key}) only has "
                f"{len(matched_keys)} overlapping match(es) < --min-n={args.min_n}, so falling "
                "back from paired -- see snapshot.py's docstring for how to enable paired "
                "comparison)"
                if args.mode == "auto"
                else ""
            )
        )
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
    p_snapshot.add_argument(
        "--eval-history",
        type=Path,
        default=None,
        help=(
            "Phase 4 R2: path to an `adk eval`-written .evalset_result.json file "
            "(<agents_dir>/<app_name>/.adk/eval_history/*.evalset_result.json) -- when given, "
            "resolves each captured invocation's stable, authored eval_case_id by joining on "
            "session_id, so `tracegauge check --mode paired` can pair by eval case id even "
            "against the default `adk eval` CLI flow (no --eval-history means eval_case_id is "
            "never populated and paired mode falls back to session_id, then two-sample)."
        ),
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
    p_check.add_argument(
        "--mode",
        choices=["auto", "two-sample", "paired"],
        default="auto",
        help=(
            "Comparison method. 'two-sample': the original independent-samples bootstrap "
            "(evaluate_regression). 'paired': a per-eval-case paired bootstrap "
            "(evaluate_regression_paired), substantially more powerful at the same n but "
            "requires a matching pairing key on both snapshots -- eval_case_id (preferred, via "
            "`tracegauge snapshot --eval-history`) or session_id (see snapshot.py docstring) -- "
            "fails with an actionable error if requested explicitly and too few keys overlap. "
            "'auto' (default): uses paired when enough keys overlap (>= --min-n) on the "
            "best-available key, else falls back to two-sample -- the resolved mode AND key are "
            "always printed."
        ),
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
