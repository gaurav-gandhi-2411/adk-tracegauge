"""`adk-tracegauge --version`, and the PEP 561 `py.typed` marker.

The audit of all 20 published adk-tracegauge versions (2026-10-08) found that none had a
`--version` (the CLI exited 2 with "the following arguments are required: command") and that no
wheel shipped `py.typed`, so type checkers treated every consumer import as untyped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import adk_tracegauge
from adk_tracegauge._cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"adk-tracegauge {adk_tracegauge.__version__}"


def test_version_flag_needs_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    # The subcommand is `required=True`; --version must still win, like --help does.
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "required" not in capsys.readouterr().err


def test_no_arguments_is_still_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_py_typed_marker_is_in_the_package() -> None:
    assert (Path(adk_tracegauge.__file__).parent / "py.typed").is_file()


def test_pyproject_ships_the_marker_and_classifies_the_package_as_typed() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"Typing :: Typed"' in text
    assert re.search(r'"adk_tracegauge" = \[[^\]]*"py\.typed"', text)
