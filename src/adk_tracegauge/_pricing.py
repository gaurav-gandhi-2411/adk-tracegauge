"""adk_tracegauge/_pricing.py — Gemini price table loading and strict model resolution.

adk-tracegauge ships its own Gemini price table (Gemini pricing is an ADK
concern, not a tracegauge/Claude-Code concern -- see README). This module is
deliberately independent of tracegauge's own ``tes.cost._resolve_model``,
which silently defaults to a fallback model rate on no match. That is the
right call for tracegauge's own problem (best-effort scoring of imperfect
session logs) but wrong here: a cost evaluator that fabricates a number for
an unrecognized model is worse than one that refuses. ``resolve_model``
below returns ``None`` on no match instead of defaulting, and callers must
treat ``None`` as "do not report a cost for this invocation" rather than
falling through to tracegauge's own default-model path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Any

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")

STALE_THRESHOLD_DAYS = 90
"""A price entry older than this is flagged, not silently trusted. Gemini
pricing has no published change cadence to derive this number from
precisely -- 90 days is a deliberately conservative round number, not a
measured constant (tightened from 180 in Phase 2 W1 after a live P0 finding:
a promotional per-model rate scheduled to change on a fixed calendar date,
not a token-usage threshold, was found stale-by-construction under the old
180-day window -- see gemini-3.6-flash/gemini-3.7-flash entries in
gemini_prices.json). Tune down further if you have evidence prices move
faster still.

Staleness is always evaluated against "today" as of the moment the check
runs (``date.today()`` in ``ResolvedModel.is_stale``, or the CI runner's
clock in scripts/check_price_freshness.py) -- never a date baked into the
library at import time. That is intentional: a price table shipped inside a
user's installed package has no way to know the real "today" except by
asking the running process's own clock, and the whole point of this guard
is to catch drift between the table's fetched_on and whatever day the
check actually executes."""

_PRICE_TABLE_CACHE: dict[str, Any] | None = None


def load_gemini_prices() -> dict[str, Any]:
    """Loads the bundled Gemini price table (cached after first call)."""
    global _PRICE_TABLE_CACHE
    if _PRICE_TABLE_CACHE is None:
        pkg_files = resources.files("adk_tracegauge") / "data" / "gemini_prices.json"
        _PRICE_TABLE_CACHE = json.loads(pkg_files.read_text(encoding="utf-8"))
    return _PRICE_TABLE_CACHE


@dataclass
class ResolvedModel:
    """A model string that matched a table entry, with its priced rates."""

    model_key: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    note: str
    fetched_on: str
    source_url: str
    long_context_threshold_tokens: int | None = None
    long_context_model_key: str | None = None
    """Both None unless this model has a published context-length pricing
    tier (schema_version 2+). When set, a call whose prompt_token_count
    exceeds long_context_threshold_tokens must be re-priced against the
    entry named by long_context_model_key instead of this one -- see
    resolve_model_for_call, the only function that should act on these two
    fields. resolve_model itself never applies the tier (it has no token
    count to compare against), so existing callers of resolve_model are
    unaffected by schema_version 2 -- they keep getting the base (<=
    threshold) rate exactly as before."""

    @property
    def is_stale(self) -> bool:
        """True when this entry's fetched_on is older than STALE_THRESHOLD_DAYS."""
        try:
            fetched = date.fromisoformat(self.fetched_on)
        except ValueError:
            # An unparseable date is itself a staleness signal -- treat it
            # as stale rather than silently skipping the check.
            return True
        return (date.today() - fetched).days > STALE_THRESHOLD_DAYS


def _entry_to_resolved(model_key: str, entry: dict[str, Any]) -> ResolvedModel:
    return ResolvedModel(
        model_key=model_key,
        input_usd_per_mtok=entry["input_usd_per_mtok"],
        output_usd_per_mtok=entry["output_usd_per_mtok"],
        note=entry.get("note", ""),
        fetched_on=entry.get("fetched_on", ""),
        source_url=entry.get("source_url", ""),
        long_context_threshold_tokens=entry.get("long_context_threshold_tokens"),
        long_context_model_key=entry.get("long_context_model_key"),
    )


def resolve_model(model_version: str, prices: dict[str, Any] | None = None) -> ResolvedModel | None:
    """Resolves a raw ``model_version`` string to a price-table entry.

    Returns ``None`` if the model is not in the table -- never a default or
    approximate guess. Callers must not report a cost when this returns
    ``None``.
    """
    if prices is None:
        prices = load_gemini_prices()

    cleaned = _DATE_SUFFIX_RE.sub("", model_version.strip())
    models: dict[str, Any] = prices["models"]

    if cleaned in models:
        return _entry_to_resolved(cleaned, models[cleaned])

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            model_key = pattern["model_key"]
            return _entry_to_resolved(model_key, models[model_key])

    return None


def resolve_model_for_call(
    model_version: str, prompt_token_count: int, prices: dict[str, Any] | None = None
) -> ResolvedModel | None:
    """Resolves ``model_version`` to a price-table entry, applying the
    model's long-context tier (if any) when ``prompt_token_count`` crosses
    its published threshold.

    This is the tiering-aware entry point real call sites (``_adapter.py``)
    must use -- ``resolve_model`` alone always returns the base (<=
    threshold) rate, by design, since it has no token count to compare
    against. Returns ``None`` under the same conditions ``resolve_model``
    does (no match at all), never a default or approximate guess.
    """
    if prices is None:
        prices = load_gemini_prices()

    resolved = resolve_model(model_version, prices)
    if resolved is None:
        return None

    if (
        resolved.long_context_model_key is not None
        and resolved.long_context_threshold_tokens is not None
        and prompt_token_count > resolved.long_context_threshold_tokens
    ):
        models: dict[str, Any] = prices["models"]
        tier_key = resolved.long_context_model_key
        return _entry_to_resolved(tier_key, models[tier_key])

    return resolved


def known_model_keys(prices: dict[str, Any] | None = None) -> list[str]:
    """Returns the model keys this table can price, for error messages."""
    if prices is None:
        prices = load_gemini_prices()
    return sorted(prices["models"].keys())


__all__ = [
    "ResolvedModel",
    "load_gemini_prices",
    "resolve_model",
    "resolve_model_for_call",
    "known_model_keys",
]
