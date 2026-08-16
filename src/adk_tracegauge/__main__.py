"""adk_tracegauge/__main__.py -- enables `python -m adk_tracegauge` as a PATH-independent
fallback for the `adk-tracegauge` console script (see [project.scripts] in pyproject.toml).

A user-site install (`pip install --user`, the default outcome of `pip install adk-tracegauge`
run outside any venv) puts console scripts in a per-user Scripts directory
(`%APPDATA%\\Python\\PythonXYZ\\Scripts` on Windows) that is not on PATH by default -- the
`adk-tracegauge` command then raises `CommandNotFoundException` (PowerShell) or "not
recognized" (cmd) even though the package installed successfully. `python -m adk_tracegauge`
always works regardless, since it depends only on `python` itself being on PATH, which every
Python install guarantees.
"""

from __future__ import annotations

import sys

from adk_tracegauge._cli import main

if __name__ == "__main__":
    sys.exit(main())
