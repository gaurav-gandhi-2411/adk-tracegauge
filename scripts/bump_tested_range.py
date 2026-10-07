"""Bump the tested google-adk range when the daily canary has passed on a newer release.

``_compat._KNOWN_TESTED_MAX_EXCLUSIVE`` says "the newest google-adk minor this release was tested
against, exclusive". Until now a new ADK release outside it fired the owner's weekly alert and
waited for a human to edit several places. This script makes that edit and
``.github/workflows/tested-range-bump.yml`` opens the PR; the PR still goes through the normal checks
and ``merge_gate.py`` -- nothing here merges anything.

Input is the google-adk version a PASSING canary run installed, read from that run's log (the canary
prints ``google-adk X.Y.Z`` in its "Report the installed google-adk version" step), or given
explicitly. If it is below the current tested max there is nothing to do. Otherwise the new max is
``(major, minor + 1, 0)`` of that version, and these places move together (the drift guard in
``tests/test_adk_tested_range.py`` fails if the first two disagree):

* ``src/adk_tracegauge/_compat.py``: the constant;
* ``.github/tested-adk-legs.json``: the version is appended to the ``bare-adk`` CI legs (ci.yml reads
  that file; a workflow run with the default token cannot edit ``.github/workflows/`` itself);
* ``README.md``: the "Tested range" bound and the lists of releases CI exercises;
* ``docs/troubleshooting.md``: the two places that state the bound.

Every anchor is required: if one is missing the script FAILS rather than half-updating, because a
silent partial bump is exactly the drift the guard exists to catch. It never touches any workflow
file or ``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

Version = tuple[int, int, int]

_V = r"\d+\.\d+\.\d+"
_LIST = rf"{_V}(?:, {_V})*(?: and {_V})?"  # "2.6.0, 2.9.2 and 2.11.0"
_STABLE = re.compile(rf"^({_V})$")
_CONSTANT = re.compile(r"^_KNOWN_TESTED_MAX_EXCLUSIVE = \((\d+), (\d+), (\d+)\)$", re.M)
_README_RANGE = re.compile(rf"(\*\*Tested range: `>=2\.6\.0` and `<)({_V})(`\*\*)")
_README_SMOKE = re.compile(rf"(CI smoke-tests )({_LIST})(; the full suite passes on )({_LIST})(\))")
_README_BARE = re.compile(rf"(runs the whole base surface on bare google-adk )({_LIST})(\.)")
_DOC_RANGE = re.compile(rf"(>=2\.6\.0,<)({_V})(`)")
_DOC_NEWER = re.compile(rf"(newer than `)({_V})(`)")
_LOG_LINE = re.compile(rf"google-adk ({_V})\s*$")
_LEGS_LINE = re.compile(r'("bare_adk_legs":\s*)\[[^\]]*\]')

LEGS_FILE = Path(".github/tested-adk-legs.json")


class AnchorMissing(RuntimeError):
    """An expected line was not found, so a partial bump would have been written."""


def parse_version(text: str) -> Version:
    if not _STABLE.match(text.strip()):
        raise ValueError(f"not a stable X.Y.Z release: {text!r}")
    a, b, c = (int(p) for p in text.strip().split("."))
    return a, b, c


def fmt(v: tuple[int, ...]) -> str:
    return ".".join(map(str, v))


def new_max_exclusive(tested: Version) -> Version:
    return (tested[0], tested[1] + 1, 0)


def canary_version_from_log(log: str) -> str:
    """The google-adk version a canary run installed: the last ``google-adk X.Y.Z`` line printed by
    its report step. ``gh run view --log`` prefixes each line with job, step and timestamp."""
    found = [
        m[1]
        for line in log.splitlines()
        if "Report the installed google-adk version" in line
        and (m := _LOG_LINE.search(line.rstrip()))
    ]
    if not found:
        raise ValueError("no `google-adk X.Y.Z` line from the canary's report step in the log")
    return found[-1]


def _join(versions: list[str]) -> str:
    return versions[0] if len(versions) == 1 else ", ".join(versions[:-1]) + " and " + versions[-1]


def _split(text: str) -> list[str]:
    return re.findall(_V, text)


def _sub_once(
    pattern: re.Pattern[str], repl: str | Callable[[re.Match[str]], str], text: str, what: str
) -> str:
    new, n = pattern.subn(repl, text, count=1)
    if n != 1:
        raise AnchorMissing(f"{what}: anchor not found")
    return new


def current_max(root: Path) -> Version:
    text = (root / "src/adk_tracegauge/_compat.py").read_text(encoding="utf-8")
    m = _CONSTANT.search(text)
    if not m:
        raise AnchorMissing("_compat.py: `_KNOWN_TESTED_MAX_EXCLUSIVE = (a, b, c)` not found")
    return int(m[1]), int(m[2]), int(m[3])


def plan(root: Path, tested: str) -> dict[Path, str] | None:
    """New file texts for a canary pass on ``tested``; None when ``tested`` is inside the range."""
    tv = parse_version(tested)
    if tv < current_max(root):
        return None
    nm = new_max_exclusive(tv)
    new_max = fmt(nm)
    out: dict[Path, str] = {}

    p = root / "src/adk_tracegauge/_compat.py"
    out[p] = _sub_once(
        _CONSTANT,
        f"_KNOWN_TESTED_MAX_EXCLUSIVE = ({nm[0]}, {nm[1]}, {nm[2]})",
        p.read_text(encoding="utf-8"),
        "_compat.py constant",
    )

    p = root / LEGS_FILE
    text = p.read_text(encoding="utf-8")
    legs = list(json.loads(text)["bare_adk_legs"])
    if tested not in legs:
        legs.append(tested)
    # edit only the list's line, so the rest of the file (its _comment) stays byte-identical
    out[p] = _sub_once(
        _LEGS_LINE,
        lambda m: f"{m[1]}[{', '.join(json.dumps(v) for v in legs)}]",
        text,
        "tested-adk-legs.json bare_adk_legs",
    )

    p = root / "README.md"
    t = _sub_once(
        _README_RANGE,
        lambda m: f"{m[1]}{new_max}{m[3]}",
        p.read_text(encoding="utf-8"),
        "README tested range",
    )

    def smoke(m: re.Match[str]) -> str:
        smoke_legs, full = _split(m[2]), _split(m[4])
        if tested not in smoke_legs:
            smoke_legs.append(tested)
        if tested not in full:
            full.append(tested)
        return f"{m[1]}{_join(smoke_legs)}{m[3]}{_join(full)}{m[5]}"

    def bare(m: re.Match[str]) -> str:
        bare_legs = _split(m[2])
        if tested not in bare_legs:
            bare_legs.append(tested)
        return f"{m[1]}{_join(bare_legs)}{m[3]}"

    t = _sub_once(_README_SMOKE, smoke, t, "README CI smoke-tests list")
    out[p] = _sub_once(_README_BARE, bare, t, "README bare-adk list")

    p = root / "docs/troubleshooting.md"
    t = _sub_once(
        _DOC_RANGE,
        lambda m: f"{m[1]}{new_max}{m[3]}",
        p.read_text(encoding="utf-8"),
        "troubleshooting range",
    )
    out[p] = _sub_once(
        _DOC_NEWER, lambda m: f"{m[1]}{new_max}{m[3]}", t, "troubleshooting 'newer than'"
    )
    return out


def pr_body(tested: str, old_max: str, new_max: str, canary_url: str) -> str:
    return (
        "## What & why\n"
        f"The daily canary passed on google-adk {tested}, which is at or above the tested max "
        f"({old_max}). This moves `_KNOWN_TESTED_MAX_EXCLUSIVE` to {new_max} and adds a "
        f"`bare-adk ({tested})` CI leg, so the range the README states is the range CI exercises. "
        "Opened by `tested-range-bump.yml`; nothing here merges itself.\n\n"
        f"## Evidence\nCanary run: {canary_url}\n\n"
        "## Changes\n`_compat.py` constant; `.github/tested-adk-legs.json` (the bare-adk legs); "
        "README tested-range line and lists; `docs/troubleshooting.md` bound. "
        "`tests/test_adk_tested_range.py` fails if these disagree.\n\n"
        "## Testing\nThe PR's CI is dispatched by the workflow (a PR opened with the workflow token "
        f"does not trigger `pull_request` runs), including the new `bare-adk ({tested})` leg. "
        "UNVERIFIED until that run is green.\n\n"
        "## Screenshots\nn/a (no UI path).\n\n"
        "## Risk & rollback\nThe tested range is a claim, not a pin (no upper bound on the "
        "dependency): a wrong bump only silences one log line. Revert = close the PR. After merging, "
        f"consider adding `bare-adk ({tested})` to the required checks.\n\n"
        "## Reviewable / generated split\nSmall hand-reviewable edits, 0 generated lines.\n"
    )


def _emit(outputs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in outputs.items())
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(lines)
    print(lines, end="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Bump the tested google-adk range after a canary pass."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--version", help="google-adk X.Y.Z that passed (explicit, e.g. a dry run)")
    src.add_argument("--canary-log", type=Path, help="`gh run view --log` output of the canary run")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--write", action="store_true", help="write the files (default: report only)")
    ap.add_argument("--canary-url", default="(explicit version, no canary run)")
    ap.add_argument("--body-file", type=Path, help="write the PR body here when a bump is needed")
    args = ap.parse_args(argv)

    tested = args.version or canary_version_from_log(args.canary_log.read_text(encoding="utf-8"))
    old = fmt(current_max(args.root))
    edits = plan(args.root, tested)
    if edits is None:
        print(f"google-adk {tested} is inside the tested range (< {old}); nothing to do")
        _emit({"changed": "false", "adk_version": tested})
        return 0
    new = fmt(new_max_exclusive(parse_version(tested)))
    changed = [p for p, text in edits.items() if p.read_text(encoding="utf-8") != text]
    print(f"google-adk {tested} >= {old}: tested max {old} -> {new}; {len(changed)} file(s) change")
    for p in changed:
        print(f"  {p.relative_to(args.root).as_posix()}")
    if args.write:
        for p in changed:
            p.write_text(edits[p], encoding="utf-8", newline="")
    if args.body_file and changed:
        args.body_file.write_text(pr_body(tested, old, new, args.canary_url), encoding="utf-8")
    _emit(
        {
            "changed": "true" if changed else "false",
            "adk_version": tested,
            "old_max": old,
            "new_max": new,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
