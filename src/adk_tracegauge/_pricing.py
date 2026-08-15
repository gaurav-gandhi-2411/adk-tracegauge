"""adk_tracegauge/_pricing.py — multi-provider price table loading and strict model resolution.

adk-tracegauge ships its own price table covering Gemini (ADK's native
backend), plus Claude and GPT models reachable through ADK's LiteLlm
integration, plus a synthetic zero-cost entry for local/self-hosted models
(Ollama, vLLM) -- see README. Originally Gemini-only (hence the historical
``gemini_prices.json``/``load_gemini_prices`` naming, kept as-is rather than
renamed -- Phase 2 W3 broadened scope, not the file); every symbol here now
covers all three real providers plus the local-model case. This module is
deliberately independent of ``_cost._resolve_model_key`` (through Phase 3,
an external dependency, tracegauge's own ``tes.cost._resolve_model`` --
ported in-house Phase 4 R5, see ``_cost.py``'s module docstring), which
silently defaults to a fallback model rate on no match. That was the right
call for tracegauge's own original problem (best-effort scoring of
imperfect session logs) but wrong here: a cost evaluator that fabricates a
number for an unrecognized model is worse than one that refuses.
``resolve_model`` below returns ``None`` on no match instead of defaulting,
and callers must treat ``None`` as "do not report a cost for this
invocation" rather than falling through to ``_cost``'s own (provably dead
for every real call path -- see ``_cost.py``) default-model fallback.

Phase 2 W3 additions, because they're load-bearing and not obvious from the
code:

- **Provider-prefix stripping.** ADK's LiteLlm wrapper carries model strings
  as ``"<provider>/<model>"`` (confirmed by reading google-adk's
  ``models/lite_llm.py``: ``LlmResponse.model_version`` is set to LiteLLM's
  own ``response.model``, which echoes the requested string, prefix
  included). ``resolve_model`` strips a small allowlist of prefixes
  (``anthropic/``, ``openai/``) that route to first-party APIs whose
  published pricing this table's entries were verified against, then
  matches the bare model name exactly as it always has. Deliberately NOT
  stripped: ``bedrock/``, ``vertex_ai/``, ``azure/`` -- Claude/GPT pricing on
  those platforms can differ from first-party rates (see
  ``shared/platform-availability.md``-style vendor docs), so a call routed
  through one of them fails closed (unresolved) rather than silently
  pricing at a rate that may not apply. Register those via the
  ``ADK_TRACEGAUGE_PRICE_TABLE`` override (below) if you've confirmed the
  rate actually matches.
- **Local/self-hosted models resolve to a real, zero-cost table entry, not
  a bypass.** ``resolve_model_for_call`` recognizes the ``ollama_chat/``,
  ``ollama/``, and ``vllm/`` LiteLlm prefixes (``is_local_model``) and
  routes them to the ``__local_zero_cost__`` entry -- a real row in the
  price table with ``0.0`` rates, not a special-cased short-circuit that
  skips pricing entirely. This keeps local calls flowing through the exact
  same ``compute_session_cost``/threshold-gate pipeline as any priced call
  (trivially producing cost=$0.00 and a PASSED verdict against any positive
  threshold), rather than duplicating that pipeline's logic in a second
  code path. ``resolve_model`` itself (the plain, non-call-site function)
  deliberately does NOT resolve local prefixes -- only
  ``resolve_model_for_call`` does. That split matters: ``resolve_model``
  answers "is this a priced model in the table", which is correctly `None`
  for e.g. ``"ollama_chat/qwen2.5:7b"``; ``resolve_model_for_call`` answers
  "what should this real call cost", which is correctly the zero-cost entry
  for the same string.
- **Custom price registration via ``ADK_TRACEGAUGE_PRICE_TABLE``.** Mirrors
  tracegauge's own ``TES_PRICE_TABLE`` env-var override pattern (see
  ``tes.cost.load_price_table``) for consistency. Points at a JSON file with
  the same schema as the bundled table (``models``, ``model_patterns``,
  ``cache_multipliers``, ``default_model``) to add or replace entries --
  e.g. a self-hosted model behind a paid gateway, or a Bedrock/Azure-routed
  model whose actual negotiated rate differs from the first-party price.
  Deliberately a whole-file override, not a merge/plugin API -- "keep it
  minimal, don't over-engineer a plugin system" per this work item's own
  scope.

Phase 3 B1 (release-blocking fix, not a new feature): **local-model
zero-cost pricing now requires an explicit opt-in.** Ollama has a real paid
product, Ollama Cloud, routed through the *identical* LiteLlm prefix as
local Ollama (``ollama_chat/``, ``ollama/``) -- only the ``api_base``/host
differs (localhost vs. ``https://ollama.com``), and that field is
confirmed NOT reachable at the point a real call lands in
``TraceGaugeUsagePlugin.after_model_callback``: read directly,
google-adk's ``models/lite_llm.py`` builds ``LlmResponse`` from
``response.model`` (litellm's ``ModelResponse.model``, the bare
``"<provider>/<model>"`` string) with no host/endpoint field anywhere on
it; ``LlmResponse`` itself (``models/llm_response.py``) is a pydantic model
with ``extra="forbid"`` and no ``api_base``-shaped field in its schema;
and neither ``CallbackContext`` (= ``agents/context.py``'s ``Context``) nor
the ``InvocationContext`` it wraps expose the underlying ``LiteLlm`` model
client instance (which is the only place ``api_base`` is actually stored,
in ``LiteLlm._additional_args``) to a plugin callback. Since local-vs-cloud
is NOT distinguishable from anything available here, a bare
``ollama_chat/``/``ollama/``/``vllm/`` prefix match is no longer
sufficient on its own to price a call at $0.00 -- see
``ASSUME_LOCAL_ENV_VAR``/``is_local_model_asserted`` below. A wrong $0.00
for a genuinely paid Ollama Cloud call is strictly worse than a loud,
actionable refusal to price (NOT_EVALUATED) -- see
``_adapter.unknown_model_message`` for the actionable remedy text.

Phase 3 B2 (release-blocking fix): **promotional/introductory price
entries can now expire without becoming silently wrong.** An entry may
carry ``promo_until`` (ISO date) and ``standard_rate`` (the published
post-promo input/output rate); ``resolve_model``/``resolve_model_for_call``
report the *effective* rate for "today" automatically (the promotional
rate while ``date.today() <= promo_until``, the standard rate once past
it, no manual table edit required), and ``effective_prices`` below does
the same for the raw dict ``_cost.compute_session_cost`` reads directly
(an in-house function as of Phase 4 R5, previously tracegauge's own -- see
that function's docstring for why the rewrite has to happen here and not
inside the arithmetic itself). An entry whose promo is
approaching or past expiry with no published ``standard_rate`` warns
loudly (``ResolvedModel.standard_rate_warning_due``) rather than silently
either freezing at a now-possibly-wrong number or guessing one.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_DASHED_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")
"""Strips a hyphen-separated YYYY-MM-DD suffix (e.g. the historical OpenAI
snapshot convention "gpt-4o-2024-08-06"). Checked, Phase 2 W3: none of the
GPT-5.x models this table actually prices use this convention as of
2026-08-14 (all fetched IDs -- gpt-5, gpt-5.1, gpt-5.6-sol/terra/luna -- are
bare, undated strings) -- this exists defensively, for a caller referencing
an older dated OpenAI deployment string via LiteLlm, and to strip the date
off a request before failing closed with an accurate model name in the
unresolved-model message rather than a spurious per-day fanout of "unknown
models". Independent of _DATE_SUFFIX_RE above (matches Gemini's and
Anthropic's dateless 8-digit convention, e.g. "-20251101") -- the two never
match the same string, so both are applied unconditionally."""

_LITELLM_PROVIDER_PREFIXES = ("anthropic/", "openai/")
"""LiteLlm provider prefixes stripped before price-table lookup. Deliberately
an allowlist of first-party-API routes only -- see this module's docstring
for why bedrock/vertex_ai/azure are excluded."""

_LOCAL_MODEL_PREFIXES = ("ollama_chat/", "ollama/", "vllm/")
"""LiteLlm provider prefixes that route to a model the caller runs
themselves -- zero marginal API cost by design, PROVIDED the caller has
confirmed that. See is_local_model (structural prefix check only) and
is_local_model_asserted (the actual gate resolve_model_for_call uses --
requires ASSUME_LOCAL_ENV_VAR opt-in, Phase 3 B1)."""

LOCAL_MODEL_KEY = "__local_zero_cost__"
"""The price-table key resolve_model_for_call routes an ASSERTED local-model
call to (see is_local_model_asserted). A real entry in the bundled table
(0.0 rates) -- see resolve_model_for_call and this module's docstring for
why it's a real entry, not a bypass."""

PRICE_TABLE_ENV_VAR = "ADK_TRACEGAUGE_PRICE_TABLE"
"""Environment variable naming a JSON file (same schema as the bundled
table) to load instead of the bundled one -- the extension mechanism for
registering a custom price. Mirrors tracegauge's own TES_PRICE_TABLE."""

ASSUME_LOCAL_ENV_VAR = "ADK_TRACEGAUGE_ASSUME_LOCAL"
"""Environment variable asserting that model strings matching a local-model
prefix (_LOCAL_MODEL_PREFIXES) are genuinely local/self-hosted, not a paid
gateway sharing the identical LiteLlm prefix (Ollama Cloud -- see this
module's docstring, Phase 3 B1). Unset/empty means NO local-prefixed model
is priced at zero cost -- resolve_model_for_call fails closed (returns
None) instead, and the caller reports NOT_EVALUATED with an actionable
message naming this env var (see _adapter.unknown_model_message). Two
accepted forms, checked case-insensitively:

  - "1" / "true" / "yes" / "on": assert EVERY recognized local prefix
    (_LOCAL_MODEL_PREFIXES) as genuinely local.
  - a comma-separated SUBSET of _LOCAL_MODEL_PREFIXES (e.g. "vllm/"): assert
    only the listed prefixes -- e.g. to trust a self-hosted vllm/
    deployment while still failing closed on ollama_chat/, the exact
    prefix Ollama Cloud (a real paid product) also uses. Entries that don't
    exactly match a recognized local prefix are silently ignored -- a typo
    here must never silently WIDEN what gets trusted as zero-cost.

See is_local_model_asserted, the function that actually reads this."""

_ASSUME_LOCAL_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
"""Case-insensitive "assert everything" spellings for ASSUME_LOCAL_ENV_VAR."""

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

PROMO_EXPIRY_WARNING_DAYS = 14
"""Shared lead-time window (days) for two related-but-distinct promotional-
pricing guards (Phase 3 B2): (1) scripts/check_price_freshness.py's CI gate
fails when any entry's promo_until is within this many days of "today" or
already past; (2) ResolvedModel.standard_rate_warning_due (runtime) fires
the same window early for a promotional entry with NO published
standard_rate, so a user sees the "post-promo rate not yet confirmed"
warning before the rate changes, not only after. One constant, not two
independently-tuned magic numbers, since both exist for the same reason:
give a human enough lead time before a promotional entry's pricing stops
being trustworthy without a table update."""

_PRICE_TABLE_CACHE: dict[str, Any] | None = None


def load_gemini_prices() -> dict[str, Any]:
    """Loads the price table (cached after first call).

    Loads from the ``ADK_TRACEGAUGE_PRICE_TABLE`` env var's path when set
    (the custom-price extension mechanism -- see module docstring),
    otherwise the bundled table covering Gemini/Claude/GPT plus the
    zero-cost local-model entry. Cached process-wide after first call --
    tests that toggle ``ADK_TRACEGAUGE_PRICE_TABLE`` must reset
    ``_PRICE_TABLE_CACHE`` to ``None`` themselves for the change to take
    effect, same as tracegauge's own equivalent cache.
    """
    global _PRICE_TABLE_CACHE
    if _PRICE_TABLE_CACHE is None:
        override_path = os.environ.get(PRICE_TABLE_ENV_VAR)
        if override_path:
            _PRICE_TABLE_CACHE = json.loads(Path(override_path).read_text(encoding="utf-8"))
        else:
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

    promo_until: str | None = None
    """ISO date through which input_usd_per_mtok/output_usd_per_mtok above
    are the PROMOTIONAL rate (schema_version 3+, Phase 3 B2). None for a
    non-promotional entry. The rates on this ResolvedModel are already the
    EFFECTIVE rate for "today" (promotional while promo_active, standard
    once past promo_until and a standard_rate is known) -- see
    _effective_rates/_entry_to_resolved; callers never need to apply the
    switch themselves."""
    promo_active: bool = False
    """True iff promo_until is set and date.today() <= promo_until
    (inclusive -- the boundary day itself is still promotional, matching
    vendor phrasing like "$0.75 through December 31, 2026" and the same
    day-inequality convention as is_stale's own boundary choice below).
    Always False for a non-promotional entry."""
    standard_rate_unknown: bool = False
    """True iff promo_until is set but no standard_rate is published for
    this entry -- the rates on this ResolvedModel remain the last-known
    promotional figures even past expiry (never a fabricated guess), and
    standard_rate_warning_due below is the loud signal that this needs a
    human to re-verify."""
    standard_rate_input_usd_per_mtok: float | None = None
    standard_rate_output_usd_per_mtok: float | None = None
    """The published post-promo rate, if known, regardless of whether it is
    currently in effect -- None when standard_rate_unknown (or the entry
    isn't promotional at all)."""

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

    @property
    def standard_rate_warning_due(self) -> bool:
        """True iff this entry is promotional, its post-promo standard_rate
        is genuinely unknown, AND its promo_until is within
        PROMO_EXPIRY_WARNING_DAYS of "today" or already past -- the loud
        pre-expiry warning window from Phase 3 B2 2.3 (never just a silent
        failure at the exact expiry instant). An unparseable promo_until is
        treated as due (fail closed -- can't confirm it ISN'T due)."""
        if not self.standard_rate_unknown or not self.promo_until:
            return False
        try:
            promo_until_date = date.fromisoformat(self.promo_until)
        except ValueError:
            return True
        return (promo_until_date - date.today()).days <= PROMO_EXPIRY_WARNING_DAYS


def _effective_rates(entry: dict[str, Any]) -> tuple[float, float, bool, bool]:
    """Returns (input_usd_per_mtok, output_usd_per_mtok, promo_active,
    standard_rate_unknown) for `entry`, applying automatic promo-expiry
    switching (Phase 3 B2). See ResolvedModel.promo_active/
    standard_rate_unknown for what each flag means.

    An entry with no promo_until is never promotional -- returns its base
    rates verbatim, (promo_active, standard_rate_unknown) = (False, False).

    An entry WITH promo_until:
      - date.today() <= promo_until (inclusive, see ResolvedModel.
        promo_active's docstring for the boundary-day rationale): returns
        the entry's own (promotional) rates, promo_active=True.
      - date.today() > promo_until and standard_rate is published: returns
        standard_rate's rates instead -- automatic, no manual table edit.
        promo_active=False, standard_rate_unknown=False.
      - date.today() > promo_until and standard_rate is NOT published:
        returns the entry's last-known (promotional) rates rather than
        fabricating a post-promo number, with standard_rate_unknown=True
        so callers surface a loud warning.
      - An unparseable promo_until is treated as standard_rate_unknown=True
        with promo_active held True -- fail closed: never silently
        auto-switch off a date that couldn't be parsed.
    """
    promo_until = entry.get("promo_until")
    if not promo_until:
        return entry["input_usd_per_mtok"], entry["output_usd_per_mtok"], False, False

    try:
        promo_until_date = date.fromisoformat(promo_until)
    except ValueError:
        return entry["input_usd_per_mtok"], entry["output_usd_per_mtok"], True, True

    promo_active = date.today() <= promo_until_date
    standard_rate = entry.get("standard_rate")

    if promo_active or standard_rate is None:
        return (
            entry["input_usd_per_mtok"],
            entry["output_usd_per_mtok"],
            promo_active,
            standard_rate is None,
        )

    return standard_rate["input_usd_per_mtok"], standard_rate["output_usd_per_mtok"], False, False


def effective_prices(prices: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns `prices` with every model entry's input_usd_per_mtok/
    output_usd_per_mtok rewritten to its EFFECTIVE (promo-aware) rate for
    "today" -- see _effective_rates.

    This is the dict real pricing call sites must hand to
    _cost.compute_session_cost/compute_turn_cost (via _adapter.price_digest,
    which calls this internally -- Phase 3 B2), because that arithmetic
    reads prices["models"][key]["input_usd_per_mtok"] straight off whatever
    dict it's given (ported unchanged from tracegauge's own tes/cost.py,
    Phase 4 R5 -- see _cost.py's module docstring) with zero knowledge of
    promo_until/standard_rate -- the automatic promo-expiry switch has to
    happen here, before the dict is handed over, not inside the arithmetic
    itself.

    Returns a new dict (top level shallow-copied, each model entry
    shallow-copied) -- never mutates the cached table in place, so this can
    be called freshly every time pricing actually happens (same principle
    as is_stale/ResolvedModel.promo_active: always evaluated against
    date.today() at call time, never baked in at process-cache-load time).
    """
    if prices is None:
        prices = load_gemini_prices()

    effective_models: dict[str, Any] = {}
    for model_key, entry in prices["models"].items():
        input_rate, output_rate, _, _ = _effective_rates(entry)
        new_entry = dict(entry)
        new_entry["input_usd_per_mtok"] = input_rate
        new_entry["output_usd_per_mtok"] = output_rate
        effective_models[model_key] = new_entry

    effective = dict(prices)
    effective["models"] = effective_models
    return effective


def _entry_to_resolved(model_key: str, entry: dict[str, Any]) -> ResolvedModel:
    input_rate, output_rate, promo_active, standard_rate_unknown = _effective_rates(entry)
    standard_rate = entry.get("standard_rate")
    return ResolvedModel(
        model_key=model_key,
        input_usd_per_mtok=input_rate,
        output_usd_per_mtok=output_rate,
        note=entry.get("note", ""),
        fetched_on=entry.get("fetched_on", ""),
        source_url=entry.get("source_url", ""),
        long_context_threshold_tokens=entry.get("long_context_threshold_tokens"),
        long_context_model_key=entry.get("long_context_model_key"),
        promo_until=entry.get("promo_until"),
        promo_active=promo_active,
        standard_rate_unknown=standard_rate_unknown,
        standard_rate_input_usd_per_mtok=(standard_rate or {}).get("input_usd_per_mtok"),
        standard_rate_output_usd_per_mtok=(standard_rate or {}).get("output_usd_per_mtok"),
    )


def _strip_litellm_provider_prefix(model_version: str) -> str:
    """Strips a recognized first-party-API LiteLlm provider prefix, if present.

    Only the providers in _LITELLM_PROVIDER_PREFIXES -- see module docstring
    for why bedrock/vertex_ai/azure routes are deliberately left alone
    (pricing there can diverge from first-party rates).
    """
    for prefix in _LITELLM_PROVIDER_PREFIXES:
        if model_version.startswith(prefix):
            return model_version[len(prefix) :]
    return model_version


def is_local_model(model_version: str) -> bool:
    """True if ``model_version`` carries a LiteLlm prefix associated with a
    backend the caller CAN run themselves (Ollama, vLLM) -- a purely
    STRUCTURAL/syntactic check on the RAW ``model_version`` string, before
    any price-table lookup or prefix stripping.

    This is NOT sufficient on its own to price a call at zero cost (Phase 3
    B1): the identical ``ollama_chat/``/``ollama/`` prefix also routes to
    Ollama Cloud, a real paid product, and nothing available at the point a
    real call reaches this package can distinguish the two -- see this
    module's docstring. Use is_local_model_asserted (which also requires
    the caller's explicit ASSUME_LOCAL_ENV_VAR opt-in) to decide whether a
    call should actually be priced at $0.00; resolve_model_for_call is the
    only function that acts on that decision.
    """
    cleaned = model_version.strip().lower()
    return cleaned.startswith(_LOCAL_MODEL_PREFIXES)


def _asserted_local_prefixes() -> frozenset[str]:
    """Returns the local-model prefixes ASSUME_LOCAL_ENV_VAR asserts as
    genuinely local for this process. Empty if unset/empty -- see
    ASSUME_LOCAL_ENV_VAR's docstring for the two accepted forms.
    """
    raw = os.environ.get(ASSUME_LOCAL_ENV_VAR, "").strip()
    if not raw:
        return frozenset()
    if raw.lower() in _ASSUME_LOCAL_TRUE_VALUES:
        return frozenset(_LOCAL_MODEL_PREFIXES)
    requested = {p.strip().lower() for p in raw.split(",") if p.strip()}
    # Intersect with the known prefixes, not a straight pass-through --
    # ASSUME_LOCAL_ENV_VAR narrows which recognized local prefixes are
    # trusted, it never WIDENS what counts as "local" in the first place
    # (that's is_local_model's job, and a typo here must fail closed, not
    # silently expand the trusted set).
    return frozenset(p for p in _LOCAL_MODEL_PREFIXES if p in requested)


def is_local_model_asserted(model_version: str) -> bool:
    """True iff ``model_version`` both looks local (is_local_model) AND the
    caller has explicitly asserted, via ASSUME_LOCAL_ENV_VAR, that calls
    carrying this specific prefix are genuinely local/self-hosted -- not,
    e.g., Ollama Cloud (see this module's docstring for why the two cases
    are not distinguishable from any field available to
    TraceGaugeUsagePlugin.after_model_callback, Phase 3 B1). This is the
    actual gate resolve_model_for_call uses to decide whether to price a
    local-prefixed call at $0.00 -- is_local_model alone is a structural
    signal only, never sufficient by itself since Phase 3 B1.
    """
    if not is_local_model(model_version):
        return False
    cleaned = model_version.strip().lower()
    return any(cleaned.startswith(prefix) for prefix in _asserted_local_prefixes())


def resolve_model(model_version: str, prices: dict[str, Any] | None = None) -> ResolvedModel | None:
    """Resolves a raw ``model_version`` string to a price-table entry.

    Returns ``None`` if the model is not in the table -- never a default or
    approximate guess. Callers must not report a cost when this returns
    ``None``. Does NOT resolve local-model prefixes (ollama_chat/, ollama/,
    vllm/) to the zero-cost entry -- that is resolve_model_for_call's job
    (see module docstring for why the split exists); this function alone
    correctly reports "not a priced table entry" for those strings.
    """
    if prices is None:
        prices = load_gemini_prices()

    cleaned = model_version.strip()
    cleaned = _strip_litellm_provider_prefix(cleaned)
    cleaned = _DATE_SUFFIX_RE.sub("", cleaned)
    cleaned = _DASHED_DATE_SUFFIX_RE.sub("", cleaned)
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

    This is the tiering-aware, local-model-aware entry point real call
    sites (``_adapter.py``) must use -- ``resolve_model`` alone always
    returns the base (<= threshold) rate, by design, since it has no token
    count to compare against, and never resolves a local-model prefix (see
    is_local_model / module docstring). Returns ``None`` under the same
    conditions ``resolve_model`` does (no match at all), never a default or
    approximate guess -- INCLUDING for a local-prefixed model that has not
    been asserted via ASSUME_LOCAL_ENV_VAR (Phase 3 B1): a bare prefix
    match is fail-closed here, not priced at $0.00, since it cannot be
    distinguished from a paid Ollama Cloud call. See
    is_local_model_asserted and this module's docstring.
    """
    if prices is None:
        prices = load_gemini_prices()

    if is_local_model(model_version):
        if not is_local_model_asserted(model_version):
            # Fails closed -- see is_local_model_asserted's docstring.
            # Callers (build_session_digest -> unknown_model_message) turn
            # this None into an actionable NOT_EVALUATED naming the exact
            # opt-in remedy, never a silent $0.00.
            return None
        # Explicit, named, auditable local-model path -- routed to a real
        # zero-cost table entry (not a bypass) so it flows through the
        # exact same pricing/threshold-gate pipeline as any priced call.
        # See LOCAL_MODEL_KEY and the module docstring.
        return _entry_to_resolved(LOCAL_MODEL_KEY, prices["models"][LOCAL_MODEL_KEY])

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
    "ASSUME_LOCAL_ENV_VAR",
    "LOCAL_MODEL_KEY",
    "PRICE_TABLE_ENV_VAR",
    "PROMO_EXPIRY_WARNING_DAYS",
    "ResolvedModel",
    "effective_prices",
    "is_local_model",
    "is_local_model_asserted",
    "known_model_keys",
    "load_gemini_prices",
    "resolve_model",
    "resolve_model_for_call",
]
