"""Structural guard for the bug caught during development: compute_session_cost
called without an explicit price table silently prices against tracegauge's
bundled Claude table instead of ours.

_adapter.price_digest is the only sanctioned call site (prices is a
required kwarg there, so omitting it is a TypeError, not a wrong number).
As of Phase 2 W4, both evaluator.py (via its own _price_digest alias) and
snapshot.py (the new CI regression-gate snapshot builder) route through
this one wrapper rather than each calling compute_session_cost themselves
-- moved here (out of evaluator.py, where it originally lived) specifically
so a second real caller could exist without reintroducing the bug. This
test asserts no call site can bypass that wrapper -- it greps the actual
source tree rather than trusting a code review comment to stay true
forever.

Phase 3 B2: price_digest now wraps the caller's `prices` through
_pricing.effective_prices (the promo-expiry auto-switch -- see that
function's docstring for why the rewrite has to happen here, since
tracegauge's own compute_session_cost reads promo-unaware raw rates
straight off whatever dict it's given) before calling compute_session_cost.
The guard below was updated accordingly -- it still asserts the value fed
to compute_session_cost's `prices=` kwarg is derived from the caller's own
`prices` argument, not a re-fetched default or a hardcoded table.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src" / "adk_tracegauge"


def test_compute_session_cost_is_called_from_exactly_one_place():
    # Matches an actual invocation statement (`return compute_session_cost(`,
    # `x = compute_session_cost(`, etc.), not the name merely appearing in a
    # comment or docstring explaining the wrapper's own rationale.
    call_pattern = re.compile(r"(^|[=(,]\s*|return\s+)compute_session_cost\(")

    call_sites = []
    for path in SRC_DIR.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if call_pattern.search(stripped) and not stripped.startswith(("#", '"', "'")):
                call_sites.append((path.relative_to(SRC_DIR), lineno, stripped))

    assert len(call_sites) == 1, (
        "compute_session_cost must be called from exactly one place in "
        f"src/ (_adapter.price_digest). Found {len(call_sites)}: {call_sites}"
    )

    (rel_path, _lineno, call_line) = call_sites[0]
    assert rel_path.name == "_adapter.py"
    # Phase 3 B2: the sole call site now wraps the caller's `prices` through
    # effective_prices (applying the promo-expiry auto-switch) before handing
    # it to compute_session_cost -- still derived from the CALLER's own
    # `prices` argument, never a re-fetched default or a hardcoded table, so
    # this doesn't reintroduce the original bug (see module docstring). The
    # exact wrapping literal is asserted, not just "prices" appearing
    # somewhere in the line, so a future edit that quietly drops the
    # effective_prices() promo-switch (or drops the caller's own `prices`
    # argument) still fails this guard.
    assert "prices=effective_prices(prices)" in call_line, (
        "the sole call site must forward the required `prices` kwarg through "
        "effective_prices() unchanged from the caller's own argument, not a "
        f"default, a differently-named variable, or a bypassed switch: {call_line}"
    )


def test_price_digest_requires_prices_with_no_default():
    import inspect

    from adk_tracegauge._adapter import price_digest

    prices_param = inspect.signature(price_digest).parameters["prices"]
    assert prices_param.default is inspect.Parameter.empty, (
        "price_digest's prices parameter must have no default -- that's what "
        "makes omitting it a TypeError instead of a silent wrong price table."
    )


def test_evaluator_price_digest_alias_also_requires_prices_with_no_default():
    """evaluator._price_digest is a thin alias kept for backward-compatible
    imports (see this module's own docstring) -- it must preserve the same
    no-default guarantee, not just the underlying _adapter.price_digest."""
    import inspect

    from adk_tracegauge.evaluator import _price_digest

    prices_param = inspect.signature(_price_digest).parameters["prices"]
    assert prices_param.default is inspect.Parameter.empty
