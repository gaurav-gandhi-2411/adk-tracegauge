"""adk_tracegauge/_adapter.py — Maps captured ADK usage data onto tracegauge's digest shape.

tracegauge's tes.cost.compute_session_cost operates on a tes._digest.SessionDigest
(a list of TurnDigest, one per AI turn). One ADK invocation can involve more
than one real model call, so it maps onto a SessionDigest with one TurnDigest
per captured call -- summed cost across the whole invocation, same as
tracegauge sums cost across a whole Claude Code session.

Model resolution happens here, once, before any TurnDigest is built. A call
whose model_version doesn't match the Gemini price table makes the whole
adaptation fail closed (see AdaptResult.unresolved_model) rather than
producing a partially-priced or silently-approximate result.
"""

from __future__ import annotations

from dataclasses import dataclass

from tes._digest import SessionDigest, TurnDigest

from ._pricing import known_model_keys, resolve_model
from ._store import CapturedCall


@dataclass
class AdaptResult:
    """Either a ready-to-price digest, or the model that blocked pricing."""

    digest: SessionDigest | None
    unresolved_model: str | None

    @property
    def ok(self) -> bool:
        return self.digest is not None


def build_session_digest(invocation_id: str, calls: list[CapturedCall]) -> AdaptResult:
    """Builds a SessionDigest from captured calls, or reports the blocking model.

    Every call's model_version must resolve against the Gemini price table.
    On the first unresolved model, adaptation stops and returns it -- no
    partial digest, no fallback pricing.
    """
    turns: list[TurnDigest] = []

    for index, call in enumerate(calls):
        resolved = resolve_model(call.model_version)
        if resolved is None:
            return AdaptResult(digest=None, unresolved_model=call.model_version)

        turns.append(
            TurnDigest(
                turn_index=index,
                role="ai",
                tool_names=[],
                content_snippet="",
                token_count_input=call.prompt_token_count,
                token_count_output=call.candidates_token_count,
                cache_read=call.cached_content_token_count,
                h2_duplicate=False,
                cache_creation=0,
                model=resolved.model_key,
            )
        )

    digest = SessionDigest(
        session_id=invocation_id,
        domain="adk_invocation",
        resolved=True,
        total_tokens=sum(c.prompt_token_count + c.candidates_token_count for c in calls),
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=0.0,
        output_tokens_available=True,
        task_description="",
        turns=turns,
    )
    return AdaptResult(digest=digest, unresolved_model=None)


def unknown_model_message(model_version: str) -> str:
    known = ", ".join(known_model_keys())
    return (
        f"cost not computed: model '{model_version}' is not in the "
        f"adk-tracegauge Gemini price table (known: {known}). "
        "Check https://ai.google.dev/gemini-api/docs/pricing for a new or "
        "renamed model, or open an issue if this model should be added."
    )


__all__ = ["AdaptResult", "build_session_digest", "unknown_model_message"]
