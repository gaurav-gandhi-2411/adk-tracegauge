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
    """

    model_version: str
    prompt_token_count: int
    candidates_token_count: int
    cached_content_token_count: int
    total_token_count: int
    partial: bool = False


@dataclass
class UsageStore:
    """Accumulates CapturedCall records per invocation_id.

    A single ADK invocation can involve more than one real model call (tool
    loops, sub-agent delegation) before producing a final_response, so each
    invocation_id maps to an ordered list of calls, not a single one.
    """

    _calls: dict[str, list[CapturedCall]] = field(default_factory=dict)
    _parents: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, invocation_id: str, call: CapturedCall) -> None:
        with self._lock:
            self._calls.setdefault(invocation_id, []).append(call)

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


DEFAULT_USAGE_STORE = UsageStore()
"""Process-wide default store. Used by TraceGaugeUsagePlugin and
CostEfficiencyEvaluator unless an explicit store is passed to both."""


__all__ = ["CapturedCall", "UsageStore", "DEFAULT_USAGE_STORE"]
