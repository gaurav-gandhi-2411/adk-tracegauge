from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_readme_pypi.py"
_spec = importlib.util.spec_from_file_location("check_readme_pypi", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

# The unit tests drive the script's own logic with a stand-in renderer so they need no extra
# dependency; the CI job "readme-pypi" runs the real readme_renderer over the real README and over
# a known-bad fixture, which is what proves the two agree.


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    markdown: str,
    html: str | None,
) -> int:
    fake = types.ModuleType("readme_renderer.markdown")
    fake.render = lambda _text: html  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "readme_renderer", types.ModuleType("readme_renderer"))
    monkeypatch.setitem(sys.modules, "readme_renderer.markdown", fake)
    readme = tmp_path / "README.md"
    readme.write_text(markdown, encoding="utf-8")
    return check.main(["check_readme_pypi.py", str(readme)])


def test_slug_matches_github_style() -> None:
    assert check._slug("Quick start") == "quick-start"
    assert check._slug("What `tes` measures?") == "what-tes-measures"


def test_heading_slugs_ignore_code_fences() -> None:
    text = "# Title\n\n```\n# not a heading\n```\n\n## Real one\n"
    assert check._heading_slugs(text) == {"title", "real-one"}


def test_absolute_links_and_valid_anchor_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html = '<a href="https://example.org/x">a</a><a href="#install">b</a><img src="https://e.org/i.svg">'
    assert _run(monkeypatch, tmp_path, "# T\n\n## Install\n", html) == 0


@pytest.mark.parametrize("link", ["LICENSE", "docs/guide.md", "./assets/logo.svg", "../x"])
def test_relative_link_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, link: str) -> None:
    assert _run(monkeypatch, tmp_path, "# T\n", f'<a href="{link}">x</a>') == 1


def test_relative_image_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run(monkeypatch, tmp_path, "# T\n", '<img src="assets/badge.svg">') == 1


def test_dead_in_page_anchor_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run(monkeypatch, tmp_path, "# T\n", '<a href="#nowhere">x</a>') == 1


def test_unrenderable_readme_is_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run(monkeypatch, tmp_path, "# T\n", None) == 2


def test_missing_readme_is_exit_2(tmp_path: Path) -> None:
    assert check.main(["check_readme_pypi.py", str(tmp_path / "absent.md")]) == 2
