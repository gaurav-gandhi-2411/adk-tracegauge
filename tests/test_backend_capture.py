"""Which backend served a grounded call is captured fail-closed (0.10.0, groundwork for the grounding fee).

Verified live on google-adk 2.9.2 with a Gemini API key: ``agent.canonical_model.api_client.vertexai`` is
False for root and sub-agent calls and ``usage_metadata.traffic_type`` is absent (docs/design/grounding-fees.md).
Behaviour on Vertex, on 2.6-2.8 and on `adk eval` is not verified, so anything unreadable is "unknown".
"""

from __future__ import annotations

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore


def _response(
    grounded: bool = True, traffic_type: genai_types.TrafficType | None = None
) -> LlmResponse:
    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100,
        candidates_token_count=10,
        total_token_count=110,
        traffic_type=traffic_type,
    )
    grounding = genai_types.GroundingMetadata(web_search_queries=["q"]) if grounded else None
    return LlmResponse(
        model_version="gemini-2.5-flash", usage_metadata=usage, grounding_metadata=grounding
    )


async def _backend_of(mocker, response: LlmResponse, vertexai: object = False) -> str:
    store = UsageStore()
    ctx = mocker.MagicMock()
    ctx.invocation_id = "inv"
    ctx.session.id = "s"
    ctx.agent_name = "root_agent"
    ctx._invocation_context.agent.canonical_model.api_client.vertexai = vertexai
    await TraceGaugeUsagePlugin(store=store).after_model_callback(
        callback_context=ctx, llm_response=response
    )
    (call,) = store.get("inv")
    return call.backend


@pytest.mark.asyncio
async def test_a_grounded_call_on_the_gemini_api_is_recorded_as_gemini_api(mocker):
    assert await _backend_of(mocker, _response(), vertexai=False) == "gemini_api"


@pytest.mark.asyncio
async def test_a_grounded_call_on_vertex_is_recorded_as_vertex(mocker):
    assert await _backend_of(mocker, _response(), vertexai=True) == "vertex"


@pytest.mark.asyncio
async def test_a_traffic_type_means_vertex_even_when_the_client_says_otherwise(mocker):
    # traffic_type is documented "not supported in Gemini API": a value is a positive Vertex signal.
    response = _response(traffic_type=genai_types.TrafficType.ON_DEMAND)
    assert await _backend_of(mocker, response, vertexai=False) == "vertex"


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", ["unexpected", None, 0])
async def test_anything_but_a_boolean_from_adk_is_unknown_not_a_guess(mocker, unreadable):
    assert await _backend_of(mocker, _response(), vertexai=unreadable) == ""


@pytest.mark.asyncio
async def test_a_failure_to_read_the_client_is_unknown(mocker):
    store = UsageStore()
    ctx = mocker.MagicMock()
    ctx.invocation_id = "inv"
    ctx.session.id = "s"
    ctx.agent_name = "root_agent"
    # ADK internals drifted: reading the agent raises.
    type(ctx)._invocation_context = mocker.PropertyMock(side_effect=AttributeError("moved"))
    await TraceGaugeUsagePlugin(store=store).after_model_callback(
        callback_context=ctx, llm_response=_response()
    )
    (call,) = store.get("inv")
    assert call.backend == ""


@pytest.mark.asyncio
async def test_an_ungrounded_call_does_not_touch_adk_internals(mocker):
    assert await _backend_of(mocker, _response(grounded=False), vertexai=False) == ""
