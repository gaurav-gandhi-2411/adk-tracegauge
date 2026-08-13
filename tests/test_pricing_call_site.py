"""Structural guard for the bug caught during development: compute_session_cost
called without an explicit price table silently prices against tracegauge's
bundled Claude table instead of ours.

evaluator._price_digest is the only sanctioned call site (prices is a
required kwarg there, so omitting it is a TypeError, not a wrong number).
This test asserts no second call site can reintroduce the bug by bypassing
that wrapper -- it greps the actual source tree rather than trusting a code
review comment to stay true forever.
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
        f"src/ (evaluator._price_digest). Found {len(call_sites)}: {call_sites}"
    )

    (rel_path, _lineno, call_line) = call_sites[0]
    assert rel_path.name == "evaluator.py"
    assert "prices=prices" in call_line, (
        "the sole call site must forward the required `prices` kwarg through "
        f"unchanged, not a default or a differently-named variable: {call_line}"
    )


def test_price_digest_requires_prices_with_no_default():
    import inspect

    from adk_tracegauge.evaluator import _price_digest

    prices_param = inspect.signature(_price_digest).parameters["prices"]
    assert prices_param.default is inspect.Parameter.empty, (
        "_price_digest's prices parameter must have no default -- that's what "
        "makes omitting it a TypeError instead of a silent wrong price table."
    )
