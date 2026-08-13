from __future__ import annotations

from adk_tracegauge._store import CapturedCall, UsageStore


def _call(model: str = "gemini-2.5-flash", prompt: int = 100) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=50,
        cached_content_token_count=0,
        total_token_count=prompt + 50,
    )


def test_get_returns_empty_list_for_unknown_invocation():
    store = UsageStore()
    assert store.get("nope") == []


def test_record_then_get_returns_the_call():
    store = UsageStore()
    call = _call()
    store.record("inv-1", call)
    assert store.get("inv-1") == [call]


def test_multiple_calls_accumulate_in_order():
    store = UsageStore()
    first = _call(prompt=100)
    second = _call(prompt=200)
    store.record("inv-1", first)
    store.record("inv-1", second)
    assert store.get("inv-1") == [first, second]


def test_calls_are_isolated_per_invocation_id():
    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record("inv-2", _call(prompt=200))
    assert len(store.get("inv-1")) == 1
    assert len(store.get("inv-2")) == 1
    assert store.get("inv-1")[0].prompt_token_count == 100
    assert store.get("inv-2")[0].prompt_token_count == 200


def test_clear_removes_everything():
    store = UsageStore()
    store.record("inv-1", _call())
    store.clear()
    assert store.get("inv-1") == []


def test_get_returns_a_copy_not_the_live_list():
    store = UsageStore()
    store.record("inv-1", _call())
    snapshot = store.get("inv-1")
    snapshot.append(_call(prompt=999))
    assert len(store.get("inv-1")) == 1
