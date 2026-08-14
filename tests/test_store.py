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


def test_get_with_descendants_includes_recorded_child_calls():
    store = UsageStore()
    store.record("parent", _call(prompt=100))
    store.record("child", _call(prompt=200))
    store.record_parent("child", "parent")

    calls = store.get_with_descendants("parent")

    assert [c.prompt_token_count for c in calls] == [100, 200]


def test_get_with_descendants_recurses_through_nested_children():
    store = UsageStore()
    store.record("grandparent", _call(prompt=100))
    store.record("parent", _call(prompt=200))
    store.record("child", _call(prompt=300))
    store.record_parent("parent", "grandparent")
    store.record_parent("child", "parent")

    calls = store.get_with_descendants("grandparent")

    assert [c.prompt_token_count for c in calls] == [100, 200, 300]


def test_get_with_descendants_returns_only_own_calls_when_no_children():
    store = UsageStore()
    store.record("inv-1", _call())
    assert store.get_with_descendants("inv-1") == store.get("inv-1")


def test_get_with_descendants_does_not_pull_in_unrelated_siblings():
    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record("inv-2", _call(prompt=200))
    # inv-2 has no recorded parent relationship to inv-1.
    assert [c.prompt_token_count for c in store.get_with_descendants("inv-1")] == [100]


def test_get_with_descendants_does_not_infinite_loop_on_a_cycle():
    # A genuinely corrupted parent graph (should never happen via the plugin's
    # own before_run_callback/after_run_callback pairing, but get_with_descendants
    # must not hang if it somehow does).
    store = UsageStore()
    store.record("a", _call(prompt=100))
    store.record("b", _call(prompt=200))
    store.record_parent("a", "b")
    store.record_parent("b", "a")

    calls = store.get_with_descendants("a")

    assert {c.prompt_token_count for c in calls} == {100, 200}


def test_clear_also_clears_recorded_parent_relationships():
    store = UsageStore()
    store.record("parent", _call())
    store.record("child", _call())
    store.record_parent("child", "parent")
    store.clear()
    assert store.get_with_descendants("parent") == []


# --- session_id tracking (Phase 3 B4) -------------------------------------


def test_session_id_returns_none_for_unrecorded_invocation():
    store = UsageStore()
    assert store.session_id("nope") is None


def test_record_session_then_session_id_round_trips():
    store = UsageStore()
    store.record_session("inv-1", "case-42")
    assert store.session_id("inv-1") == "case-42"


def test_record_session_is_independent_per_invocation_id():
    store = UsageStore()
    store.record_session("inv-1", "case-a")
    store.record_session("inv-2", "case-b")
    assert store.session_id("inv-1") == "case-a"
    assert store.session_id("inv-2") == "case-b"


def test_record_session_overwrites_a_prior_value_for_the_same_invocation():
    store = UsageStore()
    store.record_session("inv-1", "case-a")
    store.record_session("inv-1", "case-a-retry")
    assert store.session_id("inv-1") == "case-a-retry"


def test_clear_also_clears_recorded_session_ids():
    store = UsageStore()
    store.record_session("inv-1", "case-42")
    store.clear()
    assert store.session_id("inv-1") is None
