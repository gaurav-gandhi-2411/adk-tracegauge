from __future__ import annotations

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore


def _llm_response(
    model_version: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    cached: int = 0,
    with_usage: bool = True,
) -> LlmResponse:
    usage = None
    if with_usage:
        usage = genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            candidates_token_count=output,
            cached_content_token_count=cached,
            total_token_count=prompt + output,
        )
    return LlmResponse(model_version=model_version, usage_metadata=usage)


@pytest.mark.asyncio
async def test_after_model_callback_records_usage_by_invocation_id(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    result = await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response()
    )

    assert result is None  # never replaces the response
    calls = store.get("inv-1")
    assert len(calls) == 1
    assert calls[0].model_version == "gemini-2.5-flash"
    assert calls[0].prompt_token_count == 1000
    assert calls[0].candidates_token_count == 200


@pytest.mark.asyncio
async def test_after_model_callback_skips_response_with_no_usage_metadata(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    await plugin.after_model_callback(
        callback_context=callback_context,
        llm_response=_llm_response(with_usage=False),
    )

    assert store.get("inv-1") == []


@pytest.mark.asyncio
async def test_multiple_calls_within_one_invocation_all_recorded(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response(prompt=100)
    )
    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response(prompt=200)
    )

    calls = store.get("inv-1")
    assert len(calls) == 2
    assert [c.prompt_token_count for c in calls] == [100, 200]


def test_defaults_to_the_shared_singleton_store_when_none_given():
    from adk_tracegauge._store import DEFAULT_USAGE_STORE

    plugin = TraceGaugeUsagePlugin()
    assert plugin._store is DEFAULT_USAGE_STORE
