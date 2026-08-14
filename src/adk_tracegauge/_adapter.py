"""adk_tracegauge/_adapter.py — Maps captured ADK usage data onto tracegauge's digest shape.

tracegauge's tes.cost.compute_session_cost operates on a tes._digest.SessionDigest
(a list of TurnDigest, one per AI turn). One ADK invocation can involve more
than one real model call (tool loops, sub-agent delegation), so it maps onto
a SessionDigest with one TurnDigest per real call -- summed cost across the
whole invocation, same as tracegauge sums cost across a whole Claude Code
session.

Three things happen here before any TurnDigest is built, all fail-closed:

1. Streamed chunks of one real call are collapsed into a single TurnDigest
   using each CapturedCall's own `partial` flag (ADK's own chunk-boundary
   signal -- see _plugin.py/_store.py), with the final (non-partial) chunk's
   own reported totals treated as authoritative, per Gemini's documented
   streaming behavior: usage_metadata is present on every chunk with a
   cumulative running total, the final chunk holding the true total (see
   README, "Streaming" -- doc-corroborated, not independently confirmed
   against a live API call). That assumption is verified at runtime, every
   time: token counts must be monotonically non-decreasing within a group,
   or the group is reported unresolved rather than priced under a broken
   assumption. A trailing group that never reaches a non-partial terminator
   (an interrupted stream) is unresolved for the same reason -- its true
   total is genuinely unknown.

2. A call reporting nonzero tool_use_prompt_token_count (Gemini server-side
   built-in tool use, e.g. Google Search grounding) has no verified billing
   rate in this table -- adaptation fails closed rather than silently
   ignoring billed tokens (see AdaptResult.unpriced_component).

3. Model resolution happens once per real call, tiering-aware (a long-context
   model resolves to its own, higher, over-threshold rate once
   prompt_token_count crosses the published threshold -- see
   _pricing.resolve_model_for_call). A call whose model_version doesn't
   match the Gemini price table at all makes the whole adaptation fail
   closed (see AdaptResult.unresolved_model) rather than producing a
   partially-priced or silently-approximate result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tes._digest import SessionDigest, TurnDigest
from tes.cost import SessionCost, compute_session_cost

from ._pricing import (
    ASSUME_LOCAL_ENV_VAR,
    PRICE_TABLE_ENV_VAR,
    effective_prices,
    is_local_model,
    known_model_keys,
    resolve_model_for_call,
)
from ._store import CapturedCall


@dataclass
class AdaptResult:
    """A ready-to-price digest, or the specific reason pricing was refused."""

    digest: SessionDigest | None
    unresolved_model: str | None = None
    streaming_anomaly: str | None = None
    unpriced_component: str | None = None
    """Set when a real call includes a token category adk-tracegauge cannot
    price with confidence (currently: tool_use_prompt_token_count > 0, from
    Gemini's server-side built-in tools). Same fail-closed philosophy as
    unresolved_model -- refuse rather than under-report cost by silently
    ignoring billed tokens. See CapturedCall's docstring in _store.py."""

    @property
    def ok(self) -> bool:
        return self.digest is not None


def _group_streaming_calls(
    calls: list[CapturedCall],
) -> tuple[list[list[CapturedCall]], str | None]:
    """Groups raw captured calls into real API calls using CapturedCall.partial.

    Returns (groups, None) on success -- one group per real model call, each
    group being zero or more partial=True chunks followed by exactly one
    partial=False terminator (a non-streamed call is simply a group of one).
    Returns ([], reason) if a group's own totals aren't monotonically
    non-decreasing, or a trailing partial group never reaches a terminator.
    """
    groups: list[list[CapturedCall]] = []
    current: list[CapturedCall] = []
    for call in calls:
        current.append(call)
        if not call.partial:
            groups.append(current)
            current = []
    if current:
        return [], (
            f"a streaming response never reached a final (non-partial) chunk -- "
            f"{len(current)} partial chunk(s) captured with no terminator, so "
            "its true total token usage is unknown"
        )

    for group in groups:
        prev_total = -1
        for call in group:
            if call.total_token_count < prev_total:
                return [], (
                    "usage_metadata.total_token_count decreased between streamed "
                    f"chunks ({prev_total} -> {call.total_token_count}) for model "
                    f"'{call.model_version}' -- the assumption that every chunk "
                    "carries a cumulative running total (see README, 'Known limitations') "
                    "does not hold for this response, so its cost cannot be trusted"
                )
            prev_total = call.total_token_count

    return groups, None


def build_session_digest(invocation_id: str, calls: list[CapturedCall]) -> AdaptResult:
    """Builds a SessionDigest from captured calls, or reports why it refused to.

    Every real call's model_version must resolve against the Gemini price
    table, and every streamed call's chunk totals must pass the monotonicity
    check above. On the first failure of either kind, adaptation stops and
    reports it -- no partial digest, no fallback pricing.
    """
    groups, anomaly = _group_streaming_calls(calls)
    if anomaly is not None:
        return AdaptResult(digest=None, streaming_anomaly=anomaly)

    turns: list[TurnDigest] = []

    for index, group in enumerate(groups):
        # The non-partial terminator carries each real call's true, complete
        # totals -- intermediate partial chunks are superseded by it, not
        # summed with it.
        final_call = group[-1]

        if final_call.tool_use_prompt_token_count:
            return AdaptResult(
                digest=None,
                unpriced_component=(
                    f"call for model '{final_call.model_version}' includes "
                    f"{final_call.tool_use_prompt_token_count} tool_use_prompt "
                    "token(s) (Gemini server-side built-in tool use, e.g. "
                    "Google Search grounding or code execution) -- "
                    "adk-tracegauge has no verified billing rate for this "
                    "token category and refuses to under-report cost by "
                    "silently ignoring it rather than fabricate one. See "
                    "README."
                ),
            )

        resolved = resolve_model_for_call(final_call.model_version, final_call.prompt_token_count)
        if resolved is None:
            return AdaptResult(digest=None, unresolved_model=final_call.model_version)

        turns.append(
            TurnDigest(
                turn_index=index,
                role="ai",
                tool_names=[],
                content_snippet="",
                token_count_input=final_call.prompt_token_count,
                # thoughts_token_count ("thinking" tokens) is billed as
                # output per Gemini's pricing pages -- folded in here so it
                # isn't silently undercounted (Phase 2 W1 P0 finding).
                token_count_output=(
                    final_call.candidates_token_count + final_call.thoughts_token_count
                ),
                cache_read=final_call.cached_content_token_count,
                h2_duplicate=False,
                cache_creation=0,
                model=resolved.model_key,
            )
        )

    digest = SessionDigest(
        session_id=invocation_id,
        domain="adk_invocation",
        resolved=True,
        total_tokens=sum(
            g[-1].prompt_token_count + g[-1].candidates_token_count + g[-1].thoughts_token_count
            for g in groups
        ),
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=0.0,
        output_tokens_available=True,
        task_description="",
        turns=turns,
    )
    return AdaptResult(digest=digest)


def price_digest(digest: SessionDigest, *, prices: dict[str, Any]) -> SessionCost:
    """The single sanctioned call site for tracegauge's compute_session_cost
    in this package -- every caller that needs a priced SessionDigest
    (``evaluator.py``'s per-invocation eval result, ``snapshot.py``'s
    regression-gate snapshots -- Phase 2 W4) must go through this function,
    never call ``compute_session_cost`` directly.

    `prices` is required with no default -- deliberately, not by
    convention. tracegauge's own ``compute_session_cost(digest,
    prices=None, ...)`` silently falls back to its bundled Claude price
    table when `prices` is omitted, and that fallback bug actually happened
    during this package's own development: omitting `prices=` priced a
    $2.80 gemini-2.5-flash call at $18.00 (Claude Sonnet's rate), no error,
    just a buried `approximate` flag. This module's own pre-check
    (``build_session_digest``) only guards against *unresolvable* models --
    it does nothing to stop the wrong price *table* being passed for an
    otherwise-valid model, which is exactly what happened. Routing every
    call through this one function, with `prices` required, converts
    "forgot the argument" from a silent wrong number into a TypeError.
    ``tests/test_pricing_call_site.py`` asserts this is the only place
    ``compute_session_cost`` is called in ``src/``, so a future call site
    added elsewhere can't reintroduce the same bug by skipping this
    wrapper.

    ``prices`` is passed through ``effective_prices`` before reaching
    tracegauge's engine (Phase 3 B2) -- tracegauge's own
    ``compute_turn_cost`` reads ``prices["models"][key]["input_usd_per_mtok"]``
    directly off whatever dict it's given, with zero knowledge of this
    package's ``promo_until``/``standard_rate`` schema fields, so the
    automatic promo-expiry rate switch has to be applied here, on every
    call through this single sanctioned call site, rather than relying on
    every caller to remember to call ``effective_prices`` themselves.
    """
    return compute_session_cost(digest, prices=effective_prices(prices))


def unknown_model_message(model_version: str) -> str:
    if is_local_model(model_version):
        # Phase 3 B1: a bare local-prefix match is no longer sufficient to
        # auto-resolve to zero cost -- distinguish this from a genuinely
        # unrecognized vendor with a specific, actionable remedy naming the
        # exact opt-in mechanism, not the generic "register a custom price"
        # text below (which would be actively misleading here: the model
        # IS recognized, just not priced without an explicit assertion).
        return (
            f"cost not computed: model '{model_version}' carries a "
            "local-model LiteLlm prefix (ollama_chat/, ollama/, or vllm/) "
            "but was NOT priced at zero cost, because that prefix alone "
            "cannot distinguish genuinely local/self-hosted inference from "
            "Ollama Cloud -- a real paid product routed through the "
            "identical prefix, where only the api_base/host differs, and "
            "that field is not available at the point adk-tracegauge "
            "captures usage (confirmed by reading google-adk's "
            "models/lite_llm.py and models/llm_response.py directly -- see "
            "_pricing.py's module docstring). A silently wrong $0.00 for a "
            "paid Ollama Cloud call would be worse than this refusal. If "
            f"'{model_version}' really is local/self-hosted, opt in "
            f"explicitly by setting {ASSUME_LOCAL_ENV_VAR}=1 (asserts every "
            "recognized local prefix) or "
            f"{ASSUME_LOCAL_ENV_VAR}=<comma-separated prefixes> (e.g. "
            "'vllm/' to assert only that one, leaving ollama_chat/ still "
            "failing closed) before running your eval."
        )

    known = ", ".join(known_model_keys())
    return (
        f"cost not computed: model '{model_version}' did not resolve against "
        f"adk-tracegauge's price table (Gemini, Claude, and GPT models "
        f"known: {known}). If this is a local/self-hosted model (Ollama, "
        "vLLM) it needs an explicit opt-in before it resolves to zero cost "
        f"-- see the {ASSUME_LOCAL_ENV_VAR} environment variable; if it's "
        "routed through a cloud platform whose pricing can differ from the "
        "first-party rate (Bedrock, Vertex AI, Azure), that's why it wasn't "
        "auto-resolved -- see _pricing.py's module docstring. Otherwise, "
        f"register a custom price by setting the {PRICE_TABLE_ENV_VAR} "
        "environment variable to the path of a JSON file with the same "
        "schema as the bundled table (src/adk_tracegauge/data/"
        "gemini_prices.json) containing an entry for this model, or open an "
        "issue at https://github.com/gaurav-gandhi-2411/adk-tracegauge/"
        "issues if it should ship built-in."
    )


__all__ = ["AdaptResult", "build_session_digest", "price_digest", "unknown_model_message"]
