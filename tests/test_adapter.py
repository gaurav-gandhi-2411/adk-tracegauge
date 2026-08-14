from __future__ import annotations

from adk_tracegauge._adapter import build_session_digest, unknown_model_message
from adk_tracegauge._store import CapturedCall


def _call(
    model: str = "gemini-2.5-flash", prompt: int = 1000, output: int = 200, cached: int = 0
) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=cached,
        total_token_count=prompt + output,
    )


def test_build_session_digest_happy_path_single_call():
    result = build_session_digest("inv-1", [_call()])
    assert result.ok
    assert result.unresolved_model is None
    assert result.digest.session_id == "inv-1"
    assert result.digest.turn_count == 1
    turn = result.digest.turns[0]
    assert turn.role == "ai"
    assert turn.model == "gemini-2.5-flash"
    assert turn.token_count_input == 1000
    assert turn.token_count_output == 200
    assert turn.cache_creation == 0


def test_build_session_digest_multiple_calls_become_multiple_turns():
    result = build_session_digest("inv-1", [_call(prompt=100), _call(prompt=200)])
    assert result.ok
    assert result.digest.turn_count == 2
    assert [t.turn_index for t in result.digest.turns] == [0, 1]


def test_build_session_digest_carries_cache_read():
    result = build_session_digest("inv-1", [_call(cached=300)])
    assert result.ok
    assert result.digest.turns[0].cache_read == 300


def test_build_session_digest_fails_closed_on_unresolved_model():
    result = build_session_digest("inv-1", [_call(model="claude-sonnet-4-6")])
    assert not result.ok
    assert result.digest is None
    assert result.unresolved_model == "claude-sonnet-4-6"


def test_build_session_digest_stops_at_first_unresolved_model_no_partial_digest():
    result = build_session_digest(
        "inv-1", [_call(model="gemini-2.5-flash"), _call(model="unknown-model-x")]
    )
    assert not result.ok
    assert result.unresolved_model == "unknown-model-x"


def test_unknown_model_message_names_the_model_and_lists_known_ones():
    message = unknown_model_message("claude-sonnet-4-6")
    assert "claude-sonnet-4-6" in message
    assert "gemini-2.5-flash" in message


def _streamed_call(total: int, output: int, partial: bool) -> CapturedCall:
    return CapturedCall(
        model_version="gemini-2.5-flash",
        prompt_token_count=100_000,
        candidates_token_count=output,
        cached_content_token_count=0,
        total_token_count=total,
        partial=partial,
    )


def test_build_session_digest_collapses_streamed_chunks_into_one_turn():
    # Three chunks of ONE real streamed call: growing cumulative totals,
    # final chunk (partial=False) carries the true total.
    calls = [
        _streamed_call(total=110_000, output=10_000, partial=True),
        _streamed_call(total=130_000, output=30_000, partial=True),
        _streamed_call(total=150_000, output=50_000, partial=False),
    ]
    result = build_session_digest("inv-1", calls)
    assert result.ok
    assert result.digest.turn_count == 1


def test_build_session_digest_uses_final_chunk_totals_not_sum_of_chunks():
    calls = [
        _streamed_call(total=110_000, output=10_000, partial=True),
        _streamed_call(total=130_000, output=30_000, partial=True),
        _streamed_call(total=150_000, output=50_000, partial=False),
    ]
    result = build_session_digest("inv-1", calls)
    turn = result.digest.turns[0]
    # Final chunk's own values, not summed across all three chunks.
    assert turn.token_count_input == 100_000
    assert turn.token_count_output == 50_000


def test_build_session_digest_non_streamed_calls_are_each_their_own_turn():
    # partial defaults to False -- today's ordinary non-streaming/tool-loop
    # shape, unaffected by the streaming-chunk grouping logic.
    result = build_session_digest("inv-1", [_call(prompt=100), _call(prompt=200)])
    assert result.ok
    assert result.digest.turn_count == 2


def test_build_session_digest_fails_closed_on_decreasing_totals_within_a_group():
    calls = [
        _streamed_call(total=150_000, output=50_000, partial=True),
        _streamed_call(total=110_000, output=10_000, partial=False),  # regressed
    ]
    result = build_session_digest("inv-1", calls)
    assert not result.ok
    assert result.digest is None
    assert result.streaming_anomaly is not None
    assert "decreased" in result.streaming_anomaly


def test_build_session_digest_fails_closed_on_unterminated_partial_stream():
    calls = [_streamed_call(total=110_000, output=10_000, partial=True)]
    result = build_session_digest("inv-1", calls)
    assert not result.ok
    assert result.streaming_anomaly is not None
    assert "never reached a final" in result.streaming_anomaly


def test_build_session_digest_equal_totals_across_chunks_is_not_a_violation():
    # Non-decreasing allows equal -- a chunk with no new output tokens yet
    # isn't itself an anomaly.
    calls = [
        _streamed_call(total=110_000, output=10_000, partial=True),
        _streamed_call(total=110_000, output=10_000, partial=False),
    ]
    result = build_session_digest("inv-1", calls)
    assert result.ok
