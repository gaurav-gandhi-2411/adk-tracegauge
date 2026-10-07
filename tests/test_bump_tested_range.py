"""scripts/bump_tested_range.py: the edit a green canary on a newer google-adk triggers.

The tests run it against copies of the REAL files (constant, legs data file, README, troubleshooting
doc), so a reworded README sentence or a moved constant fails here -- in a PR -- instead of failing
silently on the day the first newer google-adk appears. Nothing is read from the network or a clock.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from scripts.bump_tested_range import (
    LEGS_FILE,
    AnchorMissing,
    canary_version_from_log,
    current_max,
    fmt,
    main,
    new_max_exclusive,
    parse_version,
    plan,
)

ROOT = Path(__file__).resolve().parent.parent
_FILES = [
    "src/adk_tracegauge/_compat.py",
    ".github/tested-adk-legs.json",
    "README.md",
    "docs/troubleshooting.md",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel in _FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
    return tmp_path


def _apply(root: Path, version: str) -> dict[Path, str]:
    edits = plan(root, version)
    assert edits is not None
    for p, text in edits.items():
        p.write_text(text, encoding="utf-8", newline="")
    return edits


def _legs(root: Path) -> list[str]:
    return json.loads((root / LEGS_FILE).read_text(encoding="utf-8"))["bare_adk_legs"]


def _guard_holds(root: Path) -> bool:
    """The same invariant tests/test_adk_tested_range.py enforces on the real tree."""
    newest = max(parse_version(v) for v in _legs(root))
    return new_max_exclusive(newest) == current_max(root)


def _at_max(root: Path) -> tuple[str, str]:
    """(a google-adk version exactly at the current tested max, the max a bump to it must produce).
    Derived from the tree, so these tests keep passing on the PR this automation itself opens."""
    cur = current_max(root)
    return f"{cur[0]}.{cur[1]}.0", fmt(new_max_exclusive(cur))


def test_the_real_tree_satisfies_the_invariant_the_bump_must_preserve(repo: Path):
    assert _guard_holds(repo)


def test_a_version_inside_the_range_changes_nothing(repo: Path):
    before = current_max(repo)

    assert plan(repo, f"{before[0]}.{before[1] - 1}.9") is None
    assert plan(repo, "2.6.0") is None


def test_the_first_release_at_the_max_moves_every_place_together(repo: Path):
    tested, new_max = _at_max(repo)
    legs_before = _legs(repo)

    edits = _apply(repo, tested)

    assert fmt(current_max(repo)) == new_max
    assert _legs(repo) == [*legs_before, tested]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert f"**Tested range: `>=2.6.0` and `<{new_max}`**" in readme
    # the new release is the LAST item of each of the three lists the README gives
    assert re.search(
        rf"CI smoke-tests [\d., and]*? and {re.escape(tested)}; the full suite", readme
    )
    assert re.search(rf"the full suite passes on [\d., and]*? and {re.escape(tested)}\)", readme)
    assert re.search(rf"bare google-adk [\d., and]*? and {re.escape(tested)}\.", readme)
    doc = (repo / "docs/troubleshooting.md").read_text(encoding="utf-8")
    assert f">=2.6.0,<{new_max}`" in doc and f"newer than `{new_max}`" in doc
    assert _guard_holds(repo)
    assert len(edits) == 4


def test_a_canary_that_skipped_a_minor_still_lands_on_the_next_minor_after_it(repo: Path):
    cur = current_max(repo)
    tested = f"{cur[0]}.{cur[1] + 2}.3"

    _apply(repo, tested)

    assert current_max(repo) == (cur[0], cur[1] + 3, 0)
    assert _legs(repo)[-1] == tested
    assert _guard_holds(repo)


def test_a_new_major_bumps_to_its_first_minor(repo: Path):
    _apply(repo, "99.0.0")

    assert current_max(repo) == (99, 1, 0)
    assert _guard_holds(repo)


def test_running_it_twice_for_the_same_release_changes_nothing_the_second_time(repo: Path):
    tested, _ = _at_max(repo)
    first = _apply(repo, tested)
    snapshot = {p: p.read_text(encoding="utf-8") for p in first}

    # the release is now INSIDE the new range (< the new max), so the second run is a no-op
    assert plan(repo, tested) is None
    assert {p: p.read_text(encoding="utf-8") for p in first} == snapshot


def test_only_the_legs_list_changes_in_the_data_file(repo: Path):
    before = json.loads((repo / LEGS_FILE).read_text(encoding="utf-8"))
    tested, _ = _at_max(repo)

    _apply(repo, tested)

    after = json.loads((repo / LEGS_FILE).read_text(encoding="utf-8"))
    assert after["_comment"] == before["_comment"]
    assert after["bare_adk_legs"] == [*before["bare_adk_legs"], tested]
    assert (repo / LEGS_FILE).read_text(encoding="utf-8").count("\n") == (
        (ROOT / LEGS_FILE).read_text(encoding="utf-8").count("\n")
    )  # no reflow of the file


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("README.md", "**Tested range:"),
        ("README.md", "CI smoke-tests"),
        ("README.md", "runs the whole base surface on bare google-adk"),
        ("docs/troubleshooting.md", "newer than `"),
        ("src/adk_tracegauge/_compat.py", "_KNOWN_TESTED_MAX_EXCLUSIVE = ("),
    ],
)
def test_a_missing_anchor_fails_instead_of_half_updating(repo: Path, path: str, needle: str):
    target = repo / path
    target.write_text(
        target.read_text(encoding="utf-8").replace(needle, "REDACTED"), encoding="utf-8"
    )
    before = {rel: (repo / rel).read_text(encoding="utf-8") for rel in _FILES}

    with pytest.raises(AnchorMissing):
        plan(repo, _at_max(repo)[0])

    assert {rel: (repo / rel).read_text(encoding="utf-8") for rel in _FILES} == before


@pytest.mark.parametrize("bad", ["2.12", "2.12.0rc1", "v2.12.0", "", "2.12.0.1", "latest"])
def test_only_stable_x_y_z_versions_are_accepted(repo: Path, bad: str):
    with pytest.raises(ValueError):
        plan(repo, bad)


# --- reading the version out of the canary's log ------------------------------------------------


def _log(version: str) -> str:
    sep = "\t"
    return (
        f"canary{sep}Report the installed adk-tracegauge version{sep}2026-10-08T06:51:01.1Z adk-tracegauge 0.10.0\n"
        f"canary{sep}Report the installed google-adk version{sep}2026-10-08T06:51:02.4Z google-adk {version}\n"
        f"canary{sep}Run the test suite against latest google-adk{sep}2026-10-08T06:51:30.0Z google-adk 9.9.9\n"
    )


def test_the_version_comes_from_the_canarys_report_step_only():
    assert canary_version_from_log(_log("2.12.0")) == "2.12.0"


def test_a_log_without_the_report_line_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="no `google-adk X.Y.Z` line"):
        canary_version_from_log("canary\tSet up Python\t2026-10-08T06:50:00Z Python 3.11\n")


# --- the command line -----------------------------------------------------------------------------


def test_cli_dry_run_reports_and_writes_nothing(repo: Path, capsys, monkeypatch, tmp_path):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    before = {rel: (repo / rel).read_text(encoding="utf-8") for rel in _FILES}

    tested, new_max = _at_max(repo)
    old_max = fmt(current_max(repo))

    assert main(["--version", tested, "--root", str(repo)]) == 0

    assert f"tested max {old_max} -> {new_max}; 4 file(s) change" in capsys.readouterr().out
    assert {rel: (repo / rel).read_text(encoding="utf-8") for rel in _FILES} == before
    assert "changed=true" in out.read_text(encoding="utf-8")


def test_cli_write_edits_the_files_and_emits_outputs_and_a_pr_body(
    repo: Path, monkeypatch, tmp_path
):
    out, body = tmp_path / "gh_output", tmp_path / "body.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    tested, new_max = _at_max(repo)
    old_max = fmt(current_max(repo))

    code = main(
        ["--version", tested, "--root", str(repo), "--write", "--body-file", str(body)]
        + ["--canary-url", "https://example.invalid/runs/1"]
    )

    assert code == 0 and _guard_holds(repo)
    emitted = out.read_text(encoding="utf-8")
    assert "changed=true" in emitted and f"new_max={new_max}" in emitted
    assert f"old_max={old_max}" in emitted
    text = body.read_text(encoding="utf-8")
    assert f"bare-adk ({tested})" in text and "https://example.invalid/runs/1" in text
    assert "Reviewable / generated split" in text  # gate 3b wants the split stated


def test_cli_inside_the_range_reports_nothing_to_do(repo: Path, capsys, monkeypatch, tmp_path):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    assert main(["--version", "2.11.0", "--root", str(repo), "--write"]) == 0

    assert "inside the tested range" in capsys.readouterr().out
    assert "changed=false" in out.read_text(encoding="utf-8")


def test_cli_from_a_canary_log_file(repo: Path, monkeypatch, tmp_path):
    tested, new_max = _at_max(repo)
    log = tmp_path / "canary.log"
    log.write_text(_log(tested), encoding="utf-8")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert main(["--canary-log", str(log), "--root", str(repo), "--write"]) == 0

    assert fmt(current_max(repo)) == new_max


# --- the workflow's safety properties (text-level: no YAML parser in the test deps) ----------------


_WORKFLOW = (ROOT / ".github/workflows/tested-range-bump.yml").read_text(encoding="utf-8")


def test_the_workflow_only_runs_after_a_green_canary_or_by_hand():
    assert (
        "workflow_run:" in _WORKFLOW
        and 'workflows: ["PyPI canary (latest google-adk)"]' in _WORKFLOW
    )
    assert "github.event.workflow_run.conclusion == 'success'" in _WORKFLOW
    canary = (ROOT / ".github/workflows/pypi-canary.yml").read_text(encoding="utf-8")
    assert "name: PyPI canary (latest google-adk)" in canary  # the trigger name still matches


def test_the_workflow_never_stages_a_workflow_file_and_never_merges():
    stage = [line for line in _WORKFLOW.splitlines() if "git add" in line]
    assert len(stage) == 1 and ".github/workflows" not in stage[0]
    assert ".github/tested-adk-legs.json" in stage[0]
    assert "gh pr merge" not in _WORKFLOW and "--auto" not in _WORKFLOW


def test_the_workflow_dispatches_ci_because_a_bot_opened_pr_gets_no_checks():
    assert "gh workflow run ci.yml" in _WORKFLOW
    assert "actions: write" in _WORKFLOW
