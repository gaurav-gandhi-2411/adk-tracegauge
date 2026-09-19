"""`python -m adk_tracegauge` -- the PATH-independent fallback the README recommends when a
`pip install --user` console script isn't on PATH (see ``adk_tracegauge/__main__.py``).

`__main__.py` is three lines, but it is the *only* invocation path that works for a user whose
`adk-tracegauge` command is "not recognized", and its one job -- turning ``main()``'s return
value into the process exit code -- is exactly what a CI gate depends on. Two levels of test:

* in-process via ``runpy`` (fast, and what coverage sees): the exit code ``main()`` returns is
  what ``SystemExit`` carries;
* one real subprocess per behaviour that only a real process can prove (the exit status the
  OS sees, and that nothing is printed at import time that would corrupt CLI output).
"""

from __future__ import annotations

import importlib.util
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from adk_tracegauge._cli import EXIT_INSUFFICIENT_DATA
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import write_snapshot


def _one_record_snapshot(path: Path) -> Path:
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


def _run_module_in_process(argv: list[str]) -> SystemExit:
    old_argv = sys.argv
    sys.argv = ["adk_tracegauge", *argv]
    try:
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("adk_tracegauge", run_name="__main__", alter_sys=True)
    finally:
        sys.argv = old_argv
    return exc_info.value


def test_main_module_propagates_main_return_value_as_exit_code(tmp_path: Path):
    # n=1 per side is below --min-n (30) -> `check` returns EXIT_INSUFFICIENT_DATA (3), not 0.
    # If __main__ ever dropped `sys.exit(...)` around main(), this would exit 0 and a CI
    # gate running `python -m adk_tracegauge check ...` would silently pass.
    baseline = _one_record_snapshot(tmp_path / "baseline.json")
    current = _one_record_snapshot(tmp_path / "current.json")

    exit_info = _run_module_in_process(
        ["check", "--baseline", str(baseline), "--current", str(current)]
    )

    assert exit_info.code == EXIT_INSUFFICIENT_DATA


def test_main_module_help_exits_zero():
    exit_info = _run_module_in_process(["--help"])

    assert exit_info.code == 0


def test_main_module_surfaces_actionable_error_message(tmp_path: Path):
    exit_info = _run_module_in_process(
        ["snapshot", "--entrypoint", "no_colon_here", "--output", str(tmp_path / "snap.json")]
    )

    assert isinstance(exit_info.code, str)
    assert "module.path:callable_name" in exit_info.code


def _subprocess_env() -> dict[str, str]:
    # Resolve the package the *test process* imported, and hand the same location to the child
    # -- so this works identically against a src/ checkout (local/CI dev runs, where pytest's
    # `pythonpath` setting makes it importable only in-process) and an installed wheel (the
    # weekly pypi-canary), without ever shadowing one with the other.
    spec = importlib.util.find_spec("adk_tracegauge")
    assert spec is not None and spec.origin is not None
    package_parent = str(Path(spec.origin).parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH")]))
    return env


def test_python_dash_m_real_process_exit_status_and_stderr(tmp_path: Path):
    result = subprocess.run(  # noqa: S603 -- fixed argv, sys.executable, no shell
        [
            sys.executable,
            "-m",
            "adk_tracegauge",
            "snapshot",
            "--entrypoint",
            "no_colon_here",
            "--output",
            str(tmp_path / "snap.json"),
        ],
        capture_output=True,
        text=True,
        env=_subprocess_env(),
        timeout=180,
        check=False,
    )

    # SystemExit("<message>") -> the interpreter prints the message to stderr and exits 1.
    assert result.returncode == 1
    assert "module.path:callable_name" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
