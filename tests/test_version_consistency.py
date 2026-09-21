"""Guard test for the version-single-source bug (found pre-0.3.0 release).

`pyproject.toml`'s installed package metadata and `adk_tracegauge.__version__`
used to be two independently hand-maintained string literals with no
mechanism keeping them in sync. PR #6's squash-merge into `main` bumped only
one of the two (pyproject.toml's `version = "0.3.0"`), leaving
`__init__.py`'s `__version__ = "0.2.0"` stale -- caught by a release gate
before 0.3.0 was ever tagged/published.

The fix (see pyproject.toml's `[tool.setuptools.dynamic]` section) makes
`__init__.py`'s `__version__` the single source of truth: pyproject.toml
declares `dynamic = ["version"]` and reads it via
`{attr = "adk_tracegauge.__version__"}` at build time, so there is exactly
one place left to bump per release. Two tests guard that: one asserts the
build configuration still has a single source (no static `version = ...` can
creep back in), one asserts the installed metadata agrees with the runtime
attribute.

The second is skipped -- not weakened -- when the metadata is a STALE DEVELOPMENT
artefact: an editable install's metadata is frozen at `pip install -e` time, and
the build also leaves `src/adk_tracegauge.egg-info`, which pytest's `pythonpath =
["src"]` puts ahead of the real dist-info. After bumping `__version__` in the source
tree both disagree until the venv is re-synced -- a dev-venv artefact, not the bug
above, and it failed three times in a row after version bumps. Metadata from a real
(non-editable) install -- CI's wheel install, a user's `pip install` -- is always checked.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
from pathlib import Path

import pytest

import adk_tracegauge

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _metadata_is_a_development_artefact() -> bool:
    dist = importlib.metadata.distribution("adk-tracegauge")
    if str(getattr(dist, "_path", "")).endswith(".egg-info"):  # source-tree build leftover
        return True
    raw = dist.read_text("direct_url.json")
    return bool(raw) and bool(json.loads(raw).get("dir_info", {}).get("editable"))


def test_pyproject_declares_a_single_dynamic_version_source():
    text = _PYPROJECT.read_text(encoding="utf-8")
    project = text.split("\n[project]\n", 1)[1].split("\n[", 1)[0]
    assert 'dynamic = ["version"]' in project
    assert not re.search(r"^version\s*=", project, re.MULTILINE), (
        "static version in [project]: __version__ would no longer be the single source"
    )
    assert 'version = { attr = "adk_tracegauge.__version__" }' in text


def test_installed_metadata_version_matches_runtime_version_attr():
    installed = importlib.metadata.version("adk-tracegauge")
    if installed != adk_tracegauge.__version__ and _metadata_is_a_development_artefact():
        pytest.skip(
            f"stale development metadata (metadata {installed}, source {adk_tracegauge.__version__}); "
            "refresh with `uv sync --reinstall-package adk-tracegauge`"
        )
    assert installed == adk_tracegauge.__version__
