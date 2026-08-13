"""adk_tracegauge/_plugin.py — Captures real per-call token usage during inference.

ADK's Invocation/InvocationEvent objects (what a registered Evaluator
actually receives) are an explicit projection down to author+content --
usage_metadata and model_version are stripped before an evaluator ever sees
them (confirmed by reading google-adk's evaluation_generator.py). The real
data lives on the LlmResponse passed to BasePlugin.after_model_callback,
which ADK's own docstring names as "the ideal place to ... collect metrics
on token usage." This plugin does exactly that, keyed by invocation_id so
CostEfficiencyEvaluator can look it back up.

Requires the agent to run through an App wrapper (see README) --
LocalEvalService's bare-agent path does not honor plugins at all, so this
plugin (and therefore this whole evaluator) reports nothing without it.
"""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin

from ._store import DEFAULT_USAGE_STORE, CapturedCall, UsageStore


class TraceGaugeUsagePlugin(BasePlugin):
    """Captures token usage per invocation for CostEfficiencyEvaluator.

    Add to your agent's App: ``App(agent=root_agent, plugins=[TraceGaugeUsagePlugin()])``.
    """

    def __init__(self, store: UsageStore | None = None, name: str = "trace_gauge_usage") -> None:
        super().__init__(name=name)
        self._store = store if store is not None else DEFAULT_USAGE_STORE

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        usage = llm_response.usage_metadata
        if usage is None:
            # A model turn that produced no usage_metadata (e.g. an error
            # response) contributes nothing measurable -- skip rather than
            # record zeros, which would understate real cost.
            return None

        self._store.record(
            callback_context.invocation_id,
            CapturedCall(
                model_version=llm_response.model_version or "",
                prompt_token_count=usage.prompt_token_count or 0,
                candidates_token_count=usage.candidates_token_count or 0,
                cached_content_token_count=usage.cached_content_token_count or 0,
                total_token_count=usage.total_token_count or 0,
            ),
        )
        return None


__all__ = ["TraceGaugeUsagePlugin"]
