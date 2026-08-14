"""Tests for _compat.py's version-guarded wrapper around ADK's private
EvaluationGenerator.convert_events_to_eval_invocations internal (Phase 2 W5).

The happy path (a real call against the installed, in-range google-adk) is
exercised end-to-end by test_e2e_runner.py's real Runner/Event flow -- not
duplicated here with fake Event objects, since that function's real
contract is ADK's own, not this package's. These tests cover this module's
OWN logic: version parsing, the out-of-range warning, and the two
actionable-error paths a genuinely broken internal would hit.
"""

from __future__ import annotations

import builtins

import pytest

from adk_tracegauge import _compat


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.6.3", (2, 6, 3)),
        ("2.7.0", (2, 7, 0)),
        ("2.7.0rc1", (2, 7, 0)),
        ("2.7", (2, 7)),
        ("2", (2,)),
    ],
)
def test_parse_version_extracts_leading_digits_per_component(raw, expected):
    assert _compat._parse_version(raw) == expected


def test_parse_version_returns_none_for_unparseable_string():
    assert _compat._parse_version("not-a-version") is None


def test_real_call_delegates_to_the_installed_evaluationgenerator():
    from google.adk.evaluation.evaluation_generator import EvaluationGenerator

    assert _compat.convert_events_to_eval_invocations(
        []
    ) == EvaluationGenerator.convert_events_to_eval_invocations([])


def test_out_of_range_installed_version_warns_but_still_calls_through(monkeypatch):
    monkeypatch.setattr(_compat._google_adk, "__version__", "9.9.9", raising=False)
    with pytest.warns(UserWarning, match="outside this package's known-tested range"):
        result = _compat.convert_events_to_eval_invocations([])
    assert result == []


def test_unparseable_installed_version_skips_the_warning_and_still_calls_through(monkeypatch):
    monkeypatch.setattr(_compat._google_adk, "__version__", "not-a-version", raising=False)
    with warnings_none_expected():
        result = _compat.convert_events_to_eval_invocations([])
    assert result == []


class warnings_none_expected:
    """Tiny context manager asserting no warning fires -- pytest.warns has
    no direct "assert none" mode, and importing warnings.catch_warnings
    here inline (rather than at module scope) keeps this test self-
    contained next to the one assertion it backs."""

    def __enter__(self):
        import warnings

        self._cm = warnings.catch_warnings(record=True)
        self._records = self._cm.__enter__()
        warnings.simplefilter("always")
        return self

    def __exit__(self, *exc_info):
        self._cm.__exit__(*exc_info)
        assert not self._records, f"expected no warnings, got: {self._records}"


def test_missing_evaluation_generator_module_raises_actionable_runtimeerror(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "google.adk.evaluation.evaluation_generator":
            raise ImportError("simulated: module moved")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="could not import"):
        _compat.convert_events_to_eval_invocations([])


def test_missing_convert_method_raises_actionable_runtimeerror(monkeypatch):
    from google.adk.evaluation import evaluation_generator

    monkeypatch.delattr(
        evaluation_generator.EvaluationGenerator, "convert_events_to_eval_invocations"
    )
    with pytest.raises(RuntimeError, match="no longer has a"):
        _compat.convert_events_to_eval_invocations([])
