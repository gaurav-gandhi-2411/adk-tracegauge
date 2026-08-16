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
one place left to bump per release. This test asserts the two views of the
version (installed distribution metadata vs. the runtime attribute) always
agree -- it must FAIL against the old two-hardcoded-literals scheme whenever
they drift, and PASS once there is a single source of truth.
"""

from __future__ import annotations

import importlib.metadata

import adk_tracegauge


def test_installed_metadata_version_matches_runtime_version_attr():
    assert importlib.metadata.version("adk-tracegauge") == adk_tracegauge.__version__
