"""tests/test_quickstart.py — HH1.1: end-to-end coverage for
`adk-tracegauge quickstart`. This is a real, live run (a real InMemoryRunner
through 64 total toy invocations, ~15-20s wall-clock) -- deliberately not
mocked, since the entire point of this command is that it works out of the
box against what actually ships in the wheel; a mocked version of this test
would not have caught the real mypy-surfaced callback-signature issue or
verified the real deterministic numbers below.
"""

from __future__ import annotations

from adk_tracegauge._quickstart import run_quickstart


def test_quickstart_fires_a_real_regression_and_returns_exit_code_1(capsys):
    exit_code = run_quickstart()

    assert exit_code == 1

    out = capsys.readouterr().out
    assert "mode=paired (key=session_id, 32 overlapping session_ids" in out
    assert "REGRESSION: cost increased significantly" in out
    # Exact, reproducible numbers (same generator as
    # examples/05_hand_rolled_session_id_pairing.py, same seed=42) --
    # asserted precisely, not just "a regression happened", since the whole
    # point of this command is deterministic, reproducible output.
    assert "mean_baseline=$0.010611" in out
    assert "mean_current=$0.014211" in out
    assert "observed effect: +0.003600 USD (+33.93%)" in out
    assert "This ran entirely from what shipped in the installed package" in out
