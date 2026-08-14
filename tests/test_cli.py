"""Tests for the `tracegauge` console entry point (_cli.py): argument
parsing in isolation, and the two subcommands' end-to-end behavior
(snapshot creation, check exit codes 0/1/3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adk_tracegauge._cli import (
    EXIT_INSUFFICIENT_DATA,
    EXIT_PASS,
    EXIT_REGRESSION,
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
    assert args.confidence == pytest.approx(0.95)
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
        ]
    )
    assert args.confidence == pytest.approx(0.9)
    assert args.min_effect_usd == pytest.approx(0.5)
    assert args.min_effect_pct == pytest.approx(10.0)
    assert args.min_n == 50
    assert args.n_boot == 500
    assert args.seed == 7


def test_parser_requires_a_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


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
