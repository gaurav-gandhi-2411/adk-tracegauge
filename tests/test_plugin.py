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
    thoughts: int = 0,
    tool_use: int = 0,
    with_usage: bool = True,
) -> LlmResponse:
    usage = None
    if with_usage:
        usage = genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            candidates_token_count=output,
            cached_content_token_count=cached,
            total_token_count=prompt + output,
            thoughts_token_count=thoughts,
            tool_use_prompt_token_count=tool_use,
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
async def test_after_model_callback_captures_thoughts_and_tool_use_prompt_tokens(mocker):
    # Phase 2 W1 P0 fix: these two usage_metadata fields were previously
    # dropped entirely rather than captured -- see CapturedCall's docstring.
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    await plugin.after_model_callback(
        callback_context=callback_context,
        llm_response=_llm_response(thoughts=42, tool_use=7),
    )

    captured = store.get("inv-1")[0]
    assert captured.thoughts_token_count == 42
    assert captured.tool_use_prompt_token_count == 7


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


@pytest.mark.asyncio
async def test_partial_flag_on_llm_response_flows_into_captured_call(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    response = _llm_response()
    response.partial = True
    await plugin.after_model_callback(callback_context=callback_context, llm_response=response)

    assert store.get("inv-1")[0].partial is True


@pytest.mark.asyncio
async def test_after_model_callback_without_partial_set_defaults_to_false(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"

    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response()
    )

    assert store.get("inv-1")[0].partial is False


@pytest.mark.asyncio
async def test_before_run_callback_at_top_level_records_no_parent(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id = "top-level"

    await plugin.before_run_callback(invocation_context=ctx)
    await plugin.after_run_callback(invocation_context=ctx)

    assert store.get_with_descendants("top-level") == []  # no calls recorded, but no crash
    assert store._parents == {}


@pytest.mark.asyncio
async def test_nested_before_run_callback_records_parent_relationship(mocker):
    """Mirrors AgentTool.run_async: the parent's Runner.run_async is awaiting a
    tool call that itself awaits a child Runner.run_async on the SAME plugin
    instance, entirely within one asyncio task -- no create_task/gather."""
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    parent_ctx = mocker.MagicMock()
    parent_ctx.invocation_id = "parent"
    child_ctx = mocker.MagicMock()
    child_ctx.invocation_id = "child"

    await plugin.before_run_callback(invocation_context=parent_ctx)
    await plugin.before_run_callback(invocation_context=child_ctx)
    await plugin.after_run_callback(invocation_context=child_ctx)
    await plugin.after_run_callback(invocation_context=parent_ctx)

    assert store._parents == {"child": "parent"}


@pytest.mark.asyncio
async def test_after_run_callback_restores_stack_for_calls_following_a_nested_child(mocker):
    """A model call made by the parent AFTER the nested child's run completes
    must be attributed to the parent, not still nested under the child."""
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    parent_ctx = mocker.MagicMock()
    parent_ctx.invocation_id = "parent"
    child_ctx = mocker.MagicMock()
    child_ctx.invocation_id = "child"
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "parent"

    await plugin.before_run_callback(invocation_context=parent_ctx)
    await plugin.before_run_callback(invocation_context=child_ctx)
    await plugin.after_run_callback(invocation_context=child_ctx)
    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response()
    )
    await plugin.after_run_callback(invocation_context=parent_ctx)

    assert store.get("parent") != []
    assert "parent" not in store._parents  # never recorded as anyone's child


@pytest.mark.asyncio
async def test_before_run_callback_records_the_invocation_context_session_id(mocker):
    # Phase 3 B4: TraceGaugeUsagePlugin.before_run_callback also records
    # invocation_context.session.id, the pairing key `tracegauge check
    # --mode paired` uses -- see UsageStore.record_session's docstring.
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    ctx = mocker.MagicMock()
    ctx.invocation_id = "inv-1"
    ctx.session.id = "case-42"

    await plugin.before_run_callback(invocation_context=ctx)

    assert store.session_id("inv-1") == "case-42"


@pytest.mark.asyncio
async def test_before_run_callback_records_distinct_session_ids_per_invocation(mocker):
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    parent_ctx = mocker.MagicMock()
    parent_ctx.invocation_id = "parent"
    parent_ctx.session.id = "case-parent"
    child_ctx = mocker.MagicMock()
    child_ctx.invocation_id = "child"
    child_ctx.session.id = "case-child"

    await plugin.before_run_callback(invocation_context=parent_ctx)
    await plugin.before_run_callback(invocation_context=child_ctx)

    assert store.session_id("parent") == "case-parent"
    assert store.session_id("child") == "case-child"


# --- after_model_callback also captures session_id (Phase 4 R2) ----------


@pytest.mark.asyncio
async def test_after_model_callback_records_session_id(mocker):
    # Phase 4 R2: before_run_callback never fires during `adk eval`/
    # AgentEvaluator.evaluate() at all (they build a bare Runner, no
    # App/Plugin wiring) -- after_model_callback is the hook proven to fire
    # through that path (the quickstart's direct-binding mechanism), so
    # session_id capture must also happen here, not only in
    # before_run_callback.
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"
    callback_context.session.id = "case-42"

    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response()
    )

    assert store.session_id("inv-1") == "case-42"


@pytest.mark.asyncio
async def test_after_model_callback_records_session_id_even_when_usage_metadata_is_none(mocker):
    # session_id capture must not be gated behind the usage_metadata
    # early-return -- an error response still ran inside a real session.
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"
    callback_context.session.id = "case-42"

    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response(with_usage=False)
    )

    assert store.session_id("inv-1") == "case-42"


@pytest.mark.asyncio
async def test_after_model_callback_session_id_survives_when_before_run_callback_never_fired(
    mocker,
):
    # The exact `adk eval` scenario: no before_run_callback call at all
    # (bare Runner, no App/Plugin wiring) -- only after_model_callback, via
    # direct binding. Must still populate session_id.
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    callback_context = mocker.MagicMock()
    callback_context.invocation_id = "inv-1"
    callback_context.session.id = "___eval___session___abc"

    await plugin.after_model_callback(
        callback_context=callback_context, llm_response=_llm_response()
    )

    assert store.session_id("inv-1") == "___eval___session___abc"
    assert store._parents == {}  # before_run_callback's parent-tracking never ran, as expected


@pytest.mark.asyncio
async def test_after_run_callback_falls_back_to_filtering_on_non_lifo_mismatch(mocker):
    # Defensive path: if after_run_callback ever fires out of strict LIFO
    # order (shouldn't happen via ADK's own await-nested AgentTool pattern,
    # but this must not crash or leak the wrong entries if it somehow does).
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    outer_ctx = mocker.MagicMock()
    outer_ctx.invocation_id = "outer"
    inner_ctx = mocker.MagicMock()
    inner_ctx.invocation_id = "inner"

    await plugin.before_run_callback(invocation_context=outer_ctx)
    await plugin.before_run_callback(invocation_context=inner_ctx)
    # Pop "outer" first, out of order, instead of "inner" (the true top).
    await plugin.after_run_callback(invocation_context=outer_ctx)

    from adk_tracegauge._plugin import _ACTIVE_INVOCATIONS

    assert _ACTIVE_INVOCATIONS.get() == ("inner",)
