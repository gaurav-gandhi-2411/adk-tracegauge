"""The published `report` table from the public Discussion #97 reply, reproduced by `examples/mixed_run.py`.

The reply (google/adk-python Discussion #97, 2026-09-20) showed this output of
`adk-tracegauge report --entrypoint mixed_run:run`; the fixture behind it was lost and rebuilt as
`examples/mixed_run.py`. If pricing, rounding or the report layout ever changes, this test fails, and the
public figures are then no longer reproducible with the shipped example -- which is the point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from adk_tracegauge import _cli

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
# A TABLE row ("  e-inv-1     gemini-..."), not the footnote line "  e-inv-3: model '...' is not in ...".
_TABLE_ROW = re.compile(r"^\s+(e-inv-\d)\s{2,}")


@pytest.fixture
def report_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.chdir(EXAMPLES)  # --entrypoint resolves against the current directory
    monkeypatch.delitem(sys.modules, "mixed_run", raising=False)
    code = _cli.main(["report", "--entrypoint", "mixed_run:run"])
    sys.modules.pop("mixed_run", None)
    out = capsys.readouterr().out
    assert code == 0, out
    return out


def test_published_summary_line(report_output: str) -> None:
    assert (
        "adk-tracegauge report: 3 invocation(s) (2 priced, 1 unknown) -- live run of mixed_run:run"
        in report_output
    )


def test_the_two_priced_invocations_and_their_costs(report_output: str) -> None:
    rows = {m.group(1): ln for ln in report_output.splitlines() if (m := _TABLE_ROW.match(ln))}
    assert set(rows) == {"e-inv-1", "e-inv-2", "e-inv-3"}
    assert "gemini-2.5-flash" in rows["e-inv-1"] and "12,000" in rows["e-inv-1"]
    assert "800" in rows["e-inv-1"] and rows["e-inv-1"].rstrip().endswith("$0.005600")
    assert "gemini-2.5-flash" in rows["e-inv-2"] and "3,500" in rows["e-inv-2"]
    assert "420" in rows["e-inv-2"] and rows["e-inv-2"].rstrip().endswith("$0.002100")


def test_the_unknown_model_is_reported_unknown_not_priced(report_output: str) -> None:
    row = next(ln for ln in report_output.splitlines() if _TABLE_ROW.match(ln) and "e-inv-3" in ln)
    assert "UNKNOWN" in row and row.rstrip().endswith("unknown")
    assert "model 'acme-llm-7b' is not in the price table" in report_output
    assert "no guessed rate is ever used" in report_output


def test_published_totals(report_output: str) -> None:
    assert (
        "priced total: $0.007700 across 2 invocation(s) -- EXCLUDES 1 unknown invocation(s), "
        "so the true total is at least this" in report_output
    )
    assert "tokens (priced invocations): 15,500 in / 1,220 out" in report_output
