"""adk_tracegauge/_compat.py — Guards this package's one call into an
unguarded, non-public ADK internal.

Phase 2 W5 finding (docs/audit -- this module's own docstring is the
canonical record): ``EvaluationGenerator.convert_events_to_eval_invocations``
is the exact function ``LocalEvalService`` calls internally to build
``Invocation`` objects from a run's ``Event``s -- not a reimplementation
this package maintains -- but it carries no ``@experimental`` marker, no
public-API status, and no stated deprecation/breakage discipline at all
(stricter, less-guarded than the ``@experimental`` registry API this
package's own registration also depends on -- see README, "Compatibility
risk").

**The package's own primary, recommended integration path never touches
this function.** Confirmed by grep: nothing under ``src/adk_tracegauge/``
calls it. ``after_model_callback`` + ``adk eval``/``AgentEvaluator`` (this
package's documented quickstart, per the Phase 2 reframe) works because
``LocalEvalService``/``AgentEvaluator`` do their OWN internal Event ->
Invocation conversion; this package only ever reads the already-built
``Invocation`` objects they hand to a registered ``Evaluator``. The private
internal is needed by exactly one thing: the hand-rolled ``Runner`` harness
pattern (README, "The only path that reliably works") that a user copies
into their own eval script when they want full sub-agent cost rollup and to
call ``evaluate_invocations()`` directly, outside ``adk eval`` entirely.

Wrapped here -- rather than called directly by copy-pasted user code, as it
was before this module existed -- so that:

1. A version check runs first and produces one clear, actionable error
   naming the installed google-adk version and this package's known-tested
   range, instead of a bare, unexplained ``AttributeError``/``ImportError``
   if the internal has moved or been removed.
2. Every real call site (``tests/test_e2e_runner.py``, and any user code
   that adopts this wrapper instead of importing ``EvaluationGenerator``
   directly) goes through one place, so a future breakage is diagnosed and
   fixed once, not per call site.

This is a defense-in-depth wrapper, not a fix for the underlying risk: if
``EvaluationGenerator.convert_events_to_eval_invocations`` is ever renamed
or its behavior changes incompatibly (not just removed), this module cannot
detect that -- only an outright missing module/attribute produces the
actionable error below. The version-range check is a coarse, best-effort
signal on top of that, not a guarantee.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import google.adk as _google_adk

if TYPE_CHECKING:
    from google.adk.evaluation.eval_case import Invocation
    from google.adk.events.event import Event

_KNOWN_TESTED_MIN = (2, 6, 0)
_KNOWN_TESTED_MAX_EXCLUSIVE = (2, 8, 0)
"""Mirrors pyproject.toml's own google-adk[eval] pin (>=2.6.0,<2.8.0) --
kept in sync manually, not imported from pyproject.toml, since this module
must work from an installed wheel with no pyproject.toml on disk. If these
two ever drift, the drift is harmless (this check is advisory -- see module
docstring), but should be fixed at the same time the pin is next bumped."""


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """Best-effort parse of a dotted version prefix (``"2.6.3"`` -> ``(2, 6,
    3)``, ``"2.7.0rc1"`` -> ``(2, 7, 0)``, stopping at the first non-digit
    character in each dot-separated component).

    Returns ``None`` -- not a guess -- if no leading digits are found in the
    very first component (e.g. an unparseable or non-numeric version
    string). A ``None`` result skips the range check entirely (fails open
    on the *version comparison itself* only; the actual internal-API call
    below always runs, with its own independent ImportError/AttributeError
    handling regardless of whether the version could be parsed).
    """
    numbers: list[int] = []
    for part in raw.split(".")[:3]:
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers) if numbers else None


def convert_events_to_eval_invocations(events: list[Event]) -> list[Invocation]:
    """Version-guarded wrapper around
    ``EvaluationGenerator.convert_events_to_eval_invocations`` -- the exact
    ADK internal ``LocalEvalService`` uses to build ``Invocation`` objects
    from a real run's ``Event``s. See module docstring for which
    integration path actually needs this (the hand-rolled ``Runner``
    harness only -- not this package's primary ``adk eval``/
    ``AgentEvaluator`` quickstart).

    Warns (does not raise) if the installed google-adk version falls
    outside this package's known-tested range -- proceeds anyway, since an
    out-of-range version is often still compatible (see PLAN.md's W6 entry:
    google-adk 2.7.0 worked despite being outside the OLD pin). Raises
    ``RuntimeError`` with an actionable message -- naming the installed
    version, this package's known-tested range, and which integration path
    is actually affected -- if the internal has genuinely moved or been
    removed, instead of letting a bare ``ImportError``/``AttributeError``
    surface unexplained.
    """
    installed = getattr(_google_adk, "__version__", "unknown")
    parsed = _parse_version(installed) if installed != "unknown" else None
    if parsed is not None and not (_KNOWN_TESTED_MIN <= parsed < _KNOWN_TESTED_MAX_EXCLUSIVE):
        warnings.warn(
            f"adk_tracegauge: installed google-adk=={installed} is outside this "
            "package's known-tested range for the private "
            "EvaluationGenerator.convert_events_to_eval_invocations internal "
            f"({'.'.join(map(str, _KNOWN_TESTED_MIN))} <= version < "
            f"{'.'.join(map(str, _KNOWN_TESTED_MAX_EXCLUSIVE))}) -- proceeding, "
            "but if the call below fails, that range mismatch is why.",
            stacklevel=2,
        )

    _affected_path_note = (
        "This only affects the hand-rolled Runner harness pattern (see README, "
        "'Sub-agent delegation') -- this package's own primary "
        "adk eval/AgentEvaluator integration (after_model_callback + a "
        "threshold-bearing metric) does not call this function at all and is "
        "unaffected."
    )

    try:
        from google.adk.evaluation.evaluation_generator import EvaluationGenerator
    except ImportError as e:
        raise RuntimeError(
            "adk_tracegauge: could not import "
            "google.adk.evaluation.evaluation_generator.EvaluationGenerator -- "
            f"this is a non-public ADK internal (installed google-adk=={installed}) "
            "and it looks like it has moved or been removed in this release. "
            f"{_affected_path_note} Pin google-adk to "
            f"{'.'.join(map(str, _KNOWN_TESTED_MIN))}<=version<"
            f"{'.'.join(map(str, _KNOWN_TESTED_MAX_EXCLUSIVE))} or open an "
            "issue at https://github.com/gaurav-gandhi-2411/adk-tracegauge/issues."
        ) from e

    try:
        convert: Any = EvaluationGenerator.convert_events_to_eval_invocations
    except AttributeError as e:
        raise RuntimeError(
            "adk_tracegauge: google.adk.evaluation.evaluation_generator."
            "EvaluationGenerator no longer has a "
            "convert_events_to_eval_invocations attribute -- this is a "
            f"non-public ADK internal (installed google-adk=={installed}) and "
            "it looks like it has been renamed or removed in this release. "
            f"{_affected_path_note} Pin google-adk to "
            f"{'.'.join(map(str, _KNOWN_TESTED_MIN))}<=version<"
            f"{'.'.join(map(str, _KNOWN_TESTED_MAX_EXCLUSIVE))} or open an "
            "issue at https://github.com/gaurav-gandhi-2411/adk-tracegauge/issues."
        ) from e

    return convert(events)  # type: ignore[no-any-return]


def load_eval_case_ids_by_session_id(path: str | Path) -> dict[str, str]:
    """Phase 4 R2: reads an ADK-written ``.evalset_result.json`` file (the
    persisted output of an ``adk eval`` CLI run -- see
    ``LocalEvalSetResultsManager.save_eval_set_result``, which writes one to
    ``<agents_dir>/<app_name>/.adk/eval_history/*.evalset_result.json`` after
    every ``adk eval`` invocation) and returns a ``{session_id: eval_id}``
    mapping -- one entry per eval case in the file.

    This is the mechanism that makes eval case id reachable as a pairing key
    for `adk eval`'s CLI path at all. adk-tracegauge's own capture surface
    (``TraceGaugeUsagePlugin``'s callback signatures) has NO access to
    eval_case_id during a live run -- confirmed by reading
    ``InvocationContext``/``Session``/``Context`` (=``CallbackContext``)
    directly: none of them carry an eval_case_id field anywhere.
    eval_case_id is known only to ``LocalEvalService``/``EvaluationGenerator``,
    entirely outside the agent-execution callback surface. The ONE place it
    is genuinely available afterward is this persisted file, where ADK's own
    ``EvalCaseResult`` carries both ``eval_id`` (the authored, stable id from
    the .evalset.json) and ``session_id`` (the actual session the case ran
    under, live-capturable via ``TraceGaugeUsagePlugin.after_model_callback``
    -- see ``_plugin.py``). Joining on ``session_id`` (present in both this
    file and adk-tracegauge's own captured snapshot records) recovers the
    true eval_id per invocation -- see ``snapshot.py``'s ``build_snapshot``
    ``eval_case_ids_by_session`` parameter and ``_cli.py``'s
    ``--eval-history`` flag, the two real call sites.

    Like ``convert_events_to_eval_invocations`` above, ``EvalSetResult``/
    ``EvalCaseResult`` (``google.adk.evaluation.eval_result``) are not
    exported from ``google.adk.evaluation``'s own ``__init__.py`` -- not
    officially public API, same risk category as the other wrapped call in
    this module -- so this is guarded the same way: an actionable
    ``RuntimeError`` naming the installed version on an outright missing
    module/class, rather than a bare ``ImportError``/``AttributeError``.
    Malformed/foreign JSON at ``path`` (not really an ADK eval-history file)
    also raises ``RuntimeError``, not a silent empty mapping -- a caller
    passing the wrong file by mistake should learn about it immediately, not
    fall through to two-sample mode without ever finding out why.

    A case whose ``session_id``/``eval_id`` is empty (should not happen in
    practice -- both are non-optional, non-default fields on
    ``EvalCaseResult`` -- but pydantic's ``str = ""`` defaults mean an empty
    string is structurally possible) is excluded from the mapping rather than
    recorded as a real key: an empty string is not a usable pairing key.
    """
    installed = getattr(_google_adk, "__version__", "unknown")

    try:
        from google.adk.evaluation.eval_result import EvalSetResult
    except ImportError as e:
        raise RuntimeError(
            "adk_tracegauge: could not import "
            "google.adk.evaluation.eval_result.EvalSetResult -- this is a "
            f"non-public ADK internal (installed google-adk=={installed}) and it "
            "looks like it has moved or been removed in this release. This only "
            "affects --eval-history-based eval-case-id pairing for `tracegauge "
            "check --mode paired`; session_id-based pairing (a hand-rolled harness "
            "pinning runner.run_async(session_id=...)) and two-sample mode are both "
            "unaffected. Open an issue at "
            "https://github.com/gaurav-gandhi-2411/adk-tracegauge/issues."
        ) from e

    raw = Path(path).read_text(encoding="utf-8")
    try:
        result = EvalSetResult.model_validate_json(raw)
    except Exception as e:
        raise RuntimeError(
            f"adk_tracegauge: could not parse {path!s} as a google-adk "
            "EvalSetResult (an `adk eval` .evalset_result.json file) -- is this "
            "really the file `adk eval` wrote to "
            "<agents_dir>/<app_name>/.adk/eval_history/*.evalset_result.json? "
            f"Underlying error: {e}"
        ) from e

    return {
        case.session_id: case.eval_id
        for case in result.eval_case_results
        if case.session_id and case.eval_id
    }


__all__ = ["convert_events_to_eval_invocations", "load_eval_case_ids_by_session_id"]
