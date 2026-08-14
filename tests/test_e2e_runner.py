"""End-to-end: real google.adk.runners.Runner + App + a fake model + ADK's own
Event -> Invocation conversion -> our plugin -> our evaluator.

This is the test the earlier unit/integration suite didn't have: those
exercised TraceGaugeUsagePlugin and CostEfficiencyEvaluator directly, sharing
a hand-picked "inv-1" string between them. That never actually proved the
plugin's captured invocation_id is the SAME value ADK stamps onto the
Invocation object a real eval run produces -- it just proved our own two
classes agree with each other when fed the same string.

This test does not take that on faith. It runs a real BaseAgent through a
real Runner with TraceGaugeUsagePlugin attached via a real App, collects the
real Event objects the Runner produces, converts them via
adk_tracegauge._compat.convert_events_to_eval_invocations -- this package's
own version-guarded wrapper (Phase 2 W5) around
EvaluationGenerator.convert_events_to_eval_invocations, the exact function
LocalEvalService calls internally, not a reimplementation -- and only then
hands the resulting real Invocation objects to CostEfficiencyEvaluator. If
ADK's invocation_id generation ever diverges between the plugin-visible
CallbackContext and the eval-facing Invocation, this test breaks, which is
the point.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from adk_tracegauge._compat import convert_events_to_eval_invocations
from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator


class _FakeLlm(BaseLlm):
    """Minimal BaseLlm returning one fixed response with real usage_metadata.

    Modeled on google-adk's own tests/unittests/testing_utils.py::MockModel,
    which is test-only infrastructure in the adk-python source repo and not
    importable from an installed google-adk -- reimplemented minimally here
    rather than depended on.
    """

    model: str = "fake-e2e-model"

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["fake-e2e-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            model_version="gemini-2.5-flash",
            content=genai_types.Content(
                parts=[genai_types.Part(text="the answer is 4")], role="model"
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1_000_000,
                candidates_token_count=1_000_000,
                cached_content_token_count=0,
                total_token_count=2_000_000,
            ),
        )


@pytest.mark.asyncio
async def test_real_runner_plugin_capture_correlates_with_real_invocation_id():
    store = UsageStore()

    root_agent = LlmAgent(
        name="test_agent",
        model=_FakeLlm(),
        instruction="Answer the question.",
    )
    app = App(
        name="adk_tracegauge_e2e_test",
        root_agent=root_agent,
        plugins=[TraceGaugeUsagePlugin(store=store)],
    )

    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name=app.name, user_id="test_user")

    events: list[Event] = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=genai_types.Content(parts=[genai_types.Part(text="what is 2+2?")], role="user"),
    ):
        events.append(event)

    # Sanity: the plugin actually fired during this real run, before we even
    # get to the correlation question below.
    captured_ids = store.invocation_ids()
    assert len(captured_ids) == 1, "plugin captured nothing during the real run"
    captured_invocation_id = captured_ids[0]

    # The real conversion path LocalEvalService uses internally -- not a
    # reimplementation of it -- via this package's own version-guarded
    # wrapper (Phase 2 W5, adk_tracegauge._compat).
    invocations = convert_events_to_eval_invocations(events)
    assert len(invocations) == 1
    real_invocation = invocations[0]

    # The actual assertion this test exists for: the invocation_id our
    # plugin saw via CallbackContext during the live model call is the exact
    # same value ADK's own eval conversion assigned to the Invocation object.
    assert captured_invocation_id == real_invocation.invocation_id

    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1_000.0), store=store
    )
    result = evaluator.evaluate_invocations([real_invocation])

    assert result.per_invocation_results[0].score == 2.80
    assert result.per_invocation_results[0].eval_status == EvalStatus.PASSED
