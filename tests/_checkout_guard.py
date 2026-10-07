"""Refuse to run the suite against an adk_tracegauge that is not this checkout's.

pytest's ``pythonpath = [".", "src", "scripts"]`` (pyproject.toml) makes every IN-PROCESS import
resolve to this checkout. A subprocess does not get it: ``tests/test_bare_adk_import.py`` and the
quickstart/CLI tests start ``sys.executable`` and import whatever is INSTALLED in that environment.
When the venv's editable install still points at an older checkout, those tests fail with a
confusing assertion about the code under test, while every in-process test passes. Seen twice
(2026-10-07): five bare-import "failures" that were an environment fault, not a defect.

``enforce`` is called once, at session start, by ``tests/conftest.py``.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# find_spec LOCATES the package without executing it. Importing it (the first version of this
# probe) runs google-adk's whole import chain: 17-26 s warm and over the old 120 s timeout on a
# cold venv, where the guard then crashed the session with a raw TimeoutExpired.
_PROBE = (
    "import importlib.util as u; s = u.find_spec('adk_tracegauge'); "
    "print(s.origin if s is not None and s.origin else '')"
)


def installed_package_file(python: str = sys.executable) -> Path | None:
    """The ``adk_tracegauge/__init__.py`` a fresh subprocess of ``python`` would import, or None.

    ``-I`` ignores the working directory and PYTHON* variables, so only the environment's own
    installation can answer. None means the package is not installed there at all.
    """
    proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input
        [python, "-I", "-W", "ignore", "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip().splitlines()[-1])


def enforce(
    repo_root: Path,
    probe: Callable[[], Path | None] = installed_package_file,
) -> None:
    """Exit the pytest session, with an actionable message, if the install is not this checkout's."""
    src = (repo_root / "src").resolve()
    try:
        found = probe()
    except (subprocess.TimeoutExpired, OSError) as exc:
        # fail closed with a message, not an INTERNALERROR traceback: "could not check" is not "fine"
        pytest.exit(
            f"could not determine which adk_tracegauge the test interpreter ({sys.executable}) "
            f"would import: {type(exc).__name__}: {exc}.\n"
            f"Check that interpreter works, then from {repo_root} run:  uv pip install -e .",
            returncode=4,
        )
    if found is not None and found.resolve().is_relative_to(src):
        return
    where = f"resolves to {found.resolve()}" if found is not None else "is not importable at all"
    pytest.exit(
        "adk_tracegauge in the test interpreter's environment "
        f"({sys.executable}) {where}, not inside this checkout's {src}.\n"
        "Subprocess-based tests (tests/test_bare_adk_import.py and others) import the INSTALLED "
        "package, so they would test the wrong code and fail misleadingly.\n"
        f"Fix: from {repo_root}, run:  uv pip install -e .   (or: python -m pip install -e .)",
        returncode=4,
    )
