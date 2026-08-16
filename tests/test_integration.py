"""End-to-end: plugin captures usage -> evaluator reads it back -> priced result.

No mocking of the store hand-off itself -- this is the actual thing the
two-extension-point design depends on working.
"""

from __future__ import annotations

import pytest
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator


def _invocation(invocation_id: str) -> Invocation:
    return Invocation(
        invocation_id=invocation_id,
        user_content=genai_types.Content(parts=[genai_types.Part(text="hi")], role="user"),
    )


def _fake_callback_context(
    invocation_id: str, session_id: str = "fake-session", agent_name: str = "fake-agent"
) -> object:
    """A minimal fake CallbackContext -- real ADK CallbackContext always has
    a `.session` (a required, non-optional property backed by
    InvocationContext.session: Session), so after_model_callback's Phase 4
    R2 session_id capture (`callback_context.session.id`) needs one here
    too, same as it would against a real ADK-built CallbackContext.
    `.agent_name` (LL2) is the same kind of always-present real property --
    see _plugin.py's after_model_callback -- so this fake needs one too."""
    fake_session = type("FakeSession", (), {"id": session_id})()
    return type(
        "Ctx",
        (),
        {"invocation_id": invocation_id, "session": fake_session, "agent_name": agent_name},
    )()


@pytest.mark.asyncio
async def test_plugin_capture_flows_through_to_evaluator_score():
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0), store=store
    )

    callback_context = _fake_callback_context("inv-1")
    llm_response = LlmResponse(
        model_version="gemini-2.5-flash-001",
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
            cached_content_token_count=0,
            total_token_count=2_000_000,
        ),
    )

    await plugin.after_model_callback(callback_context=callback_context, llm_response=llm_response)
    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    assert result.per_invocation_results[0].score == 2.80


@pytest.mark.asyncio
async def test_two_calls_in_one_invocation_sum_correctly_end_to_end():
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0), store=store
    )
    callback_context = _fake_callback_context("inv-1")

    for _ in range(2):
        await plugin.after_model_callback(
            callback_context=callback_context,
            llm_response=LlmResponse(
                model_version="gemini-2.5-flash",
                usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=1_000_000,
                    candidates_token_count=0,
                    cached_content_token_count=0,
                    total_token_count=1_000_000,
                ),
            ),
        )

    result = evaluator.evaluate_invocations([_invocation("inv-1")])

    # Two tool-loop calls within one invocation: 2 x $0.30 = $0.60.
    assert result.per_invocation_results[0].score == 0.60
