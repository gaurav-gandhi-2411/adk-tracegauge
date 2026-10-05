"""EvalStatus.INFORMATIONAL (google-adk >= 2.10.0, ADK commit 2d428ead3).

Two code paths in this package touch ADK-produced or ADK-consumed eval statuses:

* ``evaluator._aggregate_eval_status`` -- folds this evaluator's own per-invocation statuses.
* ``_compat.load_eval_case_ids_by_session_id`` -- validates an ADK-written
  ``.evalset_result.json`` through ADK's own ``EvalSetResult``; a new enum value must load.

``INFORMATIONAL`` does not exist on the supported floor (2.6.0), so every test that needs the member
is skipped there rather than faked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.evaluation.eval_metrics import EvalStatus

from adk_tracegauge import _compat
from adk_tracegauge.evaluator import _aggregate_eval_status

INFORMATIONAL = getattr(EvalStatus, "INFORMATIONAL", None)
needs_informational = pytest.mark.skipif(
    INFORMATIONAL is None, reason="EvalStatus.INFORMATIONAL needs google-adk >= 2.10.0"
)


def test_aggregate_without_informational_is_unchanged() -> None:
    P, F, N = EvalStatus.PASSED, EvalStatus.FAILED, EvalStatus.NOT_EVALUATED
    assert _aggregate_eval_status([P, F]) == F
    assert _aggregate_eval_status([N, P]) == P
    assert _aggregate_eval_status([N, N]) == N
    assert _aggregate_eval_status([]) == N


@needs_informational
def test_informational_never_overrides_a_real_verdict() -> None:
    assert _aggregate_eval_status([INFORMATIONAL, EvalStatus.FAILED]) == EvalStatus.FAILED
    assert _aggregate_eval_status([INFORMATIONAL, EvalStatus.PASSED]) == EvalStatus.PASSED
    assert (
        _aggregate_eval_status([EvalStatus.PASSED, INFORMATIONAL, EvalStatus.FAILED])
        == EvalStatus.FAILED
    )


@needs_informational
def test_an_all_informational_case_is_informational_not_not_evaluated() -> None:
    assert _aggregate_eval_status([INFORMATIONAL]) == INFORMATIONAL
    assert _aggregate_eval_status([INFORMATIONAL, INFORMATIONAL]) == INFORMATIONAL


@needs_informational
def test_informational_mixed_with_not_evaluated_stays_not_evaluated() -> None:
    # Something in the case produced no value at all, so "reported, does not gate" is not true of
    # the whole case; the existing NOT_EVALUATED default stands.
    assert (
        _aggregate_eval_status([INFORMATIONAL, EvalStatus.NOT_EVALUATED])
        == EvalStatus.NOT_EVALUATED
    )


@needs_informational
def test_eval_history_file_with_an_informational_status_loads(tmp_path: Path) -> None:
    from google.adk.evaluation.eval_result import EvalCaseResult, EvalSetResult

    result = EvalSetResult(
        eval_set_result_id="app_s_1",
        eval_set_id="s",
        eval_case_results=[
            EvalCaseResult(
                eval_set_id="s",
                eval_id="case_1",
                final_eval_status=INFORMATIONAL,
                overall_eval_metric_results=[],
                eval_metric_result_per_invocation=[],
                session_id="sess-a",
            )
        ],
    )
    path = tmp_path / "app_s_1.evalset_result.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    assert _compat.load_eval_case_ids_by_session_id(path) == {"sess-a": "case_1"}
