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
