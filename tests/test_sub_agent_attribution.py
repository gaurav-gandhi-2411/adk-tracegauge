"""tests/test_sub_agent_attribution.py -- LL2: per-call agent_name capture,
snapshot cost_by_agent, and `adk-tracegauge check --agent`.

LL2.5's four required cases, each covered explicitly below:
  1. the real two-agent AgentTool-delegation case (root delegates to one
     sub-agent) -- a real InMemoryRunner run, not a mock, same discipline as
     examples/02_subagent_rollup.py and tests/test_e2e_runner.py.
  2. agents sharing a name -- two distinct LlmAgent objects both named
     "shared_name", verifying this doesn't crash and documenting the actual
     (collapsed) behavior rather than assuming it.
  3. a deeply nested AgentTool chain (root -> mid -> leaf, three agents).
  4. the single-agent case, where nothing about the unscoped cost_usd/score
     changes -- cost_by_agent just adds one exact-match key.

Plus the store/snapshot/CLI-level unit tests for the mechanics those
end-to-end cases depend on: CapturedCall.agent_name default, cost_by_agent
grouping (including the empty-agent_name-contributes-no-key rule),
Snapshot.costs_for_agent's FILTER-not-zero-pad behavior (see its own
docstring for why zero-padding would corrupt a two-sample gate), agent-scoped
paired-mode grouping, schema_version 1/2/3 read compatibility, and the
`adk-tracegauge check --agent` CLI flag end to end.
"""

from __future__ import annotations

import json

import pytest
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types as genai_types

from adk_tracegauge._cli import main
from adk_tracegauge._store import CapturedCall, UsageStore
from adk_tracegauge.evaluator import METRIC_NAME, CostEfficiencyEvaluator, CostThresholdCriterion
from adk_tracegauge.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    build_snapshot,
    pair_costs_by_session_id,
    read_snapshot,
    write_snapshot,
)

# ---------------------------------------------------------------------------
# Store/snapshot-level unit tests (fast, no real ADK Runner needed).
# ---------------------------------------------------------------------------


def _call(
    model: str = "gemini-2.5-flash",
    prompt: int = 1000,
    output: int = 200,
    agent_name: str = "",
) -> CapturedCall:
    return CapturedCall(
        model_version=model,
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=0,
        total_token_count=prompt + output,
        agent_name=agent_name,
    )


def test_captured_call_agent_name_defaults_to_empty_string():
    call = CapturedCall(
        model_version="gemini-2.5-flash",
        prompt_token_count=100,
        candidates_token_count=20,
        cached_content_token_count=0,
        total_token_count=120,
    )
    assert call.agent_name == ""


def test_build_snapshot_single_agent_cost_by_agent_has_exactly_one_matching_key():
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, agent_name="solo_agent"))

    snapshot = build_snapshot(store)
    record = snapshot.records[0]

    assert set(record.cost_by_agent) == {"solo_agent"}
    assert record.cost_by_agent["solo_agent"] == pytest.approx(record.cost_usd)


def test_build_snapshot_empty_agent_name_contributes_no_key():
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, agent_name=""))

    snapshot = build_snapshot(store)

    assert snapshot.records[0].cost_by_agent == {}


def test_build_snapshot_two_calls_different_agents_within_one_invocation():
    # Agent-transfer case: one invocation_id, two different agent_names on
    # its calls -- cost_by_agent should carry both, not collapse to one.
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, agent_name="agent_a"))
    store.record("inv-1", _call(prompt=2000, output=400, agent_name="agent_b"))

    snapshot = build_snapshot(store)
    record = snapshot.records[0]

    assert set(record.cost_by_agent) == {"agent_a", "agent_b"}
    assert record.cost_by_agent["agent_a"] < record.cost_by_agent["agent_b"]
    assert record.cost_by_agent["agent_a"] + record.cost_by_agent["agent_b"] == pytest.approx(
        record.cost_usd
    )


def test_costs_for_agent_filters_records_the_agent_had_no_part_in():
    store = UsageStore()
    store.record("inv-root", _call(prompt=1000, output=200, agent_name="root_agent"))
    store.record("inv-sub", _call(prompt=500, output=100, agent_name="sub_agent"))

    snapshot = build_snapshot(store)

    # Two records total, but only one belongs to "sub_agent" -- costs_for_agent
    # must return a length-1 list, NOT a length-2 list with a fake 0.0 for the
    # root's record (see Snapshot.costs_for_agent's docstring: this feeds a
    # two-sample bootstrap directly, so a padded zero would be a fabricated
    # data point, not a real observation).
    sub_costs = snapshot.costs_for_agent("sub_agent")
    root_costs = snapshot.costs_for_agent("root_agent")

    assert len(sub_costs) == 1
    assert len(root_costs) == 1
    assert snapshot.costs_for_agent("nonexistent_agent") == []


def test_costs_by_session_id_agent_scoped_sums_only_that_agents_contribution():
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, agent_name="root_agent"))
    store.record("inv-2", _call(prompt=500, output=100, agent_name="sub_agent"))
    store.record_session("inv-1", "case-a")
    store.record_session("inv-2", "case-a")  # same session, e.g. one multi-invocation case

    snapshot = build_snapshot(store)

    unscoped = snapshot.costs_by_session_id()
    root_scoped = snapshot.costs_by_session_id(agent="root_agent")
    sub_scoped = snapshot.costs_by_session_id(agent="sub_agent")

    assert unscoped["case-a"] == pytest.approx(root_scoped["case-a"] + sub_scoped["case-a"])
    assert root_scoped["case-a"] > 0
    assert sub_scoped["case-a"] > 0


def test_pair_costs_by_session_id_agent_scoped_still_matches_on_session_id_not_cost():
    store_b = UsageStore()
    store_b.record("inv-1", _call(prompt=1000, output=200, agent_name="root_agent"))
    store_b.record_session("inv-1", "case-a")
    baseline = build_snapshot(store_b)

    store_c = UsageStore()
    store_c.record("inv-1", _call(prompt=2000, output=400, agent_name="root_agent"))
    store_c.record_session("inv-1", "case-a")
    current = build_snapshot(store_c)

    baseline_costs, current_costs, matched = pair_costs_by_session_id(
        baseline, current, agent="root_agent"
    )

    assert matched == ["case-a"]
    assert current_costs[0] > baseline_costs[0]


# ---------------------------------------------------------------------------
# Schema version compatibility (LL2.4): old files must still read cleanly,
# with cost_by_agent defaulting to {} rather than KeyError/TypeError.
# ---------------------------------------------------------------------------


def test_schema_version_bumped_to_3():
    assert SNAPSHOT_SCHEMA_VERSION == 3


def test_read_snapshot_defaults_cost_by_agent_to_empty_dict_for_a_v1_file(tmp_path):
    out_path = tmp_path / "v1.json"
    out_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
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

    assert snapshot.records[0].cost_by_agent == {}
    assert snapshot.costs_for_agent("anything") == []


def test_read_snapshot_defaults_cost_by_agent_to_empty_dict_for_a_v2_file(tmp_path):
    out_path = tmp_path / "v2.json"
    out_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
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
                        "session_id": "sess-a",
                        "eval_case_id": "case_1",
                    }
                ],
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = read_snapshot(out_path)

    assert snapshot.records[0].cost_by_agent == {}
    assert snapshot.records[0].eval_case_id == "case_1"  # v2 fields still round-trip


def test_write_then_read_snapshot_round_trips_cost_by_agent(tmp_path):
    store = UsageStore()
    store.record("inv-1", _call(prompt=1000, output=200, agent_name="root_agent"))
    out_path = tmp_path / "v3.json"

    written = write_snapshot(store, out_path)
    read_back = read_snapshot(out_path)

    assert written.schema_version == 3
    assert written.records[0].cost_by_agent == {
        "root_agent": pytest.approx(written.records[0].cost_usd)
    }
    assert read_back.records[0].cost_by_agent == written.records[0].cost_by_agent


# ---------------------------------------------------------------------------
# CLI end-to-end: `adk-tracegauge check --agent <name>`.
# ---------------------------------------------------------------------------


def test_cli_check_agent_flag_scopes_two_sample_mode_to_one_agent(tmp_path, capsys):
    baseline_store = UsageStore()
    for i in range(35):
        baseline_store.record(f"root-{i}", _call(prompt=1000, output=200, agent_name="root_agent"))
        baseline_store.record(f"sub-{i}", _call(prompt=500, output=100, agent_name="sub_agent"))
    baseline_path = tmp_path / "baseline.json"
    write_snapshot(baseline_store, baseline_path)

    # current run: sub_agent's cost triples, root_agent's stays flat.
    current_store = UsageStore()
    for i in range(35):
        current_store.record(f"root-{i}", _call(prompt=1000, output=200, agent_name="root_agent"))
        current_store.record(f"sub-{i}", _call(prompt=1500, output=300, agent_name="sub_agent"))
    current_path = tmp_path / "current.json"
    write_snapshot(current_store, current_path)

    exit_code = main(
        [
            "check",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--agent",
            "sub_agent",
            "--mode",
            "two-sample",
            "--min-n",
            "30",
        ]
    )

    out = capsys.readouterr().out
    assert "[agent=sub_agent]" in out
    # sub_agent's cost roughly tripled -- must be flagged as a regression.
    assert exit_code == 1


def test_cli_check_agent_flag_scoped_to_flat_agent_passes(tmp_path, capsys):
    baseline_store = UsageStore()
    current_store = UsageStore()
    for i in range(35):
        baseline_store.record(f"root-{i}", _call(prompt=1000, output=200, agent_name="root_agent"))
        baseline_store.record(f"sub-{i}", _call(prompt=500, output=100, agent_name="sub_agent"))
        current_store.record(f"root-{i}", _call(prompt=1000, output=200, agent_name="root_agent"))
        # sub_agent triples again, but we scope the gate to root_agent this time.
        current_store.record(f"sub-{i}", _call(prompt=1500, output=300, agent_name="sub_agent"))
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    write_snapshot(baseline_store, baseline_path)
    write_snapshot(current_store, current_path)

    exit_code = main(
        [
            "check",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--agent",
            "root_agent",
            "--mode",
            "two-sample",
            "--min-n",
            "30",
        ]
    )

    assert exit_code == 0


def test_cli_check_agent_flag_on_old_schema_file_reports_zero_cost_not_a_crash(tmp_path):
    # An old (pre-LL2) v1 snapshot has no cost_by_agent data at all --
    # --agent must report insufficient_data (zero real observations), not
    # crash. This is the exact "old snapshots must not break" requirement
    # (LL2.4) applied to the --agent flag specifically.
    old_path = tmp_path / "old.json"
    old_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "records": [
                    {
                        "invocation_id": f"inv-{i}",
                        "cost_usd": 0.01,
                        "tokens_input": 100,
                        "tokens_output": 20,
                        "tokens_cache_read": 0,
                        "models": ["gemini-2.5-flash"],
                        "call_count": 1,
                    }
                    for i in range(35)
                ],
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check",
            "--baseline",
            str(old_path),
            "--current",
            str(old_path),
            "--agent",
            "root_agent",
            "--mode",
            "two-sample",
        ]
    )

    from adk_tracegauge._cli import EXIT_INSUFFICIENT_DATA

    assert exit_code == EXIT_INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# Real-Runner end-to-end cases (LL2.5): AgentTool delegation, shared names,
# nested chains, and the single-agent backward-compat case.
# ---------------------------------------------------------------------------


def _fixed_response(model_version: str, text: str, prompt_tokens: int, output_tokens: int):
    return LlmResponse(
        model_version=model_version,
        content=genai_types.Content(parts=[genai_types.Part(text=text)], role="model"),
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=0,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


def _tool_call_response(model_version: str, tool_name: str, prompt_tokens: int, output_tokens: int):
    return LlmResponse(
        model_version=model_version,
        content=genai_types.Content(
            parts=[
                genai_types.Part(
                    function_call=genai_types.FunctionCall(id="call_1", name=tool_name, args={})
                )
            ],
            role="model",
        ),
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=0,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


class _LeafLlm(BaseLlm):
    model: str = "leaf-fake-model"

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["leaf-fake-model"]

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield _fixed_response("gemini-2.5-flash-lite", "leaf answer", 200_000, 50_000)


def _make_delegating_llm(tool_name: str, model_name: str):
    """A fake BaseLlm that calls `tool_name` once, then returns a final answer."""

    class _DelegatingLlm(BaseLlm):
        model: str = model_name
        _call_count: int = 0

        @classmethod
        def supported_models(cls) -> list[str]:
            return [model_name]

        async def generate_content_async(self, llm_request, stream: bool = False):
            self._call_count += 1
            if self._call_count == 1:
                yield _tool_call_response("gemini-2.5-pro", tool_name, 100_000, 10_000)
            else:
                yield _fixed_response("gemini-2.5-pro", "final answer", 120_000, 15_000)

    return _DelegatingLlm()


async def _run_and_capture(root_agent: LlmAgent, app_name: str) -> UsageStore:
    from adk_tracegauge._plugin import TraceGaugeUsagePlugin

    store = UsageStore()
    app = App(name=app_name, root_agent=root_agent, plugins=[TraceGaugeUsagePlugin(store=store)])
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name=app.name, user_id="u")
    async for _event in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=genai_types.Content(parts=[genai_types.Part(text="hello")], role="user"),
    ):
        pass
    return store


@pytest.mark.asyncio
async def test_real_two_agent_agenttool_delegation_captures_distinct_agent_names():
    sub_agent = LlmAgent(name="capital_finder", model=_LeafLlm(), instruction="Answer.")
    root_agent = LlmAgent(
        name="root_agent",
        model=_make_delegating_llm("capital_finder", "root-fake-model"),
        instruction="Delegate.",
        tools=[AgentTool(agent=sub_agent)],
    )

    store = await _run_and_capture(root_agent, "two_agent_test")

    invocation_ids = store.invocation_ids()
    assert len(invocation_ids) == 2
    root_id, sub_id = invocation_ids
    root_calls = store.get(root_id)
    sub_calls = store.get(sub_id)
    assert all(c.agent_name == "root_agent" for c in root_calls)
    assert all(c.agent_name == "capital_finder" for c in sub_calls)

    snapshot = build_snapshot(store)
    root_record = next(r for r in snapshot.records if r.invocation_id == root_id)
    sub_record = next(r for r in snapshot.records if r.invocation_id == sub_id)
    assert set(root_record.cost_by_agent) == {"root_agent"}
    assert set(sub_record.cost_by_agent) == {"capital_finder"}

    # Total cost attributable to capital_finder across the whole snapshot
    # (its own separate record -- build_snapshot does not roll descendants
    # into the parent's row, see build_snapshot's own docstring).
    total_sub_cost = sum(snapshot.costs_for_agent("capital_finder"))
    assert total_sub_cost == pytest.approx(sub_record.cost_usd)

    # The eval rationale text (LL2.2) names the agent per call line.
    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(
            metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=1000.0)
        ),
        store=store,
    )
    result = evaluator.evaluate_invocations(
        [Invocation(invocation_id=root_id, user_content=genai_types.Content(parts=[]))]
    )
    rationale = result.per_invocation_results[0].rubric_scores[0].rationale
    assert "agent=root_agent" in rationale


@pytest.mark.asyncio
async def test_agents_sharing_a_name_collapse_into_one_cost_by_agent_key_without_crashing():
    # Two distinct LlmAgent objects, deliberately given the SAME name -- ADK
    # itself has no mechanism at this API surface to disambiguate them, and
    # neither does callback_context.agent_name (a plain string). This test
    # documents the real, actual behavior (they collapse into one key) so
    # this is a known, tested characteristic, not an unverified assumption.
    sub_agent = LlmAgent(name="shared_name", model=_LeafLlm(), instruction="Answer.")
    root_agent = LlmAgent(
        name="shared_name",
        model=_make_delegating_llm("shared_name", "root-fake-model-2"),
        instruction="Delegate.",
        tools=[AgentTool(agent=sub_agent)],
    )

    store = await _run_and_capture(root_agent, "shared_name_test")

    invocation_ids = store.invocation_ids()
    assert len(invocation_ids) == 2
    for inv_id in invocation_ids:
        for call in store.get(inv_id):
            assert call.agent_name == "shared_name"

    snapshot = build_snapshot(store)
    for record in snapshot.records:
        assert set(record.cost_by_agent) == {"shared_name"}

    # Summing costs_for_agent("shared_name") across the snapshot recovers
    # the FULL total (both records), since both really are named identically.
    assert sum(snapshot.costs_for_agent("shared_name")) == pytest.approx(
        sum(r.cost_usd for r in snapshot.records)
    )


@pytest.mark.asyncio
async def test_deeply_nested_agenttool_chain_three_levels_each_own_agent_name():
    leaf_agent = LlmAgent(name="leaf_agent", model=_LeafLlm(), instruction="Answer.")
    mid_agent = LlmAgent(
        name="mid_agent",
        model=_make_delegating_llm("leaf_agent", "mid-fake-model"),
        instruction="Delegate to leaf.",
        tools=[AgentTool(agent=leaf_agent)],
    )
    root_agent = LlmAgent(
        name="root_agent",
        model=_make_delegating_llm("mid_agent", "root-fake-model-3"),
        instruction="Delegate to mid.",
        tools=[AgentTool(agent=mid_agent)],
    )

    store = await _run_and_capture(root_agent, "nested_chain_test")

    invocation_ids = store.invocation_ids()
    assert len(invocation_ids) == 3

    calls_by_agent: dict[str, int] = {}
    for inv_id in invocation_ids:
        for call in store.get(inv_id):
            calls_by_agent[call.agent_name] = calls_by_agent.get(call.agent_name, 0) + 1

    assert set(calls_by_agent) == {"root_agent", "mid_agent", "leaf_agent"}

    snapshot = build_snapshot(store)
    agent_names_across_records = {
        agent for record in snapshot.records for agent in record.cost_by_agent
    }
    assert agent_names_across_records == {"root_agent", "mid_agent", "leaf_agent"}

    # get_with_descendants from the root recovers all three levels' calls,
    # unchanged pre-existing rollup behavior -- LL2 must not have broken it.
    root_id = invocation_ids[0]
    assert len(store.get_with_descendants(root_id)) == len(store.get(root_id)) + sum(
        len(store.get(i)) for i in invocation_ids[1:]
    )


@pytest.mark.asyncio
async def test_single_agent_case_nothing_changes_about_the_unscoped_score():
    root_agent = LlmAgent(name="only_agent", model=_LeafLlm(), instruction="Answer.")

    store = await _run_and_capture(root_agent, "single_agent_test")

    invocation_ids = store.invocation_ids()
    assert len(invocation_ids) == 1
    inv_id = invocation_ids[0]

    snapshot = build_snapshot(store)
    record = snapshot.records[0]
    # Backward-compat: cost_by_agent adds exactly one key equal to the whole
    # record's cost -- the unscoped cost_usd/score is completely unaffected.
    assert set(record.cost_by_agent) == {"only_agent"}
    assert record.cost_by_agent["only_agent"] == pytest.approx(record.cost_usd)

    evaluator = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(metric_name=METRIC_NAME, threshold=1000.0), store=store
    )
    result = evaluator.evaluate_invocations(
        [Invocation(invocation_id=inv_id, user_content=genai_types.Content(parts=[]))]
    )
    assert result.per_invocation_results[0].score == pytest.approx(record.cost_usd)
