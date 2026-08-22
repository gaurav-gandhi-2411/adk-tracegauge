"""adk_tracegauge/_cost.py — In-house dollar-cost arithmetic (Phase 4 R5).

Through Phase 3, every real dollar figure this package ever reported was
computed by an EXTERNAL dependency: the ``tracegauge`` PyPI package's
``tes.cost.compute_session_cost``/``compute_turn_cost``, fed a
``tes._digest.SessionDigest``/``TurnDigest`` this package built by hand
(see ``_adapter.py``). Phase 4 R5's audit (5.1, full findings in
``PLAN.md``) found every one of the following was a REVERSE-ENGINEERED
assumption about that dependency's internal shape, not a documented
contract:

- ``tes._digest``'s own module docstring states outright "these ... are
  internal to the tes package -- not part of the public API" -- this
  package imported ``SessionDigest``/``TurnDigest`` from it anyway, since
  there was no public alternative.
- ``compute_turn_cost``/``compute_session_cost`` never document their
  ``prices: dict`` parameter's required shape anywhere (no docstring
  mention, no TypedDict, no schema) -- the exact keys read
  (``prices["models"][key]["input_usd_per_mtok"/"output_usd_per_mtok"]``,
  ``prices["cache_multipliers"]["read"/"write_5min"/"write_1hr"]``,
  ``prices["default_model"]``, ``prices.get("model_patterns", [])``,
  ``prices.get("as_of", "unknown")``, ``prices.get("approximate_threshold_pct", 25)``)
  were recovered entirely by reading ``tes/cost.py``'s source.
- ``compute_session_cost``'s own default-``None`` fallback silently loads
  tracegauge's OWN bundled Claude price table -- the exact mechanism that
  produced this package's own historical bug (a $2.80 Gemini call priced at
  $18.00, see ``price_digest``'s docstring in ``_adapter.py``).
- adk-tracegauge's own Apache-2.0 license depended on a fact never checked
  against the actually-INSTALLED package before this audit: ``tes/cost.py``
  and ``tes/_digest.py`` are dual-licensed (AGPL-3.0-only OR Apache-2.0) only
  as of the source carrying an explicit SPDX header -- confirmed present at
  https://github.com/gaurav-gandhi-2411/token-efficiency-scorer HEAD
  (commit ``b582c60565150015d4a9f3cc87bc64f19375e52a``) and in the
  ``tracegauge==0.10.1`` release's bundled ``LICENSE-APACHE``, but ABSENT
  (no per-file header, though still covered by the repo's overall README
  license note) from the ``tracegauge==0.10.0`` release actually resolved
  into this package's own ``.venv`` at audit time -- see 5.4's per-version
  table in ``PLAN.md``.

5.3's assessment (``PLAN.md``, Phase 4 R5) concluded moving the arithmetic
in-house was S-M effort and worth doing: ``compute_turn_cost`` is ~20 lines
and ``compute_session_cost`` ~35, tracegauge's OWN model-resolution
fallback-to-default behavior was already provably DEAD for every real
adk-tracegauge invocation (this package's own ``_pricing.resolve_model_for_call``
always pre-resolves to an exact price-table key or refuses closed BEFORE a
``TurnDigest`` is ever built -- see ``_adapter.build_session_digest``), and
grep-confirmed the arithmetic + these two dataclasses were the ONLY things
adk-tracegauge used from ``tracegauge`` anywhere in ``src/`` -- no scoring,
baselines, judge, or waste-detection features (tracegauge's actual
differentiators, all Claude-Code-session-specific) were ever touched. The
``tracegauge`` PyPI dependency was removed from ``pyproject.toml`` entirely
as a result (Phase 4 R5).

**This file WAS a byte-identical, behavior-preserving PORT of
``tracegauge==0.10.0`` at write time (Phase 4 R5 5.4, diffed directly
against the installed package, line-for-line identical modulo line
endings) -- it no longer is, as of the fail-closed hardening below.**
``compute_turn_cost``'s arithmetic is still unchanged. Its model-resolution
fallback (``_resolve_model_key``) is NOT: ``tracegauge==0.10.0``'s
``_resolve_model`` silently defaulted an unresolved model to
``prices["default_model"]``'s rate, a real overcharge/undercharge bug
tracegauge itself fixed in ``0.10.2`` (its ``_resolve_model`` now returns
``None`` and ``compute_turn_cost`` returns an unpriced ``$0.00`` result
instead of ever guessing). This port kept the OLD, pre-fix behavior for a
long time after that upstream fix landed, reasoning it was safe because
``_pricing.resolve_model_for_call`` (this package's own, separate,
already-fail-closed guard) always pre-resolves or refuses closed BEFORE a
``TurnDigest`` is ever built (``_adapter.build_session_digest``) -- true
for every call reachable through this package's own real call path, but
NOT true for ``compute_turn_cost``/``_resolve_model_key`` themselves:
both are exported (``__all__`` below) and callable directly, bypassing
that upstream guard entirely, and the module's own test suite exercised
exactly that direct path. An exhaustive caller audit (every call site in
``src/``, ``tests/``, ``examples/``, and this package's public re-exports)
found no OTHER caller bypasses the guard today, but "unreachable via the
one caller we have" is a property of the current call graph, not a
guarantee -- so ``_resolve_model_key``/``compute_turn_cost`` are now
independently hardened to fail closed, matching tracegauge's CURRENT
(post-``0.10.2``) behavior rather than staying pinned to its ``0.10.0``
snapshot. See each function's own docstring below for the exact behavior.

One further deliberate, documented behavior CHANGE from the original
``0.10.0`` port, unrelated to the above: ``compute_session_cost`` below has
no ``prices=None`` default (tracegauge's own defaulted to ``None`` and
silently loaded ITS bundled Claude table) -- removed at the source rather
than merely guarded around, since nothing in this codebase has ever called
it without an explicit ``prices=`` argument (the sole call site,
``_adapter.price_digest``, already required it with no default of its own)
and the historical fallback bug this exact default caused is the whole
reason that requirement exists.

``TurnDigest``/``SessionDigest`` below are trimmed from ``tes._digest``'s
original 10-field/11-field shape down to only the fields adk-tracegauge's
own ``_adapter.py``/``snapshot.py`` ever construct with real data or read
(grep-verified against this package's own ``src/`` and ``tests/`` before
trimming, Phase 4 R5 5.3) -- the dropped fields (``tool_names``,
``content_snippet``, per-turn ``h2_duplicate``, ``domain``, ``resolved``,
``total_tokens``, per-session ``h2_duplicate_count``, ``cache_hit_rate``,
``p25_token_ratio``, ``output_tokens_available``, ``task_description``)
existed only to satisfy tracegauge's OWN judge/dashboard rendering
(``digest_to_text``), which adk-tracegauge never called. ``SessionDigest.turn_count``
is now a derived ``@property`` (``len(self.turns)``) rather than a
separately-stored field the caller must keep in sync -- tracegauge's own
shape stored both independently, a real (if minor) invariant-violation risk
this port removes. ``TurnCost``/``SessionCost`` are trimmed similarly
(dropping ``SessionCost.session_id``/``domain_of_validity``/``approximate_turn_count``,
none of which any caller in this repo reads) -- ``TurnCost`` is kept in full
since it costs nothing (produced entirely internally, never hand-built field
by field by a caller) and ``is_approximate``/``approximate_reason`` still
roll up into ``SessionCost.approximate``/``approximate_reasons``, which
``evaluator.py`` does read.

Copyright and license note (Apache-2.0, `LICENSE` in this repository root):
the arithmetic and dataclass shapes in this file originate from
``tracegauge``'s ``tes/cost.py``/``tes/_digest.py`` (Copyright 2026 Gaurav
Gandhi, https://github.com/gaurav-gandhi-2411/token-efficiency-scorer),
which that repository's own source and README dual-license as
AGPL-3.0-only OR Apache-2.0 at the licensor's option -- this file exercises
the Apache-2.0 option, consistent with this package's own Apache-2.0
license. Both packages share the same author; this port is not a
third-party incorporation, but the license note is recorded here anyway
since Apache-2.0 requires it for a derivative file, and because it's the
finding (5.1/5.4) that motivated checking this in the first place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
"""Ported verbatim from tes.cost._DATE_SUFFIX_RE. adk-tracegauge's own
_pricing.resolve_model already strips an 8-digit date suffix (plus a
dashed-YYYY-MM-DD form tracegauge's own resolver doesn't handle) before a
model string ever reaches _resolve_model_key below via a real call site --
so this branch is provably unreachable for any TurnDigest _adapter.py
builds. Kept anyway for behavior-identical fidelity to the ported function
(see module docstring) and because a future direct caller of
compute_turn_cost/compute_session_cost, bypassing _adapter.py's own
pre-resolution, would still hit a correctly-behaving fallback rather than a
KeyError."""


@dataclass
class TurnDigest:
    """One priced AI turn -- see module docstring for the field trim
    rationale (was tes._digest.TurnDigest's 10-field shape)."""

    turn_index: int
    role: str
    token_count_input: int
    token_count_output: int
    cache_read: int
    cache_creation: int = 0
    model: str = ""


@dataclass
class SessionDigest:
    """One priced invocation -- see module docstring for the field trim
    rationale (was tes._digest.SessionDigest's 11-field shape).
    ``turn_count`` is derived, not stored -- see module docstring."""

    session_id: str
    turns: list[TurnDigest]

    @property
    def turn_count(self) -> int:
        return len(self.turns)


@dataclass
class TurnCost:
    """Dollar-cost breakdown for a single priced turn. Field-identical to
    tracegauge==0.10.0's tes.cost.TurnCost at port time -- see module
    docstring for why this one isn't trimmed. No longer field-identical to
    CURRENT tracegauge, which later gained a ``priced: bool`` field plus a
    ``server_tool_warning: str`` field (a Claude-specific server-side-tool
    billing gap this package has no equivalent of) -- not backported here,
    since ``is_approximate``/``approximate_reason`` already carry the same
    signal this trimmed shape needs (see ``_resolve_model_key`` and
    ``compute_turn_cost`` below for the fail-closed behavior this dataclass
    now supports without a dedicated ``priced`` field)."""

    turn_index: int
    model_key: str
    is_approximate: bool
    approximate_reason: str
    fresh_tokens: int
    fresh_cost: float
    cache_read_cost: float
    cache_creation_cost: float
    output_cost: float
    total_usd: float


@dataclass
class SessionCost:
    """Aggregated dollar-cost for a full priced session/invocation -- see
    module docstring for the field trim rationale (was tes.cost.SessionCost's
    8-field shape; dropped session_id/domain_of_validity/approximate_turn_count,
    none of which any caller in this repo reads)."""

    total_usd: float
    turn_costs: list[TurnCost]
    approximate: bool
    approximate_reasons: list[str]
    ai_turn_count: int


def _resolve_model_key(model_str: str, prices: dict[str, Any]) -> tuple[str | None, bool, str]:
    """Resolve a raw model string to a price-table key.

    FAILS CLOSED as of BI1/BJ3.1 -- was "ported verbatim from
    tes.cost._resolve_model" at tracegauge==0.10.0, which silently
    defaulted an unresolved model to ``prices["default_model"]``'s rate
    (see module docstring for why that's a real overcharge/undercharge bug,
    fixed upstream in tracegauge 0.10.2 and now matched here). Returns
    ``(resolved_key, is_approximate, approximate_reason)`` where
    ``resolved_key`` is ``None`` -- never a guessed default -- when the
    model could not be resolved against the price table. Callers
    (``compute_turn_cost``) MUST NOT substitute a default/guessed rate in
    that case.
    """
    cleaned = _DATE_SUFFIX_RE.sub("", model_str.strip())

    models: dict[str, Any] = prices["models"]
    default_key: str = prices["default_model"]

    if not cleaned:
        return None, True, "empty model string — cost unknown, not priced at a guessed/default rate"

    if cleaned in models:
        return cleaned, False, ""

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            return pattern["model_key"], False, ""

    known = ", ".join(sorted(models))
    reason = (
        f"unknown model '{model_str}' — cost unknown, not priced at a guessed/default rate "
        f"(known models: {known}; default_model={default_key} is never substituted). Register "
        "it via the ADK_TRACEGAUGE_PRICE_TABLE env var override."
    )
    return None, True, reason


def compute_turn_cost(
    turn: TurnDigest, prices: dict[str, Any], cache_duration: str = "5min"
) -> TurnCost:
    """Compute the dollar cost for a single AI turn.

    Arithmetic below is still the tracegauge==0.10.0 port, unchanged. Model
    resolution now FAILS CLOSED (BI1/BJ3.1): when ``turn.model`` does not
    resolve against ``prices`` (see ``_resolve_model_key``), this returns a
    ``TurnCost`` with every cost field at ``0.0`` and ``approximate_reason``
    naming the model and the remedy -- NEVER a dollar figure computed at a
    guessed/default rate, matching tracegauge's own current (post-0.10.2)
    behavior rather than the pre-fix behavior this port originally carried.
    ``cache_duration`` controls which cache-creation multiplier is used:
    ``"5min"`` (default) or ``"1hr"`` -- always ``"5min"`` in practice today,
    since every TurnDigest _adapter.py builds sets cache_creation=0 (ADK's
    plugin path never surfaces a separate cache-write token count for any
    provider -- see _adapter.py's module docstring), making
    cache_creation_cost always 0 regardless of this parameter. Kept for
    behavior-identical parity and in case a future provider surfaces a real
    cache-write count.
    """
    model_key, is_approximate, approximate_reason = _resolve_model_key(turn.model, prices)

    if model_key is None:
        # Cost genuinely unknown for this turn -- never substitute the
        # default model's rate. See _resolve_model_key's docstring.
        return TurnCost(
            turn_index=turn.turn_index,
            model_key=turn.model or "(empty)",
            is_approximate=is_approximate,
            approximate_reason=approximate_reason,
            fresh_tokens=0,
            fresh_cost=0.0,
            cache_read_cost=0.0,
            cache_creation_cost=0.0,
            output_cost=0.0,
            total_usd=0.0,
        )

    input_rate: float = prices["models"][model_key]["input_usd_per_mtok"]
    output_rate: float = prices["models"][model_key]["output_usd_per_mtok"]
    cache_mult: dict[str, float] = prices["cache_multipliers"]

    write_mult = cache_mult["write_1hr"] if cache_duration == "1hr" else cache_mult["write_5min"]

    fresh_tokens = max(0, turn.token_count_input - turn.cache_read - turn.cache_creation)

    fresh_cost = fresh_tokens * input_rate / 1_000_000
    cache_read_cost = turn.cache_read * (input_rate * cache_mult["read"]) / 1_000_000
    cache_creation_cost = turn.cache_creation * (input_rate * write_mult) / 1_000_000
    output_cost = turn.token_count_output * output_rate / 1_000_000
    total = fresh_cost + cache_read_cost + cache_creation_cost + output_cost

    return TurnCost(
        turn_index=turn.turn_index,
        model_key=model_key,
        is_approximate=is_approximate,
        approximate_reason=approximate_reason,
        fresh_tokens=fresh_tokens,
        fresh_cost=fresh_cost,
        cache_read_cost=cache_read_cost,
        cache_creation_cost=cache_creation_cost,
        output_cost=output_cost,
        total_usd=total,
    )


def compute_session_cost(
    digest: SessionDigest, prices: dict[str, Any], cache_duration: str = "5min"
) -> SessionCost:
    """Compute the aggregated dollar cost for a full session/invocation.

    Ported (arithmetic unchanged) from tes.cost.compute_session_cost -- see
    module docstring for the ONE deliberate behavior change (``prices`` is
    required here, no ``None``-defaulting fallback to a bundled table) and
    the SessionCost field trim. Only AI turns (``role == "ai"``) are priced;
    user/tool/system turns are skipped -- adk-tracegauge only ever
    constructs "ai"-role turns today (see _adapter.py), so this filter is
    presently a no-op, kept for behavior-identical parity.
    """
    approximate_threshold_pct: int = prices.get("approximate_threshold_pct", 25)

    turn_costs: list[TurnCost] = []
    for turn in digest.turns:
        if turn.role != "ai":
            continue
        turn_costs.append(compute_turn_cost(turn, prices, cache_duration))

    ai_turn_count = len(turn_costs)
    approximate_turn_count = sum(1 for tc in turn_costs if tc.is_approximate)

    # Session-level approximate flag: threshold is STRICTLY greater than the pct.
    session_approximate = False
    if ai_turn_count > 0:
        pct = approximate_turn_count / ai_turn_count * 100
        session_approximate = pct > approximate_threshold_pct

    approximate_reasons = list(
        {tc.approximate_reason for tc in turn_costs if tc.approximate_reason}
    )

    total_usd = sum(tc.total_usd for tc in turn_costs)

    return SessionCost(
        total_usd=total_usd,
        turn_costs=turn_costs,
        approximate=session_approximate,
        approximate_reasons=approximate_reasons,
        ai_turn_count=ai_turn_count,
    )


__all__ = [
    "SessionCost",
    "SessionDigest",
    "TurnCost",
    "TurnDigest",
    "compute_session_cost",
    "compute_turn_cost",
]
