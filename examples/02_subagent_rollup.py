"""examples/02_subagent_rollup.py — sub-agent delegation cost rollup.

WHAT THIS DOES
    Builds a real two-agent ADK app: a root agent that delegates one
    question to a sub-agent via `AgentTool` (agent-as-a-tool delegation),
    with `TraceGaugeUsagePlugin` wired into the App's plugin list (the
    hand-rolled Runner harness pattern -- README, "The only path that
    reliably works" -- needed here specifically because AgentTool rollup
    depends on `before_run_callback`/`after_run_callback`, which only fire
    through a real Runner's PluginManager; `after_model_callback` alone,
    example 01's workaround, does NOT get you rollup -- see README,
    "Sub-agent delegation").

    Runs it through a REAL `InMemoryRunner`, not a mock -- both agents are
    tiny fake `BaseLlm`s (see example 01 for why: zero-cost, deterministic,
    no API key needed) returning fixed token counts on different models
    (`gemini-2.5-pro` for the root, `gemini-2.5-flash-lite` for the
    sub-agent), so the combined dollar figure below is a real, reproducible
    number, not a guess.

    Then calls `CostEfficiencyEvaluator.evaluate_invocations()` directly
    against the root invocation and prints the REAL rolled-up total: the
    root's own 2 model calls (the initial tool-call turn + the final
    response turn) PLUS the delegated sub-agent's 1 model call, summed --
    not just the root's own share.

HOW TO RUN
    uv run python examples/02_subagent_rollup.py

EXPECTED OUTPUT (real numbers, computed from this script's own fixed token
counts against adk-tracegauge's bundled price table -- reproduces exactly
on every run)
    captured invocation_ids: 2 (one root, one delegated sub-agent)
    root's own calls: 2
    root + descendants calls: 3
    rolled-up score: $0.565000
    eval_status: EvalStatus.PASSED
      call[0] model=gemini-2.5-pro       total=$0.225000  (root, tool-call turn)
      call[1] model=gemini-2.5-pro       total=$0.300000  (root, final response)
      call[2] model=gemini-2.5-flash-lite total=$0.040000  (delegated sub-agent)
"""

from __future__ import annotations

import asyncio

from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import InMemoryRunner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types as genai_types

import adk_tracegauge  # noqa: F401 -- registers the metric as an import side effect
from adk_tracegauge import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator, CostThresholdCriterion


class _SubAgentLlm(BaseLlm):
    """The delegated sub-agent's fake model -- fixed 200k prompt / 50k output
    tokens on gemini-2.5-flash-lite ($0.04)."""

    model: str = "sub-fake-model"

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["sub-fake-model"]

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield genai_response(
            model_version="gemini-2.5-flash-lite",
            text="Paris",
            prompt_tokens=200_000,
            output_tokens=50_000,
        )


class _RootAgentLlm(BaseLlm):
    """The root agent's fake model -- makes one tool call (delegating to the
    sub-agent), then one final response, both on gemini-2.5-pro
    ($0.225 + $0.30 = $0.525 for the root's own two turns)."""

    model: str = "root-fake-model"
    _call_count: int = 0

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["root-fake-model"]

    async def generate_content_async(self, llm_request, stream: bool = False):
        self._call_count += 1
        if self._call_count == 1:
            from google.adk.models.llm_response import LlmResponse

            yield LlmResponse(
                model_version="gemini-2.5-pro",
                content=genai_types.Content(
                    parts=[
                        genai_types.Part(
                            function_call=genai_types.FunctionCall(
                                id="call_1", name="capital_finder", args={"request": "France"}
                            )
                        )
                    ],
                    role="model",
                ),
                usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=100_000,
                    candidates_token_count=10_000,
                    cached_content_token_count=0,
                    total_token_count=110_000,
                ),
            )
        else:
            yield genai_response(
                model_version="gemini-2.5-pro",
                text="The capital of France is Paris.",
                prompt_tokens=120_000,
                output_tokens=15_000,
            )


def genai_response(*, model_version: str, text: str, prompt_tokens: int, output_tokens: int):
    from google.adk.models.llm_response import LlmResponse

    return LlmResponse(
        model_version=model_version,
        content=genai_types.Content(parts=[genai_types.Part(text=text)], role="model"),
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=0,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


async def main() -> None:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)

    capital_finder = LlmAgent(
        name="capital_finder",
        model=_SubAgentLlm(),
        instruction="Answer with just the capital city name.",
    )
    root_agent = LlmAgent(
        name="root_agent",
        model=_RootAgentLlm(),
        instruction="Delegate capital-city questions to capital_finder.",
        # include_plugins=True (the default) is what makes rollup possible:
        # AgentTool.run_async reuses the SAME plugin instances from the
        # parent Runner, so before_run_callback/after_run_callback observe
        # the real nesting -- see _plugin.py's module docstring.
        tools=[AgentTool(agent=capital_finder)],
    )

    app = App(name="subagent_rollup_demo", root_agent=root_agent, plugins=[plugin])
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name=app.name, user_id="demo_user")

    async for _event in runner.run_async(
        user_id="demo_user",
        session_id=session.id,
        new_message=genai_types.Content(
            parts=[genai_types.Part(text="What is the capital of France?")], role="user"
        ),
    ):
        pass

    invocation_ids = store.invocation_ids()
    root_id = invocation_ids[0]
    print(f"captured invocation_ids: {len(invocation_ids)} (one root, one delegated sub-agent)")
    print(f"root's own calls: {len(store.get(root_id))}")
    print(f"root + descendants calls: {len(store.get_with_descendants(root_id))}")

    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(
            metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=1.00)
        ),
        store=store,
    )
    result = evaluator.evaluate_invocations(
        [Invocation(invocation_id=root_id, user_content=genai_types.Content(parts=[]))]
    )
    pir = result.per_invocation_results[0]
    print(f"rolled-up score: ${pir.score:.6f}")
    print(f"eval_status: {pir.eval_status}")
    print(pir.rubric_scores[0].rationale)


if __name__ == "__main__":
    asyncio.run(main())
