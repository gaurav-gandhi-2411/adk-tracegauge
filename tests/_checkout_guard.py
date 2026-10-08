"""Refuse to run the suite against an adk_tracegauge that is not this checkout's.

pytest's ``pythonpath = [".", "src", "scripts"]`` (pyproject.toml) makes every IN-PROCESS import
resolve to this checkout. A subprocess does not get it: ``tests/test_bare_adk_import.py`` and the
quickstart/CLI tests start ``sys.executable`` and import whatever is INSTALLED in that environment.
When the venv's editable install still points at an older checkout, those tests fail with a
confusing assertion about the code under test, while every in-process test passes. Seen twice
(2026-10-07): five bare-import "failures" that were an environment fault, not a defect.

``enforce`` is called once, at session start, by ``tests/conftest.py``.

It compares two views and requires them to agree: the package this pytest process imports
(``pythonpath``-dependent) and the package a fresh subprocess imports (the environment's own
installation). ``release.yml``'s preflight-latest-adk runs the suite against an installed
wheel with ``src`` dropped from ``pythonpath``; both views are then the wheel and the guard
must let it run (it did not, in the 0.10.1 dispatch of 2026-10-08).
"""

from __future__ import annotations

import importlib.util
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


def in_process_package_file() -> Path | None:
    """The ``adk_tracegauge/__init__.py`` THIS pytest process would import, or None.

    pytest applies ``pythonpath`` before conftest.py loads, so this reflects it: with the default
    ``pythonpath = [".", "src", "scripts"]`` it is the checkout's ``src``; with the override
    ``release.yml``'s preflight-latest-adk uses (``--override-ini="pythonpath=. scripts"``, which
    drops ``src`` on purpose) it is the installed wheel.
    """
    spec = importlib.util.find_spec("adk_tracegauge")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin)


def enforce(
    repo_root: Path,
    probe: Callable[[], Path | None] = installed_package_file,
    in_process: Callable[[], Path | None] = in_process_package_file,
) -> None:
    """Exit the pytest session, with an actionable message, if in-process and subprocess tests
    would exercise two different adk_tracegauge packages.

    The invariant is that both views agree, not that the package is inside ``src``: the release
    preflight deliberately tests an installed wheel (src dropped from ``pythonpath``), and there
    the two views are the same wheel. The failure this exists for is the other disagreement: the
    in-process import resolves to this checkout while a subprocess resolves to a stale editable
    install of an older one.
    """
    src = (repo_root / "src").resolve()
    try:
        found = probe()
        mine = in_process()
    except (subprocess.TimeoutExpired, OSError) as exc:
        # fail closed with a message, not an INTERNALERROR traceback: "could not check" is not "fine"
        pytest.exit(
            f"could not determine which adk_tracegauge the test interpreter ({sys.executable}) "
            f"would import: {type(exc).__name__}: {exc}.\n"
            f"Check that interpreter works, then from {repo_root} run:  uv pip install -e .",
            returncode=4,
        )
    if found is not None and mine is not None and found.resolve() == mine.resolve():
        return
    where = f"resolves to {found.resolve()}" if found is not None else "is not importable at all"
    pytest.exit(
        "adk_tracegauge in the test interpreter's environment "
        f"({sys.executable}) {where}, but this pytest process imports "
        f"{mine.resolve() if mine is not None else 'nothing'} (this checkout's source is {src}).\n"
        "Subprocess-based tests (tests/test_bare_adk_import.py and others) import the INSTALLED "
        "package, so they would test the wrong code and fail misleadingly.\n"
        f"Fix: from {repo_root}, run:  uv pip install -e .   (or: python -m pip install -e .)",
        returncode=4,
    )
