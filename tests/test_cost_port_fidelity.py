"""Phase 4 R5 5.3 — port-fidelity tests for adk_tracegauge._cost.

Through Phase 3, every dollar figure this package reported was computed by
an external dependency (the `tracegauge` PyPI package's
`tes.cost.compute_session_cost`/`compute_turn_cost`, operating on
`tes._digest.SessionDigest`/`TurnDigest`). Phase 4 R5's audit (5.1, see
PLAN.md) found this was the package's ONLY use of `tracegauge` anywhere in
`src/` (grep-confirmed -- no scoring/baseline/judge/waste-detection
features were ever touched), the arithmetic itself was genuinely simple
(~55 combined lines), and the model-resolution fallback-to-default behavior
tracegauge's own resolver used was already provably dead for every real
adk-tracegauge invocation. 5.3 concluded moving it in-house was worth doing
and implemented it (`src/adk_tracegauge/_cost.py`) -- the `tracegauge`
dependency was then removed from `pyproject.toml` entirely.

Before the port, 5.2 added `tests/test_tracegauge_contract.py`-shaped
contract tests asserting tracegauge's own shape (dict keys, dataclass
fields, function signatures) directly against the LIVE dependency, and ran
them for real against both `tracegauge` versions admitted by the
then-current pin (`0.10.0`, `0.10.1` -- the only two releases in
`>=0.10.0,<0.11.0` per PyPI's JSON API) in isolated scratch venvs: 8/8
passed on both (see PLAN.md's Phase 4 R5 5.4 table for the full results,
including a real finding -- 0.10.0's `tes/cost.py` lacks the SPDX
dual-license header 0.10.1 carries, though the upstream repo's own README
license note covers 0.10.0 too at the repo level). Those contract tests
protected a dependency this package no longer has, so they are not part of
the committed suite -- this file is their permanent successor: it verifies
`_cost.py`'s PORTED arithmetic directly, via hand-computed values (not
reused from tracegauge's own test suite, which this package never had
access to) rather than a live external shape.
"""

from __future__ import annotations

import inspect

import pytest

from adk_tracegauge._cost import (
    SessionCost,
    SessionDigest,
    TurnCost,
    TurnDigest,
    compute_session_cost,
    compute_turn_cost,
)

_PRICES = {
    "default_model": "test-model",
    "approximate_threshold_pct": 25,
    "cache_multipliers": {"read": 0.1, "write_5min": 0.0, "write_1hr": 0.0},
    "model_patterns": [{"prefix": "test-mo", "model_key": "test-model"}],
    "models": {
        "test-model": {"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 10.0},
    },
}


def test_compute_turn_cost_matches_hand_computed_arithmetic():
    """Same scenario, same hand-computed expected values as the retired
    live-tracegauge contract test (PLAN.md, Phase 4 R5 5.2/5.4) -- proves
    the port is byte-identical in behavior, not just "looks similar".

    fresh_tokens = 1,000,000 - 200,000 (cache_read) - 0 (cache_creation) = 800,000
    fresh_cost = 800,000 * 2.0 / 1e6 = 1.60
    cache_read_cost = 200,000 * (2.0 * 0.1) / 1e6 = 0.04
    output_cost = 500,000 * 10.0 / 1e6 = 5.00
    total = 1.60 + 0.04 + 0 + 5.00 = 6.64
    """
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        token_count_input=1_000_000,
        token_count_output=500_000,
        cache_read=200_000,
        cache_creation=0,
        model="test-model",
    )

    result = compute_turn_cost(turn, _PRICES)

    assert result.fresh_tokens == 800_000
    assert result.fresh_cost == pytest.approx(1.60)
    assert result.cache_read_cost == pytest.approx(0.04)
    assert result.cache_creation_cost == 0.0
    assert result.output_cost == pytest.approx(5.00)
    assert result.total_usd == pytest.approx(6.64)
    assert result.model_key == "test-model"
    assert result.is_approximate is False
    assert result.approximate_reason == ""


def test_compute_session_cost_sums_multiple_turns_and_skips_non_ai_role():
    """Two priced "ai" turns plus one non-"ai" turn (never produced by
    _adapter.py today, but the role filter is ported behavior -- see
    _cost.py's module docstring) -- the non-"ai" turn must be excluded from
    both the sum and ai_turn_count."""
    digest = SessionDigest(
        session_id="sess-1",
        turns=[
            TurnDigest(
                turn_index=0,
                role="ai",
                token_count_input=100_000,
                token_count_output=50_000,
                cache_read=0,
                model="test-model",
            ),
            TurnDigest(
                turn_index=1,
                role="user",
                token_count_input=999_999,
                token_count_output=999_999,
                cache_read=0,
                model="test-model",
            ),
            TurnDigest(
                turn_index=2,
                role="ai",
                token_count_input=200_000,
                token_count_output=0,
                cache_read=0,
                model="test-model",
            ),
        ],
    )

    result = compute_session_cost(digest, _PRICES)

    # turn 0: fresh=100_000*2.0/1e6=0.2, output=50_000*10.0/1e6=0.5 -> 0.7
    # turn 2: fresh=200_000*2.0/1e6=0.4, output=0 -> 0.4
    # total = 1.1; the "user" turn contributes nothing.
    assert result.ai_turn_count == 2
    assert len(result.turn_costs) == 2
    assert result.total_usd == pytest.approx(1.1)
    assert result.approximate is False
    assert result.approximate_reasons == []


def test_compute_turn_cost_unknown_model_falls_back_to_default_and_flags_approximate():
    """The ported fallback-to-default-model behavior (tes.cost._resolve_model's
    original semantics) -- provably unreachable via _adapter.build_session_digest's
    own real call path (it pre-resolves or refuses closed before a TurnDigest
    is ever built -- see _pricing.resolve_model_for_call), but kept for
    behavior-identical parity with the ported function and as a defensive
    fallback for any future direct caller of compute_turn_cost. Exercised
    directly here since nothing else in this suite ever triggers it."""
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        token_count_input=1_000_000,
        token_count_output=0,
        cache_read=0,
        model="totally-unknown-model",
    )

    result = compute_turn_cost(turn, _PRICES)

    assert result.model_key == "test-model"  # default_model
    assert result.is_approximate is True
    assert "totally-unknown-model" in result.approximate_reason
    assert "defaulted to test-model" in result.approximate_reason


def test_compute_turn_cost_empty_model_string_falls_back_to_default():
    """Same ported, provably-unreachable-via-_adapter.py fallback as the
    unknown-model test above, exercising the OTHER early-return branch in
    _resolve_model_key (an empty/whitespace-only model string)."""
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        token_count_input=1_000_000,
        token_count_output=0,
        cache_read=0,
        model="   ",
    )

    result = compute_turn_cost(turn, _PRICES)

    assert result.model_key == "test-model"  # default_model
    assert result.is_approximate is True
    assert "empty model string" in result.approximate_reason


def test_compute_turn_cost_matches_via_model_patterns_prefix():
    """Exercises _resolve_model_key's model_patterns prefix-match branch
    (a model string that isn't an exact table key but matches a registered
    prefix pattern) -- adk-tracegauge's own price table uses this mechanism
    for real (see gemini_prices.json's model_patterns), even though
    _adapter.py's own real call path pre-resolves before ever reaching here."""
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        token_count_input=1_000_000,
        token_count_output=0,
        cache_read=0,
        model="test-model-preview-variant",
    )

    result = compute_turn_cost(turn, _PRICES)

    assert result.model_key == "test-model"
    assert result.is_approximate is False
    assert result.approximate_reason == ""


def test_compute_session_cost_requires_prices_with_no_default():
    """Deliberate hardening (Phase 4 R5, see _cost.py's module docstring):
    tracegauge's own compute_session_cost defaulted prices=None and silently
    fell back to ITS bundled Claude table when omitted -- the exact mechanism
    behind a real historical bug (_adapter.price_digest's docstring). The
    ported version removes the footgun at the source instead of only
    guarding around it."""
    sig = inspect.signature(compute_session_cost)
    prices_param = sig.parameters["prices"]
    assert prices_param.default is inspect.Parameter.empty, (
        "compute_session_cost's prices parameter must have no default -- "
        "see this test's own docstring and _cost.py's module docstring for "
        "why this is a deliberate hardening, not an oversight."
    )


def test_session_digest_turn_count_is_derived_never_stored():
    """turn_count must always equal len(turns), by construction -- it is a
    property, not a separately-passed field (tracegauge's own SessionDigest
    stored both independently, a real invariant-violation risk this port
    removes -- see _cost.py's module docstring)."""
    digest = SessionDigest(
        session_id="sess-1",
        turns=[
            TurnDigest(
                turn_index=0, role="ai", token_count_input=1, token_count_output=1, cache_read=0
            ),
            TurnDigest(
                turn_index=1, role="ai", token_count_input=1, token_count_output=1, cache_read=0
            ),
        ],
    )
    assert digest.turn_count == 2
    digest.turns.append(
        TurnDigest(turn_index=2, role="ai", token_count_input=1, token_count_output=1, cache_read=0)
    )
    assert digest.turn_count == 3, (
        "turn_count did not stay in sync with turns -- it must be derived."
    )


def test_cost_module_no_longer_imports_the_external_tracegauge_package():
    """Structural guard: _cost.py (and therefore this package's whole
    pricing pipeline) must not import anything from `tes`/`tracegauge` --
    that dependency was removed entirely (Phase 4 R5). Greps the actual
    source rather than trusting a docstring claim to stay true forever,
    matching test_pricing_call_site.py's own existing pattern."""
    import re
    from pathlib import Path

    src_dir = Path(__file__).parent.parent / "src" / "adk_tracegauge"
    offending: list[tuple[Path, int, str]] = []
    pattern = re.compile(r"^\s*(from tes\b|import tes\b)")
    for path in src_dir.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.match(line):
                offending.append((path.relative_to(src_dir), lineno, line.strip()))

    assert not offending, (
        f"src/adk_tracegauge/ still imports from the external `tes`/`tracegauge` "
        f"package, which was supposed to be fully removed (Phase 4 R5): {offending}"
    )


def test_turncost_and_sessioncost_dataclasses_carry_every_field_evaluator_reads():
    """Regression guard for the SessionCost/TurnCost field trim (Phase 4 R5
    5.3): every field evaluator.py/snapshot.py actually read from the
    former external tracegauge dataclasses must still exist on the
    in-house ones (grep-verified against src/ before trimming; see
    _cost.py's module docstring for exactly which fields were dropped and
    why -- none of the ones asserted here)."""
    import dataclasses

    turncost_fields = {f.name for f in dataclasses.fields(TurnCost)}
    for name in (
        "turn_index",
        "model_key",
        "fresh_tokens",
        "fresh_cost",
        "cache_read_cost",
        "output_cost",
        "total_usd",
    ):
        assert name in turncost_fields, f"TurnCost lost field {name!r}, still read by evaluator.py"

    sessioncost_fields = {f.name for f in dataclasses.fields(SessionCost)}
    for name in ("total_usd", "turn_costs", "approximate", "approximate_reasons", "ai_turn_count"):
        assert name in sessioncost_fields, (
            f"SessionCost lost field {name!r}, still read by evaluator.py"
        )
