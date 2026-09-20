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
    # HAND-CHECKED, not copied from the output (the earlier version of this test pinned a doubled
    # $0.010611 and so confirmed a bug). gemini-2.5-flash rates: $0.30 per 1M input tokens,
    # $2.50 per 1M output tokens. Per call the fake model reports
    #   prompt = 5,000 + (i * 4,723) % 25,000 tokens, i = 0..31   -> mean prompt = 17,269 tokens
    #   output = 50 tokens
    # baseline mean cost = 17,269 * 0.30/1e6 + 50 * 2.50/1e6
    #                    = $0.0051807 + $0.000125 = $0.0053057  -> "$0.005306"
    # the regressed run adds a fixed 6,000 prompt tokens per call:
    #   + 6,000 * 0.30/1e6 = +$0.0018   -> $0.0071057 -> "$0.007106"; effect +$0.001800 = +33.93%.
    # One call per invocation and ONE registration of the plugin: a second registration would
    # print exactly twice these figures (and now raises DoubleRegistrationError instead).
    assert "mean_baseline=$0.005306" in out
    assert "mean_current=$0.007106" in out
    assert "observed effect: +0.001800 USD (+33.93%)" in out
    assert "$0.010611" not in out  # the doubled figure the 0.6.1 quickstart printed
    assert "This ran entirely from what shipped in the installed package" in out


def test_quickstart_reported_cost_equals_independently_computed_true_cost(capsys):
    """The exact-string assertions above once pinned a DOUBLED figure ($0.010611) for over a
    release, because the demo wired the plugin twice and the test was written from the output
    rather than from the arithmetic. This recomputes the mean from the generator's own token
    counts and gemini-2.5-flash's published rates ($0.30/M input, $2.50/M output), independent
    of the code under test."""
    from adk_tracegauge._quickstart import N_CASES, _case_level_prompt_tokens

    true_mean = (
        sum(_case_level_prompt_tokens(i) * 0.30 / 1e6 + 50 * 2.50 / 1e6 for i in range(N_CASES))
        / N_CASES
    )

    run_quickstart()

    out = capsys.readouterr().out
    assert f"mean_baseline=${true_mean:.6f}" in out
