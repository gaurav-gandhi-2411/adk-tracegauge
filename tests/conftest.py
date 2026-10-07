"""Session-wide pytest hooks."""

from __future__ import annotations

from pathlib import Path

import pytest
from _checkout_guard import enforce

_REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session: pytest.Session) -> None:
    # Fail fast, once, before any test runs -- see tests/_checkout_guard.py for why.
    enforce(_REPO_ROOT)
