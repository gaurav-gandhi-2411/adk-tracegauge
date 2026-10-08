"""Fail if the package README would render with a dead link on pypi.org.

PyPI shows the README on a page whose base URL is pypi.org/project/<name>/, so a relative link
(``LICENSE``, ``docs/x.md``, ``assets/logo.svg``) resolves to a pypi.org URL that does not exist.
This renders the README the way PyPI does (readme_renderer, the library behind PyPI's renderer)
and fails on:

* a README that does not render at all (``twine check`` only warns on some of these),
* any relative ``href`` / ``src`` (anything that is not an absolute http(s)/mailto URL or an
  in-page ``#anchor``),
* an in-page ``#anchor`` whose heading does not exist in the README (checked against GitHub-style
  heading slugs, since the same README is also read on GitHub).

Run: ``python scripts/check_readme_pypi.py [README.md]``. Exit 0 = clean, 1 = findings, 2 = could
not check (missing file / renderer missing / render failed). Needs ``readme-renderer[md]``.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ABSOLUTE = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in ("href", "src") and value is not None:
                self.links.append((tag, value))


def _slug(heading: str) -> str:
    """GitHub's heading slug: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"[`*_~]|<[^>]+>|!?\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", heading)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _heading_slugs(markdown: str) -> set[str]:
    slugs: set[str] = set()
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match and not in_fence:
            slugs.add(_slug(match.group(1)))
    return slugs


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "README.md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    try:
        from readme_renderer.markdown import render
    except ImportError as exc:
        print(
            f"readme_renderer is not installed (pip install 'readme-renderer[md]'): {exc}",
            file=sys.stderr,
        )
        return 2
    html = render(text)
    if html is None:
        print(
            f"{path}: readme_renderer could not render this README; PyPI would show it as text",
            file=sys.stderr,
        )
        return 2

    parser = _Links()
    parser.feed(html)
    slugs = _heading_slugs(text)
    findings: list[str] = []
    for tag, link in parser.links:
        if ABSOLUTE.match(link):
            continue
        if link.startswith("#"):
            if link[1:].lower() not in slugs:
                findings.append(f"dead in-page anchor <{tag}> {link}")
            continue
        findings.append(f"relative link <{tag}> {link} (dead on pypi.org; use an absolute URL)")
    for finding in dict.fromkeys(findings):
        print(f"{path}: {finding}")
    if findings:
        print(f"{len(set(findings))} problem(s)", file=sys.stderr)
        return 1
    print(f"{path}: renders, {len(parser.links)} link(s), all absolute or valid anchors")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
