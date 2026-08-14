"""adk_tracegauge/_store.py — Shared usage store bridging the plugin and the evaluator.

ADK's MetricEvaluatorRegistry only ever instantiates a registered evaluator
as ``EvaluatorClass(eval_metric=eval_metric)`` (see metric_evaluator_registry.py
in google-adk) -- there is no channel to hand it a custom object at
construction time. The plugin (which captures real usage_metadata during
inference, the only place ADK exposes it) and the evaluator (which reads it
back per invocation_id) therefore share a module-level singleton by default,
mirroring the same pattern google-adk itself uses for
DEFAULT_METRIC_EVALUATOR_REGISTRY. Both accept an explicit ``store=`` override
so tests can use an isolated instance instead of the shared singleton.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapturedCall:
    """One real model call's usage data, captured via after_model_callback.

    ``partial`` mirrors ``LlmResponse.partial`` at capture time: True for an
    intermediate SSE streaming chunk, False for a call's final response
    (streamed or not). This is ADK's own chunk-boundary signal, not inferred
    -- see _adapter.py's grouping logic, which relies on it to avoid pricing
    each streamed chunk of one real call as if it were a separate call.

    ``thoughts_token_count`` and ``tool_use_prompt_token_count`` are two more
    fields on Gemini's ``GenerateContentResponseUsageMetadata`` beyond the
    original four this dataclass started with (Phase 1 only captured
    prompt/candidates/cached/total -- Phase 2 W1 P0 audit found both were
    silently dropped, which undercounts real dollar cost):

    - ``thoughts_token_count``: "thinking"/reasoning tokens. Billed as
      output tokens per Gemini's own pricing pages (output price is
      documented as "including thinking tokens") -- see _adapter.py, which
      folds this into token_count_output alongside candidates_token_count.
    - ``tool_use_prompt_token_count``: tokens from Gemini's server-side
      built-in tools (e.g. Google Search grounding, code execution) fed back
      to the model within the same call. adk-tracegauge could not find an
      authoritative source for this category's exact billing rate/tier, so
      rather than guess, _adapter.py refuses to price any call where this is
      nonzero (fail-closed, same philosophy as an unresolved model) --
      see AdaptResult.unpriced_component.
    """

    model_version: str
    prompt_token_count: int
    candidates_token_count: int
    cached_content_token_count: int
    total_token_count: int
    partial: bool = False
    thoughts_token_count: int = 0
    tool_use_prompt_token_count: int = 0


@dataclass
class UsageStore:
    """Accumulates CapturedCall records per invocation_id.

    A single ADK invocation can involve more than one real model call (tool
    loops, sub-agent delegation) before producing a final_response, so each
    invocation_id maps to an ordered list of calls, not a single one.
    """

    _calls: dict[str, list[CapturedCall]] = field(default_factory=dict)
    _parents: dict[str, str] = field(default_factory=dict)
    _session_ids: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, invocation_id: str, call: CapturedCall) -> None:
        with self._lock:
            self._calls.setdefault(invocation_id, []).append(call)

    def record_session(self, invocation_id: str, session_id: str) -> None:
        """Records the ADK ``session.id`` a given invocation ran under --
        captured by ``TraceGaugeUsagePlugin.before_run_callback`` from
        ``invocation_context.session.id`` (Phase 3 B4, see snapshot.py's
        module docstring for why this exists: it's the one caller-controlled,
        stable-across-runs identifier available to a hand-rolled eval
        harness, and therefore the pairing key ``tracegauge check --mode
        paired`` uses -- unlike ``invocation_id``, which google-adk always
        regenerates fresh and random on every run, confirmed by reading
        ``evaluation_generator.py``'s ``Event.new_id()`` and
        ``runners.py``'s ``new_invocation_context_id()``).
        """
        with self._lock:
            self._session_ids[invocation_id] = session_id

    def session_id(self, invocation_id: str) -> str | None:
        """The session_id recorded for invocation_id via record_session, or
        None if none was ever recorded (e.g. a store populated directly in a
        test, or an ADK version whose InvocationContext this plugin could
        not read a session from)."""
        with self._lock:
            return self._session_ids.get(invocation_id)

    def record_parent(self, invocation_id: str, parent_invocation_id: str) -> None:
        """Records that invocation_id was spawned during parent_invocation_id's
        own run -- e.g. an AgentTool-delegated sub-agent, which gets its own
        fresh invocation_id from a Runner ADK builds internally. Observed
        directly by TraceGaugeUsagePlugin's before_run_callback/
        after_run_callback nesting (see _plugin.py), not guessed from timing.
        """
        with self._lock:
            self._parents[invocation_id] = parent_invocation_id

    def get(self, invocation_id: str) -> list[CapturedCall]:
        with self._lock:
            return list(self._calls.get(invocation_id, []))

    def get_with_descendants(self, invocation_id: str) -> list[CapturedCall]:
        """All calls captured for invocation_id, plus every call captured for
        any invocation recorded (via record_parent) as a descendant of it,
        recursively. Covers nested delegation (a sub-agent that itself
        delegates further), not just one level.
        """
        with self._lock:
            children_by_parent: dict[str, list[str]] = {}
            for child, parent in self._parents.items():
                children_by_parent.setdefault(parent, []).append(child)

            calls: list[CapturedCall] = list(self._calls.get(invocation_id, []))
            seen = {invocation_id}
            frontier = [invocation_id]
            while frontier:
                current = frontier.pop()
                for child in children_by_parent.get(current, []):
                    if child in seen:
                        continue
                    seen.add(child)
                    calls.extend(self._calls.get(child, []))
                    frontier.append(child)
            return calls

    def invocation_ids(self) -> list[str]:
        """Every invocation_id with at least one captured call, in record order."""
        with self._lock:
            return list(self._calls.keys())

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()
            self._parents.clear()
            self._session_ids.clear()


DEFAULT_USAGE_STORE = UsageStore()
"""Process-wide default store. Used by TraceGaugeUsagePlugin and
CostEfficiencyEvaluator unless an explicit store is passed to both."""


__all__ = ["CapturedCall", "UsageStore", "DEFAULT_USAGE_STORE"]
