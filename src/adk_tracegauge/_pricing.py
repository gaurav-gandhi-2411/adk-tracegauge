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
from importlib import resources
from typing import Any

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")

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
        entry = models[cleaned]
        return ResolvedModel(
            model_key=cleaned,
            input_usd_per_mtok=entry["input_usd_per_mtok"],
            output_usd_per_mtok=entry["output_usd_per_mtok"],
            note=entry.get("note", ""),
        )

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            model_key = pattern["model_key"]
            entry = models[model_key]
            return ResolvedModel(
                model_key=model_key,
                input_usd_per_mtok=entry["input_usd_per_mtok"],
                output_usd_per_mtok=entry["output_usd_per_mtok"],
                note=entry.get("note", ""),
            )

    return None


def known_model_keys(prices: dict[str, Any] | None = None) -> list[str]:
    """Returns the model keys this table can price, for error messages."""
    if prices is None:
        prices = load_gemini_prices()
    return sorted(prices["models"].keys())


__all__ = ["ResolvedModel", "load_gemini_prices", "resolve_model", "known_model_keys"]
