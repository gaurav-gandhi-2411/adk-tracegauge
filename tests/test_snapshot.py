from __future__ import annotations

from pathlib import Path

import pytest

from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    build_snapshot,
    read_snapshot,
    write_snapshot,
)


def _call(
    model: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    cached: int = 0,
) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=cached,
        total_token_count=prompt + output,
    )


def test_build_snapshot_prices_a_single_invocation():
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200))

    snapshot = build_snapshot(store)

    assert snapshot.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert len(snapshot.records) == 1
    assert snapshot.skipped == []
    record = snapshot.records[0]
    assert record.invocation_id == "inv-1"
    assert record.cost_usd > 0
    assert record.tokens_input == 1000
    assert record.tokens_output == 200
    assert record.models == ["gemini-2.5-flash"]
    assert record.call_count == 1


def test_build_snapshot_two_invocations_two_records():
    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record("inv-2", _call(prompt=200))

    snapshot = build_snapshot(store)

    assert {r.invocation_id for r in snapshot.records} == {"inv-1", "inv-2"}


def test_build_snapshot_skips_unresolved_model_with_a_reason():
    store = UsageStore()
    store.record("inv-1", _call(model="totally-unknown-model-xyz"))

    snapshot = build_snapshot(store)

    assert snapshot.records == []
    assert len(snapshot.skipped) == 1
    assert snapshot.skipped[0].invocation_id == "inv-1"
    assert "totally-unknown-model-xyz" in snapshot.skipped[0].reason


def test_build_snapshot_skips_only_the_bad_invocation_not_the_whole_run():
    store = UsageStore()
    store.record("good", _call())
    store.record("bad", _call(model="totally-unknown-model-xyz"))

    snapshot = build_snapshot(store)

    assert [r.invocation_id for r in snapshot.records] == ["good"]
    assert [s.invocation_id for s in snapshot.skipped] == ["bad"]


def test_costs_returns_the_priced_cost_list():
    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record("inv-2", _call(prompt=200))

    snapshot = build_snapshot(store)

    assert snapshot.costs() == [r.cost_usd for r in snapshot.records]
    assert len(snapshot.costs()) == 2


def test_write_then_read_snapshot_round_trips_exactly(tmp_path: Path):
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, cached=50))
    store.record("inv-2", _call(model="totally-unknown-model-xyz"))
    out_path = tmp_path / "snapshot.json"

    written = write_snapshot(store, out_path)
    read_back = read_snapshot(out_path)

    assert read_back.schema_version == written.schema_version
    assert read_back.records == written.records
    assert read_back.skipped == written.skipped
    assert read_back.costs() == written.costs()


def test_write_snapshot_creates_valid_json_file(tmp_path: Path):
    store = UsageStore()
    store.record("inv-1", _call())
    out_path = tmp_path / "snapshot.json"

    write_snapshot(store, out_path)

    assert out_path.exists()
    import json

    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert "created_at" in raw
    assert isinstance(raw["records"], list)
    assert raw["records"][0]["invocation_id"] == "inv-1"


def test_read_snapshot_rejects_unknown_schema_version(tmp_path: Path):
    out_path = tmp_path / "bad.json"
    out_path.write_text('{"schema_version": 999, "records": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        read_snapshot(out_path)


def test_build_snapshot_empty_store_produces_empty_snapshot():
    snapshot = build_snapshot(UsageStore())
    assert snapshot.records == []
    assert snapshot.skipped == []
    assert snapshot.costs() == []
