"""The google-adk tested range: no hard cap, one soft notice, and a CI-matrix drift guard.

0.9.3 removed the upper bound on google-adk (the cap's only observed effect was pip silently
DOWNGRADING users already on a newer release). What replaces it is `_compat.warn_once_if_adk_untested`
(one logged line, never an error) plus `_KNOWN_TESTED_MAX_EXCLUSIVE`, which has to stay honest. It is
tied to the `bare-adk` CI matrix and not to pyproject.toml (which has no cap to compare to) or to the
CHANGELOG (prose, which can claim "tested" without any run): the matrix is the thing that actually
installs and exercises each release on every PR, and `bare-adk (<newest>)` is a required check.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from adk_tracegauge import _compat

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


@pytest.fixture(autouse=True)
def _fresh_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_compat, "_untested_adk_warned", False)


def _set_adk_version(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(_compat._google_adk, "__version__", version, raising=False)


# --- drift guard: the constant must match what CI actually exercises --------------------------------


def _bare_adk_matrix_versions() -> list[tuple[int, ...]]:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = text.split("\n  bare-adk:\n", 1)[1]
    m = re.search(r"google-adk:\s*\[([^\]]+)\]", job)
    assert m, "bare-adk matrix has no `google-adk: [...]` list"
    return [tuple(int(p) for p in v.strip().strip("\"'").split(".")) for v in m.group(1).split(",")]


def test_tested_max_is_the_next_minor_after_the_newest_bare_adk_matrix_leg() -> None:
    newest = max(_bare_adk_matrix_versions())
    assert (newest[0], newest[1] + 1, 0) == _compat._KNOWN_TESTED_MAX_EXCLUSIVE, (
        "_KNOWN_TESTED_MAX_EXCLUSIVE must be the next minor after the newest bare-adk CI leg: add a CI "
        "leg for the new google-adk release first (and make it a required check), then bump the constant"
    )


def test_tested_min_is_the_oldest_bare_adk_matrix_leg() -> None:
    assert min(_bare_adk_matrix_versions()) == _compat._KNOWN_TESTED_MIN


def test_pyproject_has_no_upper_bound_on_google_adk() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # requirement strings only (they carry a specifier); the bare `"google-adk"` in `keywords` is not one
    reqs = re.findall(r'"(google-adk(?:\[eval\])?[<>=!~][^"]*)"', text)
    assert len(reqs) == 2, reqs  # base dependency and the [eval] extra
    for req in reqs:
        assert "<" not in req, f"{req!r}: an upper bound is back; see the 0.9.3 CHANGELOG entry"
        assert ">=2.6.0" in req


# --- the soft notice --------------------------------------------------------------------------------


def test_in_range_version_is_silent(monkeypatch, caplog) -> None:
    _set_adk_version(monkeypatch, "2.11.0")
    with caplog.at_level("WARNING", logger="adk_tracegauge"):
        assert _compat.warn_once_if_adk_untested() is False
    assert caplog.records == []


def test_newer_than_tested_logs_exactly_one_actionable_line_and_only_once(
    monkeypatch, caplog
) -> None:
    _set_adk_version(monkeypatch, "9.9.9")
    with caplog.at_level("WARNING", logger="adk_tracegauge"):
        assert _compat.warn_once_if_adk_untested() is True
        for _ in range(1000):  # a per-call hot path would hit this thousands of times
            assert _compat.warn_once_if_adk_untested() is False
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "google-adk==9.9.9" in msg and "newer than" in msg
    assert "issues" in msg  # actionable: says where to report


def test_older_than_floor_logs_one_line(monkeypatch, caplog) -> None:
    _set_adk_version(monkeypatch, "2.5.9")
    with caplog.at_level("WARNING", logger="adk_tracegauge"):
        assert _compat.warn_once_if_adk_untested() is True
    assert "older than the minimum" in caplog.records[0].getMessage()


@pytest.mark.parametrize("version", ["unknown", "not-a-version", ""])
def test_unreadable_version_is_silent_not_a_guess(monkeypatch, caplog, version) -> None:
    _set_adk_version(monkeypatch, version)
    with caplog.at_level("WARNING", logger="adk_tracegauge"):
        assert _compat.warn_once_if_adk_untested() is False
    assert caplog.records == []


def test_a_failing_logger_cannot_fail_the_caller(monkeypatch) -> None:
    _set_adk_version(monkeypatch, "9.9.9")

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("logging blew up")

    monkeypatch.setattr(_compat.logging.getLogger("adk_tracegauge"), "warning", boom)
    assert _compat.warn_once_if_adk_untested() is False  # swallowed, not raised


def test_warnings_as_errors_cannot_turn_the_notice_into_a_failure() -> None:
    """Real subprocess, an 'untested' google-adk, a real plugin construction with every warning promoted
    to an error: exit 0, no traceback, and exactly one line of ours on stderr (the bare logging message). The filter
    is applied AFTER the imports because google-adk's own import emits a DeprecationWarning
    (BaseAgentConfig) that a blanket `python -W error` would raise before any of our code runs."""
    code = (
        "import google.adk; google.adk.__version__ = '9.9.9'\n"
        "from adk_tracegauge import TraceGaugeUsagePlugin\n"
        "import warnings; warnings.simplefilter('error')\n"
        "TraceGaugeUsagePlugin(); TraceGaugeUsagePlugin()\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    # Count OUR line only: with the [eval] extra other libraries (vertexai, ADK's experimental registry)
    # print their own import-time warnings to stderr.
    ours = [
        ln for ln in out.stderr.splitlines() if ln.startswith("adk_tracegauge: google-adk==9.9.9")
    ]
    assert out.returncode == 0, out.stderr
    assert "Traceback" not in out.stderr, out.stderr
    assert len(ours) == 1 and "newer than" in ours[0], ours


# --- call sites: first use only, never a hot path -----------------------------------------------------


def test_plugin_and_evaluator_construction_trigger_the_notice(monkeypatch, caplog) -> None:
    from google.adk.evaluation.eval_metrics import EvalMetric

    from adk_tracegauge import TraceGaugeUsagePlugin
    from adk_tracegauge.evaluator import CostEfficiencyEvaluator, CostThresholdCriterion

    _set_adk_version(monkeypatch, "9.9.9")
    with caplog.at_level("WARNING", logger="adk_tracegauge"):
        TraceGaugeUsagePlugin()
        CostEfficiencyEvaluator(
            eval_metric=EvalMetric(
                metric_name="cost_efficiency",
                criterion=CostThresholdCriterion(threshold=1.0),
            )
        )
    assert len(caplog.records) == 1  # once per process across both entry points


def test_the_notice_is_only_ever_called_from_constructors_and_main() -> None:
    """Static guarantee that it is not in a per-LLM-call callback or reachable at import time."""
    callers: dict[str, str] = {}
    for path in sorted(SRC.glob("adk_tracegauge/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                for node in ast.walk(fn):
                    if (
                        isinstance(node, ast.Call)
                        and getattr(node.func, "attr", None) == "warn_once_if_adk_untested"
                    ):
                        callers[f"{path.name}:{fn.name}"] = fn.name
    assert set(callers) == {
        "_plugin.py:__init__",
        "evaluator.py:__init__",
        "_cli.py:main",
    }, callers
    # and no module-level call (import-time side effect): look for Call nodes outside any def/class
    for path in sorted(SRC.glob("adk_tracegauge/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for node in ast.walk(stmt):
                assert not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "warn_once_if_adk_untested"
                ), f"{path.name}: module-level call (import-time side effect)"
