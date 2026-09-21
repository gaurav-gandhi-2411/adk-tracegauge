"""`adk-tracegauge report` -- the per-invocation cost table (google/adk-python Discussion #97:
"how do I turn tokens into dollars").

Every dollar figure asserted here is hand-computed from the published gemini-2.5-flash rates
($0.30 per million input tokens, $2.50 per million output tokens) in a comment next to it -- not
copied from the output under test, which is how a doubled quickstart figure once got pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_cli import _write_snapshot_with_session_ids

from adk_tracegauge._cli import EXIT_INSUFFICIENT_DATA, EXIT_PASS, build_parser, main
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import write_snapshot


def _call(
    model: str = "gemini-2.5-flash", prompt: int = 1000, output: int = 200, cached: int = 0
) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=cached,
        total_token_count=prompt + output,
    )


def _snapshot(path: Path, calls: list[CapturedCall]) -> Path:
    store = UsageStore()
    for i, call in enumerate(calls):
        store.record(f"e-inv-{i}", call)
    write_snapshot(store, path)
    return path


# 1,000 in x $0.30/M = $0.000300; 200 out x $2.50/M = $0.000500 -> $0.000800
# 12,000 in x $0.30/M = $0.003600; 800 out x $2.50/M = $0.002000 -> $0.005600
# total = $0.006400; tokens 13,000 in / 1,000 out.
_TWO_CALLS = [_call(prompt=1000, output=200), _call(prompt=12000, output=800)]


def _fixture_returns_nothing() -> None:
    """An entrypoint that ran no agent at all (the plugin never fired)."""


def test_report_normal_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    snap = _snapshot(tmp_path / "snap.json", _TWO_CALLS)

    exit_code = main(["report", str(snap)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_PASS
    assert "2 invocation(s) (2 priced, 0 unknown)" in out
    assert "$0.000800" in out
    assert "$0.005600" in out
    assert "gemini-2.5-flash" in out
    assert "12,000" in out and "800" in out
    assert "total: $0.006400 across 2 invocation(s)" in out
    assert "tokens (priced invocations): 13,000 in / 1,000 out" in out
    assert "EXCLUDES" not in out
    assert "UNKNOWN" not in out


def test_report_shows_unpriceable_invocation_as_unknown_never_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = _snapshot(
        tmp_path / "snap.json",
        [_call(prompt=1000, output=200), _call(model="totally-unknown-model-xyz")],
    )

    exit_code = main(["report", str(snap)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_PASS
    assert "2 invocation(s) (1 priced, 1 unknown)" in out
    # The unknown invocation is a row of its own, not dropped from the table...
    table_rows = [
        line
        for line in out.splitlines()
        if line.startswith("  e-inv-") and "price table" not in line  # not the reason note
    ]
    assert len(table_rows) == 2
    assert any("UNKNOWN" in row and row.rstrip().endswith("unknown") for row in table_rows)
    # ...its reason names the model that could not be priced (no guessed rate)...
    assert "model 'totally-unknown-model-xyz' is not in the price table" in out
    assert "NOT priced (no guessed rate is ever used)" in out
    # ...and the total is labelled as excluding it, at exactly the one priced call's $0.000800.
    assert "priced total: $0.000800 across 1 invocation(s) -- EXCLUDES 1 unknown" in out
    assert "the true total is at least this" in out


def test_report_json_marks_total_incomplete_when_any_invocation_is_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = _snapshot(
        tmp_path / "snap.json",
        [_call(prompt=1000, output=200), _call(model="totally-unknown-model-xyz")],
    )

    exit_code = main(["report", str(snap), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == EXIT_PASS
    assert data["n_invocations"] == 2 and data["n_priced"] == 1 and data["n_unknown"] == 1
    assert data["total_usd_priced"] == pytest.approx(0.0008)
    assert data["total_is_complete"] is False
    by_status = {row["status"]: row for row in data["invocations"]}
    assert by_status["priced"]["cost_usd"] == pytest.approx(0.0008)
    assert by_status["unknown"]["cost_usd"] is None
    assert "totally-unknown-model-xyz" in by_status["unknown"]["reason"]


def test_report_json_matches_text_totals(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    snap = _snapshot(tmp_path / "snap.json", _TWO_CALLS)

    main(["report", str(snap), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert data["total_usd_priced"] == pytest.approx(0.0064)
    assert data["total_is_complete"] is True
    assert data["tokens_input_priced"] == 13000 and data["tokens_output_priced"] == 1000
    assert [row["status"] for row in data["invocations"]] == ["priced", "priced"]


def test_report_empty_snapshot_says_nothing_was_captured_and_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = _snapshot(tmp_path / "snap.json", [])

    exit_code = main(["report", str(snap)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_INSUFFICIENT_DATA
    assert "0 invocations" in out
    assert "nothing was captured" in out
    assert "TraceGaugeUsagePlugin is not wired" in out


def test_report_empty_snapshot_json_is_still_valid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = _snapshot(tmp_path / "snap.json", [])

    exit_code = main(["report", str(snap), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == EXIT_INSUFFICIENT_DATA
    assert data["n_invocations"] == 0 and data["invocations"] == []
    assert data["total_usd_priced"] == 0


def test_report_shows_cache_read_column_only_when_something_was_cached(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    plain = _snapshot(tmp_path / "plain.json", [_call()])
    cached = _snapshot(tmp_path / "cached.json", [_call(prompt=1000, output=200, cached=400)])

    main(["report", str(plain)])
    plain_out = capsys.readouterr().out
    main(["report", str(cached)])
    cached_out = capsys.readouterr().out

    assert "cache read" not in plain_out
    assert "cache read" in cached_out


def _exit_message(argv: list[str]) -> str:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    message = exc_info.value.code
    assert isinstance(message, str)
    assert "\n" not in message, f"error must be a single line, got: {message!r}"
    assert "Traceback" not in message
    return message


def test_report_missing_file_is_one_actionable_line(tmp_path: Path):
    message = _exit_message(["report", str(tmp_path / "nope.json")])

    assert message.startswith("snapshot file: file not found")
    assert "adk-tracegauge snapshot --entrypoint" in message
    assert "report --entrypoint" in message


def test_report_truncated_file_is_one_actionable_line(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 2, "records": [', encoding="utf-8")

    message = _exit_message(["report", str(bad)])

    assert message.startswith("snapshot file: ")
    assert "is not valid JSON" in message


def test_report_wrong_shape_file_is_one_actionable_line(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")

    message = _exit_message(["report", str(bad)])

    assert message.startswith("snapshot file: ")
    assert "not an adk-tracegauge snapshot" in message


def test_report_unsupported_schema_version_is_one_actionable_line(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 99, "records": []}), encoding="utf-8")

    message = _exit_message(["report", str(bad)])

    assert message.startswith("snapshot file: ")
    assert "unsupported snapshot schema_version 99" in message


def test_report_requires_exactly_one_of_snapshot_or_entrypoint(tmp_path: Path):
    snap = _snapshot(tmp_path / "snap.json", _TWO_CALLS)

    neither = _exit_message(["report"])
    both = _exit_message(["report", str(snap), "--entrypoint", "test_cli:whatever"])

    assert "exactly one of" in neither and "exactly one of" in both


def test_report_entrypoint_goes_from_agent_to_cost_table_with_no_snapshot_file(
    capsys: pytest.CaptureFixture[str],
):
    # test_cli's fixture records one gemini-2.5-flash call of 1,000 in / 200 out = $0.000800.
    exit_code = main(["report", "--entrypoint", "test_cli:_fixture_returns_explicit_store"])

    out = capsys.readouterr().out
    assert exit_code == EXIT_PASS
    assert "live run of test_cli:_fixture_returns_explicit_store" in out
    assert "1 invocation(s) (1 priced, 0 unknown)" in out
    assert "total: $0.000800 across 1 invocation(s)" in out


def test_report_entrypoint_that_captures_nothing_exits_3(capsys: pytest.CaptureFixture[str]):
    from adk_tracegauge._store import DEFAULT_USAGE_STORE

    DEFAULT_USAGE_STORE.clear()

    exit_code = main(["report", "--entrypoint", "test_report:_fixture_returns_nothing"])

    assert exit_code == EXIT_INSUFFICIENT_DATA
    assert "nothing was captured" in capsys.readouterr().out


def test_report_reads_a_snapshot_written_by_the_snapshot_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = tmp_path / "snap.json"
    _write_snapshot_with_session_ids(snap, {"case-0": 0.01, "case-1": 0.02})

    exit_code = main(["report", str(snap)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_PASS
    assert "2 invocation(s) (2 priced, 0 unknown)" in out


def test_report_subcommand_is_registered_in_the_parser():
    args = build_parser().parse_args(["report", "snap.json", "--json"])

    assert args.command == "report" and args.json_output is True


# --- prices the vendor check cannot verify ------------------------------------------------------


def test_report_flags_a_retired_model_as_not_verifiable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # gemini-2.0-flash: $0.10/M in, $0.40/M out (retired 2026-06-01, no longer on Google's page).
    snap = _snapshot(tmp_path / "snap.json", [_call(model="gemini-2.0-flash")])

    assert main(["report", str(snap)]) == EXIT_PASS

    out = capsys.readouterr().out
    assert "NOT verifiable against a live vendor page" in out
    assert "gemini-2.0-flash: retired 2026-06-01" in out


def test_report_flags_an_asserted_local_model_as_not_vendor_priced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ADK_TRACEGAUGE_ASSUME_LOCAL", "1")
    snap = _snapshot(tmp_path / "snap.json", [_call(model="ollama_chat/llama3")])

    assert main(["report", str(snap)]) == EXIT_PASS

    out = capsys.readouterr().out
    assert "__local_zero_cost__: priced $0.00 because ADK_TRACEGAUGE_ASSUME_LOCAL" in out


def test_report_says_nothing_extra_for_a_vendor_verified_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    snap = _snapshot(tmp_path / "snap.json", _TWO_CALLS)

    assert main(["report", str(snap), "--json"]) == EXIT_PASS
    assert json.loads(capsys.readouterr().out)["unverifiable_pricing"] == []

    assert main(["report", str(snap)]) == EXIT_PASS
    assert "NOT verifiable" not in capsys.readouterr().out


def test_report_json_lists_unverifiable_pricing(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    snap = _snapshot(tmp_path / "snap.json", [_call(model="gemini-2.0-flash")])

    assert main(["report", str(snap), "--json"]) == EXIT_PASS

    (entry,) = json.loads(capsys.readouterr().out)["unverifiable_pricing"]
    assert entry["model"] == "gemini-2.0-flash"
    assert entry["reason"].startswith("retired 2026-06-01")


# --- cost by agent -------------------------------------------------------------------------------


def _agent_call(agent: str, prompt: int, output: int) -> CapturedCall:
    return CapturedCall(
        model_version="gemini-2.5-flash",
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=0,
        total_token_count=prompt + output,
        agent_name=agent,
    )


def test_report_prints_cost_by_agent_when_more_than_one_agent_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # root_agent: 1,000 x $0.30/M + 200 x $2.50/M = $0.000800
    # capital_finder: 12,000 x $0.30/M + 800 x $2.50/M = $0.005600; total $0.006400
    # shares: 0.0008 / 0.0064 = 12.5%, 0.0056 / 0.0064 = 87.5%
    store = UsageStore()
    store.record("e-root", _agent_call("root_agent", 1000, 200))
    store.record("e-sub", _agent_call("capital_finder", 12000, 800))
    write_snapshot(store, tmp_path / "snap.json")

    assert main(["report", str(tmp_path / "snap.json")]) == EXIT_PASS

    out = capsys.readouterr().out
    assert "Cost by agent (priced invocations):" in out
    lines = [ln.strip() for ln in out.splitlines()]
    assert "capital_finder  $0.005600   87.5%" in lines
    assert "root_agent      $0.000800   12.5%" in lines
    assert lines.index("capital_finder  $0.005600   87.5%") < lines.index(
        "root_agent      $0.000800   12.5%"
    )  # most expensive first
    assert "total: $0.006400 across 2 invocation(s)" in out


def test_report_omits_the_agent_block_for_a_single_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    store = UsageStore()
    store.record("e-1", _agent_call("root_agent", 1000, 200))
    store.record("e-2", _agent_call("root_agent", 1000, 200))
    write_snapshot(store, tmp_path / "snap.json")

    assert main(["report", str(tmp_path / "snap.json")]) == EXIT_PASS

    assert "Cost by agent" not in capsys.readouterr().out


def test_report_agent_block_shows_the_part_no_agent_name_was_captured_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # Two named agents ($0.000800 each) plus one call with no agent name ($0.000800):
    # the block must add up to the $0.002400 total, not silently drop the third.
    store = UsageStore()
    store.record("e-a", _agent_call("agent_a", 1000, 200))
    store.record("e-b", _agent_call("agent_b", 1000, 200))
    store.record("e-c", _agent_call("", 1000, 200))
    write_snapshot(store, tmp_path / "snap.json")

    assert main(["report", str(tmp_path / "snap.json")]) == EXIT_PASS

    out = capsys.readouterr().out
    assert "total: $0.002400 across 3 invocation(s)" in out
    assert "(no agent name captured)  $0.000800   33.3%" in out
    assert out.count("$0.000800   33.3%") == 3
