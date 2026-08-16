"""adk_tracegauge/_cli.py — `adk-tracegauge` console entry point.

Two subcommands:

    adk-tracegauge snapshot --entrypoint <module:callable> --output <path>
    adk-tracegauge check --baseline <path> --current <path> [options]

**Why `snapshot` takes an `--entrypoint`, not a bare `--store` path.** A
``UsageStore`` only exists as live in-process state, built up by
``TraceGaugeUsagePlugin`` during an actual eval/agent run -- there is
nothing on disk to point a fresh CLI process at. ``--entrypoint`` names a
zero-argument callable (``module.path:function_name``, importable on
``sys.path``/``PYTHONPATH``) that this command imports and calls -- your
current working directory is inserted onto ``sys.path`` first if it isn't
already there (Phase 3 B7: found and fixed because the installed
``adk-tracegauge`` console script, unlike ``python -m adk_tracegauge._cli``,
does not get cwd on ``sys.path`` for free), so a module sitting next to
where you run the command resolves without any extra ``PYTHONPATH`` setup;
that callable is expected to run your eval (e.g. call
``AgentEvaluator.evaluate()`` or drive your own ``Runner``), which
populates ``adk_tracegauge.DEFAULT_USAGE_STORE`` as a side effect via the
plugin exactly as it would in a real eval run. The entrypoint may instead
return a ``UsageStore`` directly (e.g. if you built one explicitly with
``store=`` overrides in tests) -- if it does, that returned store is
snapshotted instead of the default one.

**`--mode` for `check`** (Phase 3 B4, re-keyed Phase 4 R2, **default policy
changed Phase 7 U1**): `auto` (default), `two-sample`, or `paired`.
`two-sample` (`evaluate_regression`) -- always available, works with any
snapshot -- is the original Phase 2 W4 method (two independent samples).
`paired` (`evaluate_regression_paired`) is substantially more statistically
powerful at the same n (Phase 3 B4/Phase 4 R2: 100% vs 0% detection on a
realistic case-correlated +10% fixture at n=25 -- see `_regression.py`'s
module docstring), but requires a real pairing key matched between both
snapshots' records -- resolved by `snapshot.py`'s `resolve_pairing` via a
fallback chain: (1) `eval_case_id`, populated when `adk-tracegauge snapshot
--eval-history <path>` was used for both runs (works with the DEFAULT `adk
eval` CLI flow, no .evalset.json changes needed); (2) `session_id`, when a
hand-rolled harness pinned `runner.run_async(session_id=
<stable-per-eval-case-id>, ...)` in both runs (B4's original mechanism); (3)
neither -- `check` refuses with a clear error naming the actual overlap
count on whichever key was attempted if `--mode paired` is requested
explicitly but fewer than `--min-n` keys overlap (1.2).

**`auto` (the default) PREFERS paired, not two-sample** (Phase 7 U1, 1.1):
paired is used whenever a pairing key resolves with overlap >= `--min-n`;
two-sample is the FALLBACK, not the default expectation, for any snapshot
pair that can't clear that bar. This is a re-affirmed, not a new, threshold
-- U1 re-examined whether the bar for PREFERRING paired should be lower
than `--min-n` now that paired is the preferred path (rather than an
opt-in bonus) and kept it identical, on real evidence, not by default: (a)
`min_n`'s statistical job is bootstrap/CLT coverage validity, a property of
how many values get resampled, not of whether they're paired deltas or two
independent groups -- nothing about pairing changes that; (b) Phase 4 R2's
own measurement found paired mode's false-positive rate at n=25 (5.5%,
11/200) was *not* better than two-sample's (4.0%, 8/200) on the identical
generator -- see `tests/test_regression_power.py`'s
`test_paired_comparison_false_positive_rate_is_not_wildly_miscalibrated` --
so there is no measured basis for trusting a paired verdict at a smaller n
than two-sample itself requires. See `_paired_mode_viable`'s docstring for
the full reasoning.

**Partial-overlap policy (Phase 7 U1, 1.4)** -- a pairing key can resolve
with SOME overlap that is still below `--min-n` (e.g. 3 of 32 eval cases
paired): this is deliberately NOT treated as "a key resolved, use it"
per 1.1's literal wording -- a paired sample that small can't support a
meaningful bootstrap CI (the identical `min_n` reasoning
`evaluate_regression_paired` itself already enforces). `auto` falls back to
`two-sample` over the FULL baseline/current distributions (not a mix of the
matched subset and the rest, which would double-count); `--mode paired`
requested explicitly still fails closed (1.2) -- an explicit request for
the more powerful method is never silently substituted. Either way, the
printed fallback message distinguishes "no pairing key resolved at all"
from "a key resolved but too few pairs" (see `_cmd_check`), so a caller can
tell which case they're in.

Either way -- paired selected, or the two-sample fallback, for whatever
reason -- the ACTUAL mode AND the ACTUAL key used (or why not) are always
printed, never silently assumed.

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
    # automatically (Python's own `-m` behavior); the installed `adk-tracegauge`
    # console-script entry point does NOT -- its sys.path[0] is the venv's
    # Scripts/ dir instead. Without this, the exact README quickstart
    # command (`adk-tracegauge snapshot --entrypoint my_eval_suite:...` run from
    # a plain project directory) fails with "could not import module" even
    # though the file is right there in cwd -- a real gap found during
    # Phase 3 B7's fresh-pip-install verification (running from a source
    # checkout or via `uv run` already has cwd on sys.path one way or
    # another, which is why this was never observed before that test).
    # Inserted explicitly so --entrypoint resolution behaves identically
    # regardless of how `adk-tracegauge` was invoked.
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
        f"adk-tracegauge snapshot: wrote {len(snapshot.records)} record(s) to "
        f"{args.output}{skip_note}{resolved_note}"
    )
    return 0


def _paired_mode_viable(matched_count: int, min_n: int) -> bool:
    """Phase 7 U1, 1.1/1.4: the single, named place `--mode auto`'s
    preference for paired over two-sample is decided -- whether a resolved
    pairing key's overlap (``matched_count``) is enough to trust a paired
    verdict at all.

    Deliberately the SAME bar as ``min_n`` (default ``MIN_N_DEFAULT=30``),
    not a separate, lower one -- re-examined explicitly (not inherited by
    default) now that paired is the PREFERRED default path rather than an
    opt-in bonus, and kept identical for two evidence-based reasons:

    1. ``min_n``'s actual statistical job (see ``_regression.py``'s
       ``MIN_N_DEFAULT`` docstring) is bootstrap/CLT COVERAGE validity -- a
       property of how many values a percentile bootstrap resamples, not of
       whether those values are paired deltas (one sequence) or two
       independent groups. Nothing about pairing changes how many points
       get resampled or how well that resampling's coverage behaves at a
       given n.
    2. Phase 4 R2's own measurement is direct evidence against a lower bar,
       not just a theoretical one: at n=25 (below min_n=30), paired mode's
       measured false-positive rate (5.5%, 11/200) was *not* better than
       two-sample's (4.0%, 8/200) on the identical case-correlated
       generator -- see
       ``tests/test_regression_power.py::test_paired_comparison_false_positive_rate_is_not_wildly_miscalibrated``.
       Paired mode is dramatically more POWERFUL at a given n once it
       clears this bar (100% vs 0% detection on the same fixture, same
       test file) -- but there is no measured evidence it is more
       RELIABLE (lower false-positive rate) below it, so there is no
       statistical basis for trusting a paired verdict at a smaller n than
       two-sample itself requires.

    A separate question -- what to DO when a key resolves with SOME overlap
    below this bar -- is 1.4's "partial-overlap policy", decided in
    `_cmd_check`/the module docstring, not here.
    """
    return matched_count >= min_n


def _resolve_check_mode(
    mode: str, baseline: Snapshot, current: Snapshot, min_n: int, agent: str | None = None
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

    "auto" (the default, Phase 7 U1 -- PAIRED-preferred, not two-sample-first):
    uses "paired" iff `_paired_mode_viable` says the best-available pairing
    key's overlap is enough to trust (see that function for the full
    reasoning); otherwise "two-sample". Always deterministic given the two
    snapshots' contents.

    ``agent`` (LL2.3, optional): forwarded to ``resolve_pairing`` -- see its
    own docstring. Agent-scoping never changes WHICH mode/key gets picked
    (that decision is still driven purely by session_id/eval_case_id
    overlap, unaffected by cost values), only which dollar figures are paired.
    """
    paired_baseline, paired_current, matched, resolved_key = resolve_pairing(
        baseline, current, agent=agent
    )
    if mode == "paired":
        return "paired", paired_baseline, paired_current, matched, resolved_key
    if mode == "two-sample":
        return "two-sample", paired_baseline, paired_current, matched, resolved_key
    # mode == "auto"
    resolved_mode = "paired" if _paired_mode_viable(len(matched), min_n) else "two-sample"
    return resolved_mode, paired_baseline, paired_current, matched, resolved_key


def _cmd_check(args: argparse.Namespace) -> int:
    baseline = read_snapshot(args.baseline)
    current = read_snapshot(args.current)
    agent: str | None = getattr(args, "agent", None)
    agent_note = f" [agent={agent}]" if agent is not None else ""

    resolved_mode, paired_baseline, paired_current, matched_keys, resolved_key = (
        _resolve_check_mode(args.mode, baseline, current, args.min_n, agent=agent)
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
                "Pass --eval-history <path-to-adk-eval's-.evalset_result.json> to `adk-tracegauge "
                "snapshot` for both runs (resolves the stable, authored eval case id -- works "
                "with the default `adk eval` CLI flow), or pin a stable session_id via "
                "runner.run_async(session_id=...) in a hand-rolled harness, or pass "
                "--mode two-sample."
            )
        print(
            f"adk-tracegauge check: mode=paired{agent_note} (key={resolved_key}, {len(matched_keys)} "
            f"overlapping {resolved_key}s matched between baseline and current)"
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
        fallback_note = ""
        if args.mode == "auto":
            if resolved_key == "none":
                # Phase 7 U1, 1.4: distinguish "no pairing key available at all"
                # from "a key resolved but too few pairs" (below) -- the two
                # have different remedies and shouldn't be reported the same way.
                fallback_note = (
                    " (two-sample, no pairing key available -- neither eval_case_id "
                    "nor session_id has any overlap between --baseline and --current, "
                    "falling back to two-sample; see snapshot.py's docstring for how to "
                    "enable paired comparison)"
                )
            else:
                fallback_note = (
                    f" (--mode auto: pairing key {resolved_key} resolved but only "
                    f"{len(matched_keys)} overlapping match(es) -- below --min-n="
                    f"{args.min_n}, the same reliability bar evaluate_regression_paired "
                    "itself requires (a handful of matched cases is not a statistically "
                    "usable paired sample -- see _paired_mode_viable's docstring), "
                    "falling back to two-sample)"
                )
        print(f"adk-tracegauge check: mode=two-sample{agent_note}{fallback_note}")
        baseline_costs = baseline.costs_for_agent(agent) if agent is not None else baseline.costs()
        current_costs = current.costs_for_agent(agent) if agent is not None else current.costs()
        result = evaluate_regression(
            baseline_costs,
            current_costs,
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


def _cmd_quickstart(args: argparse.Namespace) -> int:
    from adk_tracegauge._quickstart import run_quickstart

    return run_quickstart()


def build_parser() -> argparse.ArgumentParser:
    """Builds the `adk-tracegauge` argument parser -- factored out from `main`
    so tests can exercise argument parsing in isolation, without invoking
    a subcommand's actual side effects."""
    parser = argparse.ArgumentParser(
        prog="adk-tracegauge",
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
            "session_id, so `adk-tracegauge check --mode paired` can pair by eval case id even "
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
            "`adk-tracegauge snapshot --eval-history`) or session_id (see snapshot.py docstring) -- "
            "fails with an actionable error if requested explicitly and too few keys overlap. "
            "'auto' (default, Phase 7 U1): PREFERS paired -- uses it whenever enough keys "
            "overlap (>= --min-n) on the best-available key -- falling back to two-sample only "
            "when no key resolves or the overlap is below that bar. The resolved mode AND key "
            "(or why not) are always printed."
        ),
    )
    p_check.add_argument(
        "--agent",
        type=str,
        default=None,
        help=(
            "LL2: scope the regression gate to a single agent's own cost, via "
            "SnapshotRecord.cost_by_agent (populated only by snapshots taken with a "
            "version of adk-tracegauge that captures agent_name -- schema_version >= 3; an "
            "older snapshot reports zero cost for every agent, not an error). Matches "
            "callback_context.agent_name, i.e. the ADK agent's own `name=` -- for the "
            "common AgentTool-delegation case this is the delegated sub-agent's name "
            "(e.g. 'capital_finder' in examples/02_subagent_rollup.py), not the parent's; "
            "for agent transfer/handoff within one invocation, whichever agent(s) actually "
            "made each priced call. Works in both --mode two-sample (filters to just this "
            "agent's own invocations) and --mode paired/auto (pairs each session/eval-case's "
            "cost attributable to just this agent). No effect on WHICH mode/pairing key is "
            "chosen -- only which dollar figures are compared."
        ),
    )
    p_check.set_defaults(func=_cmd_check)

    subparsers.add_parser(
        "quickstart",
        help=(
            "Run a deterministic, self-contained demo (no API key, no network call, no "
            "ADK app of your own) that fires a real regression gate immediately."
        ),
    ).set_defaults(func=_cmd_quickstart)

    return parser


def main(argv: list[str] | None = None) -> int:
    """`adk-tracegauge` console entry point (see [project.scripts] in pyproject.toml)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["EXIT_INSUFFICIENT_DATA", "EXIT_PASS", "EXIT_REGRESSION", "build_parser", "main"]
