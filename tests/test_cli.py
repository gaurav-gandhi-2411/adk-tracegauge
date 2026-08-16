"""Tests for the `adk-tracegauge` console entry point (_cli.py): argument
parsing in isolation, and the two subcommands' end-to-end behavior
(snapshot creation, check exit codes 0/1/3).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from adk_tracegauge._cli import (
    EXIT_INSUFFICIENT_DATA,
    EXIT_PASS,
    EXIT_REGRESSION,
    _paired_mode_viable,
    _resolve_entrypoint,
    build_parser,
    main,
)
from adk_tracegauge._store import DEFAULT_USAGE_STORE, CapturedCall, UsageStore
from adk_tracegauge.snapshot import write_snapshot


def _call(model: str = "gemini-2.5-flash", prompt: int = 1000, output: int = 200) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=0,
        total_token_count=prompt + output,
    )


# --- entrypoints used by _resolve_entrypoint tests below, referenced by
# name as "test_cli:_fixture_*" (pytest's default import mode makes this
# test module importable under its own bare name -- see _resolve_entrypoint
# tests). ---------------------------------------------------------------


def _fixture_returns_explicit_store() -> UsageStore:
    store = UsageStore()
    store.record("inv-1", _call())
    return store


def _fixture_populates_default_store_and_returns_none() -> None:
    DEFAULT_USAGE_STORE.record("inv-default", _call())


def _fixture_returns_store_with_session_id() -> UsageStore:
    store = UsageStore()
    store.record("inv-1", _call())
    store.record_session("inv-1", "sess-a")
    return store


_fixture_not_callable_attr = 42  # not callable, used to test the "not callable" branch


@pytest.fixture(autouse=True)
def _clear_default_store():
    DEFAULT_USAGE_STORE.clear()
    yield
    DEFAULT_USAGE_STORE.clear()


# --- argument parsing (isolated, no subcommand side effects) ------------


def test_parser_snapshot_subcommand_required_args():
    parser = build_parser()
    args = parser.parse_args(["snapshot", "--entrypoint", "my_mod:my_func", "--output", "out.json"])
    assert args.command == "snapshot"
    assert args.entrypoint == "my_mod:my_func"
    assert args.output == Path("out.json")


def test_parser_snapshot_missing_required_flag_exits_nonzero():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["snapshot", "--output", "out.json"])
    assert exc_info.value.code != 0


def test_parser_check_required_args_and_defaults():
    parser = build_parser()
    args = parser.parse_args(["check", "--baseline", "b.json", "--current", "c.json"])
    assert args.command == "check"
    assert args.baseline == Path("b.json")
    assert args.current == Path("c.json")
    assert args.confidence == pytest.approx(0.98)  # Phase 5 S4: default tightened 0.95 -> 0.98
    assert args.min_effect_usd == pytest.approx(0.0001)
    assert args.min_effect_pct == pytest.approx(5.0)
    assert args.min_n == 30
    assert args.n_boot == 10_000
    assert args.seed == 42


def test_parser_check_overrides_every_optional_flag():
    parser = build_parser()
    args = parser.parse_args(
        [
            "check",
            "--baseline",
            "b.json",
            "--current",
            "c.json",
            "--confidence",
            "0.9",
            "--min-effect-usd",
            "0.5",
            "--min-effect-pct",
            "10",
            "--min-n",
            "50",
            "--n-boot",
            "500",
            "--seed",
            "7",
            "--mode",
            "paired",
        ]
    )
    assert args.confidence == pytest.approx(0.9)
    assert args.min_effect_usd == pytest.approx(0.5)
    assert args.min_effect_pct == pytest.approx(10.0)
    assert args.min_n == 50
    assert args.n_boot == 500
    assert args.seed == 7
    assert args.mode == "paired"


def test_parser_check_mode_defaults_to_auto():
    parser = build_parser()
    args = parser.parse_args(["check", "--baseline", "b.json", "--current", "c.json"])
    assert args.mode == "auto"


def test_parser_check_mode_rejects_invalid_choice():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["check", "--baseline", "b.json", "--current", "c.json", "--mode", "bogus"]
        )


def test_parser_requires_a_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_quickstart_subcommand_takes_no_required_args():
    # HH1.1: zero-config by design -- must parse with no flags at all.
    parser = build_parser()
    args = parser.parse_args(["quickstart"])
    assert args.command == "quickstart"


# --- _resolve_entrypoint --------------------------------------------------


def test_resolve_entrypoint_rejects_spec_without_colon():
    with pytest.raises(SystemExit, match="module.path:callable_name"):
        _resolve_entrypoint("no_colon_here")


def test_resolve_entrypoint_rejects_unimportable_module():
    with pytest.raises(SystemExit, match="could not import"):
        _resolve_entrypoint("totally_nonexistent_module_xyz:func")


def test_resolve_entrypoint_rejects_missing_attribute():
    with pytest.raises(SystemExit, match="no attribute"):
        _resolve_entrypoint("test_cli:this_function_does_not_exist")


def test_resolve_entrypoint_rejects_non_callable_attribute():
    with pytest.raises(SystemExit, match="not callable"):
        _resolve_entrypoint("test_cli:_fixture_not_callable_attr")


def test_resolve_entrypoint_returns_explicit_store():
    store = _resolve_entrypoint("test_cli:_fixture_returns_explicit_store")
    assert store.invocation_ids() == ["inv-1"]


def test_resolve_entrypoint_falls_back_to_default_store_when_none_returned():
    store = _resolve_entrypoint("test_cli:_fixture_populates_default_store_and_returns_none")
    assert store is DEFAULT_USAGE_STORE
    assert "inv-default" in store.invocation_ids()


def test_resolve_entrypoint_puts_cwd_on_syspath_for_the_bare_console_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Phase 3 B7: found via a genuinely fresh pip-installed wheel run from
    an external directory outside this repo. The installed `adk-tracegauge`
    console-script entry point does NOT get cwd on sys.path automatically
    -- unlike `python -m adk_tracegauge._cli` (Python's own `-m` behavior)
    and unlike this test suite's own pytest run (`pythonpath = [".", "src",
    "scripts"]` in pyproject.toml already covers the repo root) -- both of
    which masked this gap until it was tested from a real standalone
    install. Simulates that exact scenario with a module this test process
    genuinely cannot already see: written fresh into a `tmp_path` the test
    then `cd`s into, confirmed absent from `sys.path` beforehand (the actual
    pre-fix failure mode), then resolved successfully after.
    """
    module_path = tmp_path / "a_fresh_standalone_entrypoint_module.py"
    module_path.write_text(
        "from adk_tracegauge._store import CapturedCall, UsageStore\n"
        "def build():\n"
        "    store = UsageStore()\n"
        "    store.record('inv-standalone', CapturedCall(\n"
        "        model_version='gemini-2.5-flash', prompt_token_count=1000,\n"
        "        candidates_token_count=200, cached_content_token_count=0,\n"
        "        total_token_count=1200))\n"
        "    return store\n"
    )
    monkeypatch.chdir(tmp_path)
    assert str(tmp_path) not in sys.path  # the actual pre-fix failure mode
    try:
        store = _resolve_entrypoint("a_fresh_standalone_entrypoint_module:build")
        assert store.invocation_ids() == ["inv-standalone"]
        assert str(tmp_path) in sys.path  # the fix: cwd is now importable
    finally:
        sys.modules.pop("a_fresh_standalone_entrypoint_module", None)
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


# --- end-to-end: snapshot subcommand -------------------------------------


def test_cmd_snapshot_end_to_end_writes_a_valid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out_path = tmp_path / "snap.json"
    exit_code = main(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_explicit_store",
            "--output",
            str(out_path),
        ]
    )
    assert exit_code == 0
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["records"][0]["invocation_id"] == "inv-1"
    captured = capsys.readouterr()
    assert "wrote 1 record" in captured.out


# --- end-to-end: snapshot subcommand --eval-history (Phase 4 R2) ----------


def _write_eval_history_file(path: Path, case_session_pairs: list[tuple[str, str]]) -> None:
    from google.adk.evaluation.eval_result import EvalCaseResult, EvalSetResult
    from google.adk.evaluation.evaluator import EvalStatus

    result = EvalSetResult(
        eval_set_result_id="app_my_eval_set_123",
        eval_set_id="my_eval_set",
        eval_case_results=[
            EvalCaseResult(
                eval_set_id="my_eval_set",
                eval_id=eval_id,
                final_eval_status=EvalStatus.PASSED,
                overall_eval_metric_results=[],
                eval_metric_result_per_invocation=[],
                session_id=session_id,
            )
            for eval_id, session_id in case_session_pairs
        ],
    )
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def test_cmd_snapshot_with_eval_history_resolves_eval_case_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out_path = tmp_path / "snap.json"
    history_path = tmp_path / "app_my_eval_set_123.evalset_result.json"
    _write_eval_history_file(history_path, [("case_1", "sess-a")])

    exit_code = main(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(out_path),
            "--eval-history",
            str(history_path),
        ]
    )

    assert exit_code == 0
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["records"][0]["session_id"] == "sess-a"
    assert raw["records"][0]["eval_case_id"] == "case_1"
    captured = capsys.readouterr()
    assert "1/1 record(s) resolved to a real eval_case_id" in captured.out


def test_cmd_snapshot_without_eval_history_leaves_eval_case_id_unpopulated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out_path = tmp_path / "snap.json"

    exit_code = main(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(out_path),
        ]
    )

    assert exit_code == 0
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["records"][0]["eval_case_id"] is None
    captured = capsys.readouterr()
    assert "resolved to a real eval_case_id" not in captured.out


# --- end-to-end: check subcommand exit codes -----------------------------


def _write_snapshot_with_costs(
    path: Path, n: int, model: str = "gemini-2.5-flash", prompt: int = 1000
):
    store = UsageStore()
    for i in range(n):
        store.record(f"inv-{i}", _call(model=model, prompt=prompt))
    write_snapshot(store, path)


def test_cmd_check_end_to_end_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    _write_snapshot_with_costs(baseline_path, n=40, prompt=1000)
    _write_snapshot_with_costs(current_path, n=40, prompt=1000)  # identical distribution

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_PASS
    captured = capsys.readouterr()
    assert "PASS" in captured.out


def test_cmd_check_end_to_end_regression(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    _write_snapshot_with_costs(baseline_path, n=40, prompt=1000)
    _write_snapshot_with_costs(
        current_path, n=40, prompt=3000
    )  # 3x the input tokens -> much pricier

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_REGRESSION
    captured = capsys.readouterr()
    assert "REGRESSION" in captured.out


def test_cmd_check_end_to_end_insufficient_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    _write_snapshot_with_costs(baseline_path, n=5, prompt=1000)
    _write_snapshot_with_costs(current_path, n=5, prompt=1000)

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_INSUFFICIENT_DATA
    captured = capsys.readouterr()
    assert "INSUFFICIENT DATA" in captured.out


# --- end-to-end: check subcommand --mode (Phase 3 B4) ---------------------


def _write_snapshot_with_session_ids(
    path: Path,
    session_costs: dict[str, float],
    model: str = "gemini-2.5-flash",
):
    """Writes a snapshot with one record per (session_id, cost) pair by
    building the snapshot JSON directly (bypassing pricing entirely) --
    --mode tests only need session_id + cost_usd, not a real priced call."""
    from adk_tracegauge.snapshot import SNAPSHOT_SCHEMA_VERSION

    records = [
        {
            "invocation_id": f"inv-{session_id}",
            "session_id": session_id,
            "cost_usd": cost,
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_cache_read": 0,
            "models": [model],
            "call_count": 1,
        }
        for session_id, cost in session_costs.items()
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "created_at": "2026-01-01T00:00:00+00:00",
                "records": records,
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )


def test_cmd_check_mode_two_sample_explicit_ignores_session_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    _write_snapshot_with_costs(baseline_path, n=40, prompt=1000)
    _write_snapshot_with_costs(current_path, n=40, prompt=1000)

    exit_code = main(
        [
            "check",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--mode",
            "two-sample",
        ]
    )

    assert exit_code == EXIT_PASS
    captured = capsys.readouterr()
    assert "mode=two-sample" in captured.out
    assert "method=two_sample" in captured.out


def test_cmd_check_mode_auto_falls_back_to_two_sample_with_no_session_overlap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    _write_snapshot_with_costs(baseline_path, n=40, prompt=1000)  # no session_id on any record
    _write_snapshot_with_costs(current_path, n=40, prompt=1000)

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_PASS
    captured = capsys.readouterr()
    assert "mode=two-sample" in captured.out
    assert "falling back" in captured.out


@pytest.mark.parametrize(
    ("matched_count", "min_n", "expected"),
    [
        (30, 30, True),  # exactly at the bar -- viable
        (29, 30, False),  # one short -- not viable
        (0, 30, False),  # no overlap at all
        (100, 30, True),  # well above the bar
        (3, 30, False),  # 1.4's own motivating example: "some pairs, not enough"
    ],
)
def test_paired_mode_viable_boundary(matched_count: int, min_n: int, expected: bool):
    """Phase 7 U1, 1.1/1.4: `_paired_mode_viable` is the single named place
    `--mode auto`'s paired-vs-two-sample threshold is decided -- pinned here
    at the boundary so a future change to that threshold is a deliberate,
    reviewed edit, not an accidental off-by-one."""
    assert _paired_mode_viable(matched_count, min_n) is expected


def test_cmd_check_mode_auto_falls_back_to_two_sample_with_partial_session_overlap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Phase 7 U1, 1.4: SOME pairs exist (a key genuinely resolved) but the
    overlap (3) is well below --min-n's default of 30 -- distinct from the
    "zero overlap at all" case above (test_..._with_no_session_overlap),
    which prints a different message. `auto` still falls back to
    two-sample (using the FULL baseline/current distributions, not just the
    3 matched records), and the printed message must name the resolved key
    and its actual overlap count -- not claim "no pairing key available"
    when a key genuinely did resolve, just not with enough overlap.
    """
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    # 10 records on each side, but only 3 session_ids overlap ("case-1..3")
    # -- resolve_pairing finds a real key, just not enough of it. The other
    # 7 records on each side have DIFFERENT ids (never overlapping), so a
    # two-sample fallback still has a full n=10-per-group population to work
    # with -- proving the fallback uses the FULL distribution, not just the
    # 3 matched records (which alone would be below --min-n=5 too).
    shared = {f"case-{i}": 0.01 for i in range(1, 4)}
    baseline_costs = {**shared, **{f"extra-b-{i}": 0.01 for i in range(7)}}
    current_costs = {**shared, **{f"extra-c-{i}": 0.01 for i in range(7)}}
    _write_snapshot_with_session_ids(baseline_path, baseline_costs)
    _write_snapshot_with_session_ids(current_path, current_costs)

    exit_code = main(
        ["check", "--baseline", str(baseline_path), "--current", str(current_path), "--min-n", "5"]
    )

    assert exit_code == EXIT_PASS  # full n=10-per-group two-sample population, identical costs
    captured = capsys.readouterr()
    assert "mode=two-sample" in captured.out
    assert "falling back to two-sample" in captured.out
    assert "key session_id resolved but only 3 overlapping match(es)" in captured.out
    assert "below --min-n=5" in captured.out
    # Must NOT be confused with the "no pairing key available at all" message.
    assert "no pairing key available" not in captured.out


def test_cmd_check_mode_auto_uses_paired_when_enough_session_ids_overlap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    session_costs_baseline = {f"case-{i}": 0.01 for i in range(40)}
    session_costs_current = {f"case-{i}": 0.01 for i in range(40)}  # identical -- no regression
    _write_snapshot_with_session_ids(baseline_path, session_costs_baseline)
    _write_snapshot_with_session_ids(current_path, session_costs_current)

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_PASS
    captured = capsys.readouterr()
    assert "mode=paired" in captured.out
    assert "40 overlapping" in captured.out
    assert "method=paired" in captured.out


def test_cmd_check_mode_paired_explicit_detects_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    session_costs_baseline = {f"case-{i}": 0.01 for i in range(40)}
    session_costs_current = {f"case-{i}": 0.02 for i in range(40)}  # every case +$0.01
    _write_snapshot_with_session_ids(baseline_path, session_costs_baseline)
    _write_snapshot_with_session_ids(current_path, session_costs_current)

    exit_code = main(
        [
            "check",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--mode",
            "paired",
        ]
    )

    assert exit_code == EXIT_REGRESSION
    captured = capsys.readouterr()
    assert "mode=paired" in captured.out
    assert "REGRESSION" in captured.out


def test_cmd_check_mode_paired_explicit_fails_closed_on_insufficient_overlap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    # Only 3 overlapping session_ids -- well below the default min_n=30.
    _write_snapshot_with_session_ids(
        baseline_path, {"case-1": 0.01, "case-2": 0.01, "case-3": 0.01}
    )
    _write_snapshot_with_session_ids(current_path, {"case-1": 0.01, "case-2": 0.01, "case-3": 0.01})

    with pytest.raises(SystemExit, match="requires >= 30 overlapping pairing keys"):
        main(
            [
                "check",
                "--baseline",
                str(baseline_path),
                "--current",
                str(current_path),
                "--mode",
                "paired",
            ]
        )


# --- end-to-end: check subcommand --mode, eval_case_id key (Phase 4 R2) ---


def _write_snapshot_with_eval_case_ids(
    path: Path,
    eval_case_costs: dict[str, float],
    model: str = "gemini-2.5-flash",
):
    from adk_tracegauge.snapshot import SNAPSHOT_SCHEMA_VERSION

    records = [
        {
            "invocation_id": f"inv-{eval_case_id}",
            "eval_case_id": eval_case_id,
            "cost_usd": cost,
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_cache_read": 0,
            "models": [model],
            "call_count": 1,
        }
        for eval_case_id, cost in eval_case_costs.items()
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "created_at": "2026-01-01T00:00:00+00:00",
                "records": records,
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )


def test_cmd_check_mode_auto_prefers_eval_case_id_key_and_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    eval_case_costs_baseline = {f"case-{i}": 0.01 for i in range(30)}
    eval_case_costs_current = {f"case-{i}": 0.02 for i in range(30)}  # every case +$0.01
    _write_snapshot_with_eval_case_ids(baseline_path, eval_case_costs_baseline)
    _write_snapshot_with_eval_case_ids(current_path, eval_case_costs_current)

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_REGRESSION
    captured = capsys.readouterr()
    assert "mode=paired" in captured.out
    assert "key=eval_case_id" in captured.out
    assert "30 overlapping" in captured.out


def test_cmd_check_mode_auto_prints_session_id_key_when_that_is_all_that_overlaps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    session_costs = {f"case-{i}": 0.01 for i in range(40)}
    _write_snapshot_with_session_ids(baseline_path, session_costs)
    _write_snapshot_with_session_ids(current_path, session_costs)

    exit_code = main(["check", "--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == EXIT_PASS
    captured = capsys.readouterr()
    assert "mode=paired" in captured.out
    assert "key=session_id" in captured.out
