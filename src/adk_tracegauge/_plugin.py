"""adk_tracegauge/_plugin.py — Captures real per-call token usage during inference.

ADK's Invocation/InvocationEvent objects (what a registered Evaluator
actually receives) are an explicit projection down to author+content --
usage_metadata and model_version are stripped before an evaluator ever sees
them (confirmed by reading google-adk's evaluation_generator.py). The real
data lives on the LlmResponse passed to BasePlugin.after_model_callback,
which ADK's own docstring names as "the ideal place to ... collect metrics
on token usage." This plugin does exactly that, keyed by invocation_id so
CostEfficiencyEvaluator can look it back up.

Requires the agent to run through an App wrapper you build and drive
yourself (see README, "The only path that reliably works") -- a bare
root_agent never fires plugins at all, and AgentEvaluator/adk eval build
their own bare Runner from root_agent regardless of what App you define,
so this plugin only fires inside a hand-rolled Runner, never through ADK's
own eval CLI/API.

Sub-agent delegation via AgentTool: AgentTool.run_async builds a brand-new
Runner (its own session, its own InvocationContext) and, by default
(include_plugins=True), reuses the SAME plugin *instances* from the parent
Runner rather than fresh copies -- so this exact plugin object sees both the
parent's and the delegated sub-agent's before_run_callback/
after_run_callback/after_model_callback calls. before_run_callback/
after_run_callback fire once per Runner.run_async() call, bracketing that
invocation's whole lifetime, so a stack of "currently active invocation_ids"
built from them directly observes real parent/child nesting -- not a guess
from timing or shared-store proximity. A contextvars.ContextVar is required
here rather than a plain instance attribute: concurrent sibling invocations
(parallel eval cases, or a user's own harness awaiting multiple runs
concurrently) get independent Task-local copies of the stack, so they can't
corrupt each other's nesting, while a single nested await chain (the
AgentTool case: the parent awaits the child's entire run before continuing)
correctly shares and restores the same stack across the boundary.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin

from ._store import DEFAULT_USAGE_STORE, CapturedCall, UsageStore

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

_ACTIVE_INVOCATIONS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "adk_tracegauge_active_invocations", default=()
)


class TraceGaugeUsagePlugin(BasePlugin):
    """Captures token usage per invocation for CostEfficiencyEvaluator.

    Add to your own hand-rolled App: ``App(name=..., root_agent=root_agent,
    plugins=[TraceGaugeUsagePlugin()])``. Not honored by AgentEvaluator/adk
    eval -- see README.
    """

    def __init__(self, store: UsageStore | None = None, name: str = "trace_gauge_usage") -> None:
        super().__init__(name=name)
        self._store = store if store is not None else DEFAULT_USAGE_STORE

    async def before_run_callback(self, *, invocation_context: InvocationContext) -> None:
        stack = _ACTIVE_INVOCATIONS.get()
        if stack:
            self._store.record_parent(invocation_context.invocation_id, stack[-1])
        _ACTIVE_INVOCATIONS.set((*stack, invocation_context.invocation_id))

    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        stack = _ACTIVE_INVOCATIONS.get()
        # Ordinary case: this invocation is the top of the stack (correct
        # LIFO nesting for the AgentTool await pattern). Fall back to
        # filtering it out by value if something upstream ever violates
        # strict LIFO -- fail safe (don't leak stack entries) rather than
        # crash on a mismatched pop.
        if stack and stack[-1] == invocation_context.invocation_id:
            _ACTIVE_INVOCATIONS.set(stack[:-1])
        else:
            _ACTIVE_INVOCATIONS.set(
                tuple(i for i in stack if i != invocation_context.invocation_id)
            )

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
                partial=bool(llm_response.partial),
            ),
        )
        return None


__all__ = ["TraceGaugeUsagePlugin"]
