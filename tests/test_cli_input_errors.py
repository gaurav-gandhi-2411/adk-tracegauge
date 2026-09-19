"""A cold user's first real run must get a single actionable line -- not a 13-line traceback --
when a file they pointed the CLI at is missing, truncated, or not what it claims to be.

Every test asserts on the ``SystemExit`` message ``main()`` raises (what the console-script
wrapper prints to stderr and turns into exit status 1), and that it is one line. The library
functions underneath (``read_snapshot``) deliberately keep raising their typed exceptions --
covered at the bottom -- so only the CLI boundary changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_cli import _write_eval_history_for  # valid ADK eval-history fixture writer

from adk_tracegauge._cli import main
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import read_snapshot, write_snapshot


def _valid_snapshot(path: Path) -> Path:
    store = UsageStore()
    store.record(
        "inv-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1000,
            candidates_token_count=200,
            cached_content_token_count=0,
            total_token_count=1200,
        ),
    )
    write_snapshot(store, path)
    return path


def _exit_message(argv: list[str]) -> str:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    message = exc_info.value.code
    assert isinstance(message, str), f"expected a message, got exit code {message!r}"
    assert "\n" not in message, f"error must be a single line, got: {message!r}"
    assert "Traceback" not in message
    return message


def test_check_missing_baseline_names_flag_and_file(tmp_path: Path):
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(
        ["check", "--baseline", str(tmp_path / "baseline.json"), "--current", str(current)]
    )

    assert message.startswith("--baseline: file not found")
    assert "baseline.json" in message


def test_check_missing_current_names_flag_and_file(tmp_path: Path):
    baseline = _valid_snapshot(tmp_path / "baseline.json")

    message = _exit_message(
        ["check", "--baseline", str(baseline), "--current", str(tmp_path / "current.json")]
    )

    assert message.startswith("--current: file not found")
    assert "current.json" in message


def test_check_truncated_json_reports_position(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"schema_version": 2, "records": [', encoding="utf-8")
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(["check", "--baseline", str(baseline), "--current", str(current)])

    assert message.startswith("--baseline: ")
    assert "is not valid JSON" in message
    assert "line 1 column" in message


def test_check_empty_file_is_not_valid_json(tmp_path: Path):
    baseline = _valid_snapshot(tmp_path / "baseline.json")
    current = tmp_path / "current.json"
    current.write_text("", encoding="utf-8")

    message = _exit_message(["check", "--baseline", str(baseline), "--current", str(current)])

    assert message.startswith("--current: ")
    assert "is not valid JSON" in message


def test_check_top_level_json_array_is_rejected_as_not_a_snapshot(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("[1, 2, 3]", encoding="utf-8")
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(["check", "--baseline", str(baseline), "--current", str(current)])

    assert message.startswith("--baseline: ")
    assert "not an adk-tracegauge snapshot" in message
    assert "list" in message


def test_check_unsupported_schema_version_is_one_line(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema_version": 99, "records": []}), encoding="utf-8")
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(["check", "--baseline", str(baseline), "--current", str(current)])

    assert message.startswith("--baseline: ")
    assert "unsupported snapshot schema_version 99" in message


def test_check_malformed_record_fields_name_the_file(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"schema_version": 2, "records": [{"not_a_real_field": 1}]}), encoding="utf-8"
    )
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(["check", "--baseline", str(baseline), "--current", str(current)])

    assert message.startswith("--baseline: ")
    assert "malformed snapshot content" in message
    assert "baseline.json" in message


def test_check_directory_passed_as_snapshot_is_one_line(tmp_path: Path):
    current = _valid_snapshot(tmp_path / "current.json")

    message = _exit_message(["check", "--baseline", str(tmp_path), "--current", str(current)])

    assert message.startswith("--baseline: ")


def test_snapshot_missing_eval_set_file_names_flag(tmp_path: Path):
    history = _write_eval_history_for(tmp_path, [("case_1", "sess-a")])

    message = _exit_message(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(tmp_path / "snap.json"),
            "--eval-history",
            str(history),
            "--eval-set-file",
            str(tmp_path / "nope.evalset.json"),
        ]
    )

    assert message.startswith("--eval-set-file: file not found")


def test_snapshot_missing_eval_history_names_flag(tmp_path: Path):
    message = _exit_message(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(tmp_path / "snap.json"),
            "--eval-history",
            str(tmp_path / "nope.evalset_result.json"),
        ]
    )

    assert message.startswith("--eval-history: file not found")


def test_snapshot_unparseable_eval_history_keeps_its_own_actionable_message(tmp_path: Path):
    # _compat already raised an actionable RuntimeError here; the CLI used to print it inside
    # a traceback. It must survive verbatim -- prefixed with the flag, on one line.
    history = tmp_path / "history.evalset_result.json"
    history.write_text("{ not json", encoding="utf-8")

    message = _exit_message(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(tmp_path / "snap.json"),
            "--eval-history",
            str(history),
        ]
    )

    assert message.startswith("--eval-history: adk_tracegauge: could not parse")


def test_snapshot_unwritable_output_path_is_one_line(tmp_path: Path):
    message = _exit_message(
        [
            "snapshot",
            "--entrypoint",
            "test_cli:_fixture_returns_store_with_session_id",
            "--output",
            str(tmp_path / "no_such_dir" / "snap.json"),
        ]
    )

    assert message.startswith("--output: could not write")


def test_library_read_snapshot_still_raises_typed_exceptions(tmp_path: Path):
    # Only the CLI boundary converts to SystemExit -- library callers keep the typed errors.
    with pytest.raises(FileNotFoundError):
        read_snapshot(tmp_path / "missing.json")

    truncated = tmp_path / "truncated.json"
    truncated.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_snapshot(truncated)

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not an adk-tracegauge snapshot"):
        read_snapshot(array)
