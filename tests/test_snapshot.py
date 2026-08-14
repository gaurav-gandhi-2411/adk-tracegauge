from __future__ import annotations

from pathlib import Path

import pytest

from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    Snapshot,
    SnapshotRecord,
    build_snapshot,
    pair_costs_by_session_id,
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


# --- session_id / pairing (Phase 3 B4) ------------------------------------


def test_build_snapshot_records_session_id_when_the_store_has_one():
    store = UsageStore()
    store.record("inv-1", _call())
    store.record_session("inv-1", "case-42")

    snapshot = build_snapshot(store)

    assert snapshot.records[0].session_id == "case-42"


def test_build_snapshot_leaves_session_id_none_when_the_store_has_none():
    store = UsageStore()
    store.record("inv-1", _call())

    snapshot = build_snapshot(store)

    assert snapshot.records[0].session_id is None


def test_write_then_read_snapshot_round_trips_session_id(tmp_path: Path):
    store = UsageStore()
    store.record("inv-1", _call())
    store.record_session("inv-1", "case-42")
    out_path = tmp_path / "snapshot.json"

    written = write_snapshot(store, out_path)
    read_back = read_snapshot(out_path)

    assert written.records[0].session_id == "case-42"
    assert read_back.records[0].session_id == "case-42"


def test_read_snapshot_defaults_session_id_to_none_for_a_v1_file_without_it(tmp_path: Path):
    # A snapshot written before Phase 3 B4 has no "session_id" key at all in
    # its record JSON -- must still read back cleanly, not KeyError/TypeError.
    import json

    out_path = tmp_path / "old.json"
    out_path.write_text(
        json.dumps(
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "created_at": "2026-01-01T00:00:00+00:00",
                "records": [
                    {
                        "invocation_id": "inv-1",
                        "cost_usd": 0.01,
                        "tokens_input": 100,
                        "tokens_output": 20,
                        "tokens_cache_read": 0,
                        "models": ["gemini-2.5-flash"],
                        "call_count": 1,
                    }
                ],
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = read_snapshot(out_path)

    assert snapshot.records[0].session_id is None


def test_costs_by_session_id_excludes_records_with_no_session_id():
    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record_session("inv-1", "case-a")
    store.record("inv-2", _call(prompt=200))  # no session_id recorded

    snapshot = build_snapshot(store)
    totals = snapshot.costs_by_session_id()

    assert set(totals) == {"case-a"}


def test_costs_by_session_id_sums_multiple_invocations_sharing_one_session():
    solo_store = UsageStore()
    solo_store.record("solo", _call(prompt=100))
    single_call_cost = build_snapshot(solo_store).costs()[0]

    store = UsageStore()
    store.record("inv-1", _call(prompt=100))
    store.record("inv-2", _call(prompt=100))
    store.record_session("inv-1", "case-a")
    store.record_session("inv-2", "case-a")  # same session, e.g. a multi-turn eval case

    snapshot = build_snapshot(store)
    totals = snapshot.costs_by_session_id()

    assert totals["case-a"] == pytest.approx(2 * single_call_cost)


def _snapshot_from_pairs(pairs: dict[str, float]) -> Snapshot:
    """A minimal Snapshot built directly from {session_id: cost_usd} --
    bypasses UsageStore/pricing entirely, for testing pair_costs_by_session_id
    in isolation."""
    records = [
        SnapshotRecord(
            invocation_id=f"inv-{session_id}",
            cost_usd=cost,
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=0,
            models=[],
            call_count=1,
            session_id=session_id,
        )
        for session_id, cost in pairs.items()
    ]
    return Snapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION, created_at="2026-01-01", records=records
    )


def test_pair_costs_by_session_id_aligns_matching_keys_in_sorted_order():
    baseline = _snapshot_from_pairs({"case-b": 0.02, "case-a": 0.01})
    current = _snapshot_from_pairs({"case-a": 0.015, "case-b": 0.03})

    baseline_costs, current_costs, matched = pair_costs_by_session_id(baseline, current)

    assert matched == ["case-a", "case-b"]
    assert baseline_costs == [0.01, 0.02]
    assert current_costs == [0.015, 0.03]


def test_pair_costs_by_session_id_excludes_keys_present_in_only_one_snapshot():
    baseline = _snapshot_from_pairs({"case-a": 0.01, "only-in-baseline": 0.05})
    current = _snapshot_from_pairs({"case-a": 0.015, "only-in-current": 0.07})

    baseline_costs, current_costs, matched = pair_costs_by_session_id(baseline, current)

    assert matched == ["case-a"]
    assert baseline_costs == [0.01]
    assert current_costs == [0.015]


def test_pair_costs_by_session_id_empty_when_no_overlap():
    baseline = _snapshot_from_pairs({"case-a": 0.01})
    current = _snapshot_from_pairs({"case-b": 0.02})

    baseline_costs, current_costs, matched = pair_costs_by_session_id(baseline, current)

    assert (baseline_costs, current_costs, matched) == ([], [], [])
