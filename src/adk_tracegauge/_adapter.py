"""adk_tracegauge/_adapter.py — Maps captured ADK usage data onto tracegauge's digest shape.

tracegauge's tes.cost.compute_session_cost operates on a tes._digest.SessionDigest
(a list of TurnDigest, one per AI turn). One ADK invocation can involve more
than one real model call (tool loops, sub-agent delegation), so it maps onto
a SessionDigest with one TurnDigest per real call -- summed cost across the
whole invocation, same as tracegauge sums cost across a whole Claude Code
session.

Two things happen here before any TurnDigest is built, both fail-closed:

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

2. Model resolution happens once per real call. A call whose model_version
   doesn't match the Gemini price table makes the whole adaptation fail
   closed (see AdaptResult.unresolved_model) rather than producing a
   partially-priced or silently-approximate result.
"""

from __future__ import annotations

from dataclasses import dataclass

from tes._digest import SessionDigest, TurnDigest

from ._pricing import known_model_keys, resolve_model
from ._store import CapturedCall


@dataclass
class AdaptResult:
    """A ready-to-price digest, or the specific reason pricing was refused."""

    digest: SessionDigest | None
    unresolved_model: str | None = None
    streaming_anomaly: str | None = None

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
                    "carries a cumulative running total (see README, 'Streaming') "
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
        resolved = resolve_model(final_call.model_version)
        if resolved is None:
            return AdaptResult(digest=None, unresolved_model=final_call.model_version)

        turns.append(
            TurnDigest(
                turn_index=index,
                role="ai",
                tool_names=[],
                content_snippet="",
                token_count_input=final_call.prompt_token_count,
                token_count_output=final_call.candidates_token_count,
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
        total_tokens=sum(g[-1].prompt_token_count + g[-1].candidates_token_count for g in groups),
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=0.0,
        output_tokens_available=True,
        task_description="",
        turns=turns,
    )
    return AdaptResult(digest=digest)


def unknown_model_message(model_version: str) -> str:
    known = ", ".join(known_model_keys())
    return (
        f"cost not computed: model '{model_version}' is not in the "
        f"adk-tracegauge Gemini price table (known: {known}). "
        "Check https://ai.google.dev/gemini-api/docs/pricing for a new or "
        "renamed model, or open an issue if this model should be added."
    )


__all__ = ["AdaptResult", "build_session_digest", "unknown_model_message"]
