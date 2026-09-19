"""Registering ``TraceGaugeUsagePlugin`` twice (both ``after_model_callback=`` on the agent AND
``plugins=[...]`` on the runner) records every call twice -- every cost exactly 2x wrong. The shipped
quickstart did this and a test pinned the doubled figure, so the suite confirmed the error. The
plugin now refuses (raises ``DoubleRegistrationError``) on the second delivery of the same model
response. Refusing rather than warning: a cost tool's whole value is correct numbers, and a
warning scrolls past while every figure stays wrong.

The detector is real-ADK-driven here (an ``InMemoryRunner`` with a deterministic fake model), not
mocked -- it depends on ADK delivering the identical ``LlmResponse`` object down both paths.
"""

from __future__ import annotations

import asyncio
import gc

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from adk_tracegauge import DoubleRegistrationError, TraceGaugeUsagePlugin
from adk_tracegauge._cli import _resolve_entrypoint
from adk_tracegauge._store import UsageStore
from adk_tracegauge.snapshot import build_snapshot

# 12,000 in x $0.30/M = $0.003600; 800 out x $2.50/M = $0.002000 -> $0.005600 for ONE call.
_ONE_CALL_USD = 0.0056


def _usage(prompt: int, out: int) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=out, total_token_count=prompt + out
    )


class _FakeLlm(BaseLlm):
    model: str = "fake"
    streaming_chunks: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        content = types.Content(parts=[types.Part(text="hi")], role="model")
        if self.streaming_chunks:
            yield LlmResponse(
                model_version="gemini-2.5-flash",
                partial=True,
                content=content,
                usage_metadata=_usage(12000, 400),
            )
        yield LlmResponse(
            model_version="gemini-2.5-flash", content=content, usage_metadata=_usage(12000, 800)
        )


def _run(
    *,
    store: UsageStore,
    callback_plugin: TraceGaugeUsagePlugin | None = None,
    runner_plugins: list[TraceGaugeUsagePlugin] | None = None,
    streaming: bool = False,
) -> None:
    agent = LlmAgent(
        name="a",
        model=_FakeLlm(streaming_chunks=streaming),
        instruction="x",
        after_model_callback=callback_plugin.after_model_callback if callback_plugin else None,  # type: ignore[arg-type]
    )
    runner = InMemoryRunner(agent=agent, app_name="a", plugins=runner_plugins or [])

    async def main() -> None:
        session = await runner.session_service.create_session(app_name="a", user_id="u")
        run_config = RunConfig(streaming_mode=StreamingMode.SSE) if streaming else RunConfig()
        async for _ in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(parts=[types.Part(text="q")], role="user"),
            run_config=run_config,
        ):
            pass

    asyncio.run(main())


def test_plugins_only_wiring_counts_each_call_once():
    store = UsageStore()

    _run(store=store, runner_plugins=[TraceGaugeUsagePlugin(store=store)])

    (record,) = build_snapshot(store).records
    assert record.call_count == 1
    assert record.cost_usd == pytest.approx(_ONE_CALL_USD)


def test_after_model_callback_only_wiring_counts_each_call_once():
    store = UsageStore()

    _run(store=store, callback_plugin=TraceGaugeUsagePlugin(store=store))

    (record,) = build_snapshot(store).records
    assert record.call_count == 1
    assert record.cost_usd == pytest.approx(_ONE_CALL_USD)


def test_both_wirings_at_once_is_refused_with_an_actionable_message():
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)

    with pytest.raises(DoubleRegistrationError) as exc_info:
        _run(store=store, callback_plugin=plugin, runner_plugins=[plugin])

    message = str(exc_info.value)
    assert "registered on this agent more than once" in message
    assert "after_model_callback=" in message and "plugins=[...]" in message
    assert "exactly 2x" in message
    assert "not both" in message
    # The run stopped; nothing was silently double-counted on the way out. (The first delivery's
    # single record is fine; a second copy of it must never exist.)
    for records in (build_snapshot(store).records,):
        assert all(r.call_count == 1 for r in records)


def test_both_wirings_is_refused_under_streaming_too():
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)

    with pytest.raises(DoubleRegistrationError):
        _run(store=store, callback_plugin=plugin, runner_plugins=[plugin], streaming=True)


def test_two_plugin_instances_sharing_one_store_is_refused():
    store = UsageStore()

    # Both instances are runner-level plugins here, and ADK's PluginManager re-raises a plugin
    # callback's exception as a plain RuntimeError("Error in plugin 'two' ...") chained from the
    # original -- so the type seen is RuntimeError (DoubleRegistrationError's base), the message
    # is intact, and the original is `__cause__`. (The agent-callback path raises it unwrapped.)
    with pytest.raises(RuntimeError, match="received the same model response twice") as exc_info:
        _run(
            store=store,
            runner_plugins=[
                TraceGaugeUsagePlugin(store=store, name="one"),
                TraceGaugeUsagePlugin(store=store, name="two"),
            ],
        )

    assert isinstance(exc_info.value, DoubleRegistrationError) or isinstance(
        exc_info.value.__cause__, DoubleRegistrationError
    )


def test_two_plugin_instances_with_separate_stores_each_count_once():
    # Not a misconfiguration: each store legitimately sees each response exactly once.
    store_a, store_b = UsageStore(), UsageStore()

    _run(
        store=store_a,
        runner_plugins=[
            TraceGaugeUsagePlugin(store=store_a, name="a"),
            TraceGaugeUsagePlugin(store=store_b, name="b"),
        ],
    )

    for store in (store_a, store_b):
        (record,) = build_snapshot(store).records
        assert record.call_count == 1


def test_the_same_plugin_across_many_runs_is_never_a_false_positive():
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)

    for _ in range(5):
        _run(store=store, callback_plugin=plugin)

    assert len(build_snapshot(store).records) == 5
    assert all(r.call_count == 1 for r in build_snapshot(store).records)


def test_cli_entrypoint_that_double_registers_gets_one_line_not_a_traceback():
    with pytest.raises(SystemExit) as exc_info:
        _resolve_entrypoint("test_plugin_double_registration:_entrypoint_wiring_both")

    message = exc_info.value.code
    assert isinstance(message, str) and "\n" not in message
    assert message.startswith(
        "--entrypoint test_plugin_double_registration:_entrypoint_wiring_both"
    )
    assert "Register it exactly once" in message


def _entrypoint_wiring_both() -> UsageStore:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    _run(store=store, callback_plugin=plugin, runner_plugins=[plugin])
    return store


def _entrypoint_two_plugin_instances_one_store() -> UsageStore:
    # The ADK-wrapped form (PluginManager re-raises as RuntimeError chained from ours).
    store = UsageStore()
    _run(
        store=store,
        runner_plugins=[
            TraceGaugeUsagePlugin(store=store, name="one"),
            TraceGaugeUsagePlugin(store=store, name="two"),
        ],
    )
    return store


def _entrypoint_unrelated_runtime_error() -> UsageStore:
    raise RuntimeError("something else entirely")


def test_cli_entrypoint_with_adk_wrapped_double_registration_also_gets_one_line():
    with pytest.raises(SystemExit) as exc_info:
        _resolve_entrypoint(
            "test_plugin_double_registration:_entrypoint_two_plugin_instances_one_store"
        )

    message = exc_info.value.code
    assert isinstance(message, str) and "\n" not in message
    assert "Register it exactly once" in message


def test_cli_entrypoint_unrelated_runtime_errors_are_not_swallowed():
    with pytest.raises(RuntimeError, match="something else entirely"):
        _resolve_entrypoint("test_plugin_double_registration:_entrypoint_unrelated_runtime_error")


class _Weakrefable:
    pass


def test_claim_delivery_is_true_once_per_object_and_false_for_the_identical_object():
    store = UsageStore()
    a, b = _Weakrefable(), _Weakrefable()

    assert store.claim_delivery(a) is True
    assert store.claim_delivery(a) is False
    assert store.claim_delivery(b) is True


def test_claim_delivery_does_not_confuse_a_new_object_that_reuses_a_freed_id():
    store = UsageStore()
    first = _Weakrefable()
    assert store.claim_delivery(first) is True
    recycled_id = id(first)
    del first
    gc.collect()

    # Allocate until an object lands on the recycled id (CPython usually reuses it immediately).
    candidates = [_Weakrefable() for _ in range(2000)]
    match = next((c for c in candidates if id(c) == recycled_id), None)
    subject = match if match is not None else candidates[0]

    assert store.claim_delivery(subject) is True


def test_store_clear_resets_delivery_tracking():
    store = UsageStore()
    obj = _Weakrefable()
    assert store.claim_delivery(obj) is True

    store.clear()

    assert store.claim_delivery(obj) is True
