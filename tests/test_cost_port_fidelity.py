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

Phase 5 S5 5.3 extended this file with an INDEPENDENT functional-equivalence
proof against the REAL, LIVE external `tracegauge==0.10.1` package (installed
into its own separate scratch venv outside this repo, per S5's own zero-cost/
no-subagent constraints) -- not just a source-diff (Phase 4 R5 already did
that once) and not just this file's own pre-existing hand-computed values.
For every model in the bundled price table (`data/gemini_prices.json`,
using each entry's EFFECTIVE i.e. promo-resolved rate for 2026-08-15) x 5
token-count scenarios (a small call, a call with a meaningful cached-token
fraction, zero output tokens, a nonzero cache_creation count, and an
all-zero call) -- 110 cases total -- the SAME `TurnDigest`+`prices` input was
fed to BOTH `adk_tracegauge._cost.compute_turn_cost` (in this repo's own
venv) and the real `tes.cost.compute_turn_cost` (in the separate tracegauge
scratch venv, bridging `_digest.TurnDigest`'s extra fields --
`tool_names`/`content_snippet`/`h2_duplicate` -- with placeholder values,
confirmed by direct source read to be unused by `compute_turn_cost`'s
arithmetic). Result: **all 110 cases matched EXACTLY, bit-for-bit** (Python
float equality, not `pytest.approx`) -- zero divergence found, no fix
needed. A `compute_session_cost` multi-turn/multi-model aggregation case
(2 AI turns across 2 different models + 1 skipped non-AI turn) also matched
exactly. `_TRACEGAUGE_FIDELITY_CASES` below is that real captured output,
frozen as literal test data -- this file does NOT import `tes`/`tracegauge`
at runtime (that dependency stays fully removed per Phase 4 R5; re-running
the live cross-package comparison requires the separate scratch venv, not
part of this repo's own CI).

Two scenario types have NO tracegauge-side equivalent and are deliberately
NOT cross-package-compared, noted explicitly rather than silently omitted:
(1) long-context TIERING RESOLUTION (which table entry a call's own
prompt_token_count resolves to at the 200,000-token boundary) is decided in
`_pricing.resolve_model_for_call`, entirely upstream of `compute_turn_cost`
-- tracegauge has no context-length-tiering concept at all (confirmed S1/
S2/S3). The arithmetic ONCE a tier is resolved (i.e. computing a turn's cost
against the `*-long-context` price entry) IS covered by the 110-case sweep
above (those synthetic entries are just more rows in the price table); only
the *resolution* step itself (which entry a raw prompt_token_count maps to)
is adk-tracegauge-only, checked directly in
`test_long_context_tiering_boundary_resolves_correctly_adk_tracegauge_only`
below. (2) PROMO-EXPIRY AUTO-SWITCHING (`_pricing.effective_prices`) is also
adk-tracegauge-only and, likewise, resolves to a flat rate before
`compute_turn_cost` ever runs -- the 110-case sweep already uses each
promotional entry's CURRENT effective rate (e.g. `gemini-3.6-flash`/
`gemini-3.7-flash`, still promotional as of 2026-08-15), so the downstream
arithmetic is covered; only the switching logic itself has no tracegauge
equivalent (tracegauge has no promo/staleness machinery at all -- S1, S3).
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
from adk_tracegauge._pricing import load_gemini_prices, resolve_model_for_call

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


# fmt: off
_TRACEGAUGE_FIDELITY_CASES: list[tuple[str, dict, dict, dict]] = [
    ('__local_zero_cost__::small_call', {'model': '__local_zero_cost__', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.0, 'output_usd_per_mtok': 0.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('__local_zero_cost__::cached_call', {'model': '__local_zero_cost__', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.0, 'output_usd_per_mtok': 0.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('__local_zero_cost__::zero_output', {'model': '__local_zero_cost__', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.0, 'output_usd_per_mtok': 0.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('__local_zero_cost__::cache_creation_nonzero', {'model': '__local_zero_cost__', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.0, 'output_usd_per_mtok': 0.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('__local_zero_cost__::zero_everything', {'model': '__local_zero_cost__', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.0, 'output_usd_per_mtok': 0.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('claude-haiku-4-5::small_call', {'model': 'claude-haiku-4-5', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.0, 'output_usd_per_mtok': 5.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0003, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00075, 'total_usd': 0.00105}),
    ('claude-haiku-4-5::cached_call', {'model': 'claude-haiku-4-5', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 1.0, 'output_usd_per_mtok': 5.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.6, 'cache_read_cost': 0.04, 'cache_creation_cost': 0.0, 'output_cost': 1.0, 'total_usd': 1.6400000000000001}),
    ('claude-haiku-4-5::zero_output', {'model': 'claude-haiku-4-5', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.0, 'output_usd_per_mtok': 5.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.5}),
    ('claude-haiku-4-5::cache_creation_nonzero', {'model': 'claude-haiku-4-5', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 1.0, 'output_usd_per_mtok': 5.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.25, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.5, 'total_usd': 0.75}),
    ('claude-haiku-4-5::zero_everything', {'model': 'claude-haiku-4-5', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.0, 'output_usd_per_mtok': 5.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('claude-opus-4-8::small_call', {'model': 'claude-opus-4-8', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0015, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00375, 'total_usd': 0.0052499999999999995}),
    ('claude-opus-4-8::cached_call', {'model': 'claude-opus-4-8', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 600000, 'fresh_cost': 3.0, 'cache_read_cost': 0.2, 'cache_creation_cost': 0.0, 'output_cost': 5.0, 'total_usd': 8.2}),
    ('claude-opus-4-8::zero_output', {'model': 'claude-opus-4-8', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 500000, 'fresh_cost': 2.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 2.5}),
    ('claude-opus-4-8::cache_creation_nonzero', {'model': 'claude-opus-4-8', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 250000, 'fresh_cost': 1.25, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 2.5, 'total_usd': 3.75}),
    ('claude-opus-4-8::zero_everything', {'model': 'claude-opus-4-8', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('claude-opus-5::small_call', {'model': 'claude-opus-5', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0015, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00375, 'total_usd': 0.0052499999999999995}),
    ('claude-opus-5::cached_call', {'model': 'claude-opus-5', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 600000, 'fresh_cost': 3.0, 'cache_read_cost': 0.2, 'cache_creation_cost': 0.0, 'output_cost': 5.0, 'total_usd': 8.2}),
    ('claude-opus-5::zero_output', {'model': 'claude-opus-5', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 500000, 'fresh_cost': 2.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 2.5}),
    ('claude-opus-5::cache_creation_nonzero', {'model': 'claude-opus-5', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 250000, 'fresh_cost': 1.25, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 2.5, 'total_usd': 3.75}),
    ('claude-opus-5::zero_everything', {'model': 'claude-opus-5', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('claude-sonnet-5::small_call', {'model': 'claude-sonnet-5', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0006, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0015, 'total_usd': 0.0021}),
    ('claude-sonnet-5::cached_call', {'model': 'claude-sonnet-5', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 600000, 'fresh_cost': 1.2, 'cache_read_cost': 0.08, 'cache_creation_cost': 0.0, 'output_cost': 2.0, 'total_usd': 3.2800000000000002}),
    ('claude-sonnet-5::zero_output', {'model': 'claude-sonnet-5', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 500000, 'fresh_cost': 1.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 1.0}),
    ('claude-sonnet-5::cache_creation_nonzero', {'model': 'claude-sonnet-5', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.0, 'total_usd': 1.5}),
    ('claude-sonnet-5::zero_everything', {'model': 'claude-sonnet-5', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-2.0-flash::small_call', {'model': 'gemini-2.0-flash', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 300, 'fresh_cost': 3e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 6e-05, 'total_usd': 9e-05}),
    ('gemini-2.0-flash::cached_call', {'model': 'gemini-2.0-flash', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 600000, 'fresh_cost': 0.06, 'cache_read_cost': 0.004000000000000001, 'cache_creation_cost': 0.0, 'output_cost': 0.08, 'total_usd': 0.14400000000000002}),
    ('gemini-2.0-flash::zero_output', {'model': 'gemini-2.0-flash', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 500000, 'fresh_cost': 0.05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.05}),
    ('gemini-2.0-flash::cache_creation_nonzero', {'model': 'gemini-2.0-flash', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 250000, 'fresh_cost': 0.025, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.04, 'total_usd': 0.065}),
    ('gemini-2.0-flash::zero_everything', {'model': 'gemini-2.0-flash', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-2.5-flash::small_call', {'model': 'gemini-2.5-flash', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 300, 'fresh_cost': 9e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.000375, 'total_usd': 0.000465}),
    ('gemini-2.5-flash::cached_call', {'model': 'gemini-2.5-flash', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 600000, 'fresh_cost': 0.18, 'cache_read_cost': 0.012, 'cache_creation_cost': 0.0, 'output_cost': 0.5, 'total_usd': 0.692}),
    ('gemini-2.5-flash::zero_output', {'model': 'gemini-2.5-flash', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 500000, 'fresh_cost': 0.15, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.15}),
    ('gemini-2.5-flash::cache_creation_nonzero', {'model': 'gemini-2.5-flash', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 250000, 'fresh_cost': 0.075, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.25, 'total_usd': 0.325}),
    ('gemini-2.5-flash::zero_everything', {'model': 'gemini-2.5-flash', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-2.5-flash-lite::small_call', {'model': 'gemini-2.5-flash-lite', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 300, 'fresh_cost': 3e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 6e-05, 'total_usd': 9e-05}),
    ('gemini-2.5-flash-lite::cached_call', {'model': 'gemini-2.5-flash-lite', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 600000, 'fresh_cost': 0.06, 'cache_read_cost': 0.004000000000000001, 'cache_creation_cost': 0.0, 'output_cost': 0.08, 'total_usd': 0.14400000000000002}),
    ('gemini-2.5-flash-lite::zero_output', {'model': 'gemini-2.5-flash-lite', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 500000, 'fresh_cost': 0.05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.05}),
    ('gemini-2.5-flash-lite::cache_creation_nonzero', {'model': 'gemini-2.5-flash-lite', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 250000, 'fresh_cost': 0.025, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.04, 'total_usd': 0.065}),
    ('gemini-2.5-flash-lite::zero_everything', {'model': 'gemini-2.5-flash-lite', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.1, 'output_usd_per_mtok': 0.4}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-2.5-pro::small_call', {'model': 'gemini-2.5-pro', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 300, 'fresh_cost': 0.000375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0015, 'total_usd': 0.001875}),
    ('gemini-2.5-pro::cached_call', {'model': 'gemini-2.5-pro', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.75, 'cache_read_cost': 0.05, 'cache_creation_cost': 0.0, 'output_cost': 2.0, 'total_usd': 2.8}),
    ('gemini-2.5-pro::zero_output', {'model': 'gemini-2.5-pro', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.625, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.625}),
    ('gemini-2.5-pro::cache_creation_nonzero', {'model': 'gemini-2.5-pro', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.3125, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.0, 'total_usd': 1.3125}),
    ('gemini-2.5-pro::zero_everything', {'model': 'gemini-2.5-pro', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-2.5-pro-long-context::small_call', {'model': 'gemini-2.5-pro-long-context', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.5, 'output_usd_per_mtok': 15.0}, {'fresh_tokens': 300, 'fresh_cost': 0.00075, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00225, 'total_usd': 0.003}),
    ('gemini-2.5-pro-long-context::cached_call', {'model': 'gemini-2.5-pro-long-context', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 2.5, 'output_usd_per_mtok': 15.0}, {'fresh_tokens': 600000, 'fresh_cost': 1.5, 'cache_read_cost': 0.1, 'cache_creation_cost': 0.0, 'output_cost': 3.0, 'total_usd': 4.6}),
    ('gemini-2.5-pro-long-context::zero_output', {'model': 'gemini-2.5-pro-long-context', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.5, 'output_usd_per_mtok': 15.0}, {'fresh_tokens': 500000, 'fresh_cost': 1.25, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 1.25}),
    ('gemini-2.5-pro-long-context::cache_creation_nonzero', {'model': 'gemini-2.5-pro-long-context', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 2.5, 'output_usd_per_mtok': 15.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.625, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.5, 'total_usd': 2.125}),
    ('gemini-2.5-pro-long-context::zero_everything', {'model': 'gemini-2.5-pro-long-context', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.5, 'output_usd_per_mtok': 15.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.1-flash-lite::small_call', {'model': 'gemini-3.1-flash-lite', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.25, 'output_usd_per_mtok': 1.5}, {'fresh_tokens': 300, 'fresh_cost': 7.5e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.000225, 'total_usd': 0.0003}),
    ('gemini-3.1-flash-lite::cached_call', {'model': 'gemini-3.1-flash-lite', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.25, 'output_usd_per_mtok': 1.5}, {'fresh_tokens': 600000, 'fresh_cost': 0.15, 'cache_read_cost': 0.01, 'cache_creation_cost': 0.0, 'output_cost': 0.3, 'total_usd': 0.45999999999999996}),
    ('gemini-3.1-flash-lite::zero_output', {'model': 'gemini-3.1-flash-lite', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.25, 'output_usd_per_mtok': 1.5}, {'fresh_tokens': 500000, 'fresh_cost': 0.125, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.125}),
    ('gemini-3.1-flash-lite::cache_creation_nonzero', {'model': 'gemini-3.1-flash-lite', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.25, 'output_usd_per_mtok': 1.5}, {'fresh_tokens': 250000, 'fresh_cost': 0.0625, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.15, 'total_usd': 0.2125}),
    ('gemini-3.1-flash-lite::zero_everything', {'model': 'gemini-3.1-flash-lite', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.25, 'output_usd_per_mtok': 1.5}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.1-pro-preview::small_call', {'model': 'gemini-3.1-pro-preview', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0006, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0018, 'total_usd': 0.0024}),
    ('gemini-3.1-pro-preview::cached_call', {'model': 'gemini-3.1-pro-preview', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 600000, 'fresh_cost': 1.2, 'cache_read_cost': 0.08, 'cache_creation_cost': 0.0, 'output_cost': 2.4, 'total_usd': 3.6799999999999997}),
    ('gemini-3.1-pro-preview::zero_output', {'model': 'gemini-3.1-pro-preview', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 500000, 'fresh_cost': 1.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 1.0}),
    ('gemini-3.1-pro-preview::cache_creation_nonzero', {'model': 'gemini-3.1-pro-preview', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.2, 'total_usd': 1.7}),
    ('gemini-3.1-pro-preview::zero_everything', {'model': 'gemini-3.1-pro-preview', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.1-pro-preview-long-context::small_call', {'model': 'gemini-3.1-pro-preview-long-context', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 4.0, 'output_usd_per_mtok': 18.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0012, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0027, 'total_usd': 0.0039}),
    ('gemini-3.1-pro-preview-long-context::cached_call', {'model': 'gemini-3.1-pro-preview-long-context', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 4.0, 'output_usd_per_mtok': 18.0}, {'fresh_tokens': 600000, 'fresh_cost': 2.4, 'cache_read_cost': 0.16, 'cache_creation_cost': 0.0, 'output_cost': 3.6, 'total_usd': 6.16}),
    ('gemini-3.1-pro-preview-long-context::zero_output', {'model': 'gemini-3.1-pro-preview-long-context', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 4.0, 'output_usd_per_mtok': 18.0}, {'fresh_tokens': 500000, 'fresh_cost': 2.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 2.0}),
    ('gemini-3.1-pro-preview-long-context::cache_creation_nonzero', {'model': 'gemini-3.1-pro-preview-long-context', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 4.0, 'output_usd_per_mtok': 18.0}, {'fresh_tokens': 250000, 'fresh_cost': 1.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.8, 'total_usd': 2.8}),
    ('gemini-3.1-pro-preview-long-context::zero_everything', {'model': 'gemini-3.1-pro-preview-long-context', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 4.0, 'output_usd_per_mtok': 18.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.5-flash::small_call', {'model': 'gemini-3.5-flash', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.5, 'output_usd_per_mtok': 9.0}, {'fresh_tokens': 300, 'fresh_cost': 0.00045, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00135, 'total_usd': 0.0018}),
    ('gemini-3.5-flash::cached_call', {'model': 'gemini-3.5-flash', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 1.5, 'output_usd_per_mtok': 9.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.9, 'cache_read_cost': 0.060000000000000005, 'cache_creation_cost': 0.0, 'output_cost': 1.8, 'total_usd': 2.7600000000000002}),
    ('gemini-3.5-flash::zero_output', {'model': 'gemini-3.5-flash', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.5, 'output_usd_per_mtok': 9.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.75, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.75}),
    ('gemini-3.5-flash::cache_creation_nonzero', {'model': 'gemini-3.5-flash', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 1.5, 'output_usd_per_mtok': 9.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.9, 'total_usd': 1.275}),
    ('gemini-3.5-flash::zero_everything', {'model': 'gemini-3.5-flash', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.5, 'output_usd_per_mtok': 9.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.5-flash-lite::small_call', {'model': 'gemini-3.5-flash-lite', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 300, 'fresh_cost': 9e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.000375, 'total_usd': 0.000465}),
    ('gemini-3.5-flash-lite::cached_call', {'model': 'gemini-3.5-flash-lite', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 600000, 'fresh_cost': 0.18, 'cache_read_cost': 0.012, 'cache_creation_cost': 0.0, 'output_cost': 0.5, 'total_usd': 0.692}),
    ('gemini-3.5-flash-lite::zero_output', {'model': 'gemini-3.5-flash-lite', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 500000, 'fresh_cost': 0.15, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.15}),
    ('gemini-3.5-flash-lite::cache_creation_nonzero', {'model': 'gemini-3.5-flash-lite', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 250000, 'fresh_cost': 0.075, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.25, 'total_usd': 0.325}),
    ('gemini-3.5-flash-lite::zero_everything', {'model': 'gemini-3.5-flash-lite', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.3, 'output_usd_per_mtok': 2.5}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.6-flash::small_call', {'model': 'gemini-3.6-flash', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 300, 'fresh_cost': 0.000225, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0005625, 'total_usd': 0.0007875}),
    ('gemini-3.6-flash::cached_call', {'model': 'gemini-3.6-flash', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 600000, 'fresh_cost': 0.45, 'cache_read_cost': 0.030000000000000002, 'cache_creation_cost': 0.0, 'output_cost': 0.75, 'total_usd': 1.23}),
    ('gemini-3.6-flash::zero_output', {'model': 'gemini-3.6-flash', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 500000, 'fresh_cost': 0.375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.375}),
    ('gemini-3.6-flash::cache_creation_nonzero', {'model': 'gemini-3.6-flash', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 250000, 'fresh_cost': 0.1875, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.375, 'total_usd': 0.5625}),
    ('gemini-3.6-flash::zero_everything', {'model': 'gemini-3.6-flash', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gemini-3.7-flash::small_call', {'model': 'gemini-3.7-flash', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 300, 'fresh_cost': 0.000225, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0005625, 'total_usd': 0.0007875}),
    ('gemini-3.7-flash::cached_call', {'model': 'gemini-3.7-flash', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 600000, 'fresh_cost': 0.45, 'cache_read_cost': 0.030000000000000002, 'cache_creation_cost': 0.0, 'output_cost': 0.75, 'total_usd': 1.23}),
    ('gemini-3.7-flash::zero_output', {'model': 'gemini-3.7-flash', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 500000, 'fresh_cost': 0.375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.375}),
    ('gemini-3.7-flash::cache_creation_nonzero', {'model': 'gemini-3.7-flash', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 250000, 'fresh_cost': 0.1875, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.375, 'total_usd': 0.5625}),
    ('gemini-3.7-flash::zero_everything', {'model': 'gemini-3.7-flash', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.75, 'output_usd_per_mtok': 3.75}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gpt-5::small_call', {'model': 'gpt-5', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 300, 'fresh_cost': 0.000375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0015, 'total_usd': 0.001875}),
    ('gpt-5::cached_call', {'model': 'gpt-5', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.75, 'cache_read_cost': 0.05, 'cache_creation_cost': 0.0, 'output_cost': 2.0, 'total_usd': 2.8}),
    ('gpt-5::zero_output', {'model': 'gpt-5', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.625, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.625}),
    ('gpt-5::cache_creation_nonzero', {'model': 'gpt-5', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.3125, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.0, 'total_usd': 1.3125}),
    ('gpt-5::zero_everything', {'model': 'gpt-5', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gpt-5.1::small_call', {'model': 'gpt-5.1', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 300, 'fresh_cost': 0.000375, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0015, 'total_usd': 0.001875}),
    ('gpt-5.1::cached_call', {'model': 'gpt-5.1', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 600000, 'fresh_cost': 0.75, 'cache_read_cost': 0.05, 'cache_creation_cost': 0.0, 'output_cost': 2.0, 'total_usd': 2.8}),
    ('gpt-5.1::zero_output', {'model': 'gpt-5.1', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 500000, 'fresh_cost': 0.625, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.625}),
    ('gpt-5.1::cache_creation_nonzero', {'model': 'gpt-5.1', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.3125, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.0, 'total_usd': 1.3125}),
    ('gpt-5.1::zero_everything', {'model': 'gpt-5.1', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 1.25, 'output_usd_per_mtok': 10.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gpt-5.6-luna::small_call', {'model': 'gpt-5.6-luna', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.2, 'output_usd_per_mtok': 1.2}, {'fresh_tokens': 300, 'fresh_cost': 6e-05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.00018, 'total_usd': 0.00024}),
    ('gpt-5.6-luna::cached_call', {'model': 'gpt-5.6-luna', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 0.2, 'output_usd_per_mtok': 1.2}, {'fresh_tokens': 600000, 'fresh_cost': 0.12, 'cache_read_cost': 0.008000000000000002, 'cache_creation_cost': 0.0, 'output_cost': 0.24, 'total_usd': 0.368}),
    ('gpt-5.6-luna::zero_output', {'model': 'gpt-5.6-luna', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.2, 'output_usd_per_mtok': 1.2}, {'fresh_tokens': 500000, 'fresh_cost': 0.1, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.1}),
    ('gpt-5.6-luna::cache_creation_nonzero', {'model': 'gpt-5.6-luna', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 0.2, 'output_usd_per_mtok': 1.2}, {'fresh_tokens': 250000, 'fresh_cost': 0.05, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.12, 'total_usd': 0.16999999999999998}),
    ('gpt-5.6-luna::zero_everything', {'model': 'gpt-5.6-luna', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 0.2, 'output_usd_per_mtok': 1.2}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gpt-5.6-sol::small_call', {'model': 'gpt-5.6-sol', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 30.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0015, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0045, 'total_usd': 0.006}),
    ('gpt-5.6-sol::cached_call', {'model': 'gpt-5.6-sol', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 30.0}, {'fresh_tokens': 600000, 'fresh_cost': 3.0, 'cache_read_cost': 0.2, 'cache_creation_cost': 0.0, 'output_cost': 6.0, 'total_usd': 9.2}),
    ('gpt-5.6-sol::zero_output', {'model': 'gpt-5.6-sol', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 30.0}, {'fresh_tokens': 500000, 'fresh_cost': 2.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 2.5}),
    ('gpt-5.6-sol::cache_creation_nonzero', {'model': 'gpt-5.6-sol', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 30.0}, {'fresh_tokens': 250000, 'fresh_cost': 1.25, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 3.0, 'total_usd': 4.25}),
    ('gpt-5.6-sol::zero_everything', {'model': 'gpt-5.6-sol', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 30.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
    ('gpt-5.6-terra::small_call', {'model': 'gpt-5.6-terra', 'token_count_input': 300, 'token_count_output': 150, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 300, 'fresh_cost': 0.0006, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0018, 'total_usd': 0.0024}),
    ('gpt-5.6-terra::cached_call', {'model': 'gpt-5.6-terra', 'token_count_input': 1000000, 'token_count_output': 200000, 'cache_read': 400000, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 600000, 'fresh_cost': 1.2, 'cache_read_cost': 0.08, 'cache_creation_cost': 0.0, 'output_cost': 2.4, 'total_usd': 3.6799999999999997}),
    ('gpt-5.6-terra::zero_output', {'model': 'gpt-5.6-terra', 'token_count_input': 500000, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 500000, 'fresh_cost': 1.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 1.0}),
    ('gpt-5.6-terra::cache_creation_nonzero', {'model': 'gpt-5.6-terra', 'token_count_input': 300000, 'token_count_output': 100000, 'cache_read': 0, 'cache_creation': 50000}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 250000, 'fresh_cost': 0.5, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 1.2, 'total_usd': 1.7}),
    ('gpt-5.6-terra::zero_everything', {'model': 'gpt-5.6-terra', 'token_count_input': 0, 'token_count_output': 0, 'cache_read': 0, 'cache_creation': 0}, {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 12.0}, {'fresh_tokens': 0, 'fresh_cost': 0.0, 'cache_read_cost': 0.0, 'cache_creation_cost': 0.0, 'output_cost': 0.0, 'total_usd': 0.0}),
]
# fmt: on


def test_every_price_table_entry_matches_live_tracegauge_arithmetic():
    """Phase 5 S5 5.3 -- the independent functional-equivalence proof.

    Each row of _TRACEGAUGE_FIDELITY_CASES is (case_id, turn_input,
    per_model_rates, expected) where  is the REAL result the live
    external tracegauge==0.10.1 package computed for the identical input, in
    a separate scratch venv (see module docstring). Asserts
    adk_tracegauge._cost.compute_turn_cost reproduces every field EXACTLY
    (bit-for-bit float equality -- the two implementations share the same
    formula and the same operand order, so exact equality is the correct
    bar, not pytest.approx).
    """
    for case_id, turn_input, rates, expected in _TRACEGAUGE_FIDELITY_CASES:
        prices = {
            "default_model": turn_input["model"],
            "approximate_threshold_pct": 25,
            "cache_multipliers": {"read": 0.1, "write_5min": 0.0, "write_1hr": 0.0},
            "model_patterns": [],
            "models": {turn_input["model"]: dict(rates)},
        }
        turn = TurnDigest(
            turn_index=0,
            role="ai",
            token_count_input=turn_input["token_count_input"],
            token_count_output=turn_input["token_count_output"],
            cache_read=turn_input["cache_read"],
            cache_creation=turn_input["cache_creation"],
            model=turn_input["model"],
        )
        result = compute_turn_cost(turn, prices)

        assert result.fresh_tokens == expected["fresh_tokens"], case_id
        assert result.fresh_cost == expected["fresh_cost"], case_id
        assert result.cache_read_cost == expected["cache_read_cost"], case_id
        assert result.cache_creation_cost == expected["cache_creation_cost"], case_id
        assert result.output_cost == expected["output_cost"], case_id
        assert result.total_usd == expected["total_usd"], case_id


def test_fidelity_cases_cover_every_model_in_the_bundled_price_table():
    """Structural guard: _TRACEGAUGE_FIDELITY_CASES must cover every model
    key currently in the bundled price table -- if a future price-table
    addition (a new model) is not also re-verified against a live
    tracegauge run and added here, this test fails loudly rather than
    letting the fidelity claim silently go stale for the new entry. See
    module docstring for how the table was generated (re-run S5 5.3's
    3-script harness -- build_cases in this repo venv, compute in a
    separate tracegauge scratch venv, diff -- to regenerate)."""
    covered_models = {turn_input["model"] for _, turn_input, _, _ in _TRACEGAUGE_FIDELITY_CASES}
    bundled_models = set(load_gemini_prices()["models"].keys())
    missing = bundled_models - covered_models
    assert not missing, (
        f"Price-table model(s) {sorted(missing)} have no live-tracegauge fidelity "
        "case -- add fidelity cases for them (see module docstring) before trusting "
        "the port for these models."
    )


def test_long_context_tiering_boundary_resolves_correctly_adk_tracegauge_only():
    """Phase 5 S5 5.3 -- tiering-boundary check, ADK-TRACEGAUGE ONLY (no
    tracegauge comparison possible: tracegauge has no context-length-tiering
    concept at all, confirmed Phase 5 S1/S2/S3). At exactly the published
    threshold (200,000 tokens), a call must resolve to the BASE entry; one
    token past it, the LONG-CONTEXT entry -- for both Gemini models that
    carry a long-context tier."""
    prices = load_gemini_prices()
    for base_model, long_context_model, threshold in (
        ("gemini-2.5-pro", "gemini-2.5-pro-long-context", 200_000),
        ("gemini-3.1-pro-preview", "gemini-3.1-pro-preview-long-context", 200_000),
    ):
        just_below = resolve_model_for_call(base_model, threshold, prices)
        just_above = resolve_model_for_call(base_model, threshold + 1, prices)
        assert just_below is not None
        assert just_above is not None
        assert just_below.model_key == base_model, (
            f"{base_model} at exactly the {threshold}-token threshold must still resolve "
            "to the base (non-tiered) entry -- tiering applies only STRICTLY ABOVE it."
        )
        assert just_above.model_key == long_context_model, (
            f"{base_model} at {threshold + 1} tokens (one past the threshold) must resolve "
            f"to {long_context_model}."
        )
