"""adk_tracegauge/snapshot.py — Persist a UsageStore's per-invocation cost
distribution to disk, and read it back.

Nothing else in this package persists a ``UsageStore``'s captured data --
it's process-only, built up live during an eval/agent run. The Phase 2 W4
CI regression gate (``adk-tracegauge check``, see ``_cli.py``) needs to compare
TWO such distributions (a saved baseline, and the current run) across
separate process invocations (a baseline captured on a past CI run or
committed to the repo; the current run's own fresh process) -- so this
module defines the on-disk format and the two functions that cross that
boundary: ``write_snapshot`` (in-process UsageStore -> JSON file, run at the
end of an eval script) and ``read_snapshot`` (JSON file -> Snapshot, run by
the ``check`` CLI subcommand, which never needs a live UsageStore at all).

Snapshot JSON schema (schema_version=3, LL2 -- see below for what changed
and why)::

    {
      "schema_version": 3,
      "created_at": "2026-08-14T12:00:00+00:00",
      "records": [
        {
          "invocation_id": "e-1234...",
          "session_id": "case-42",
          "eval_case_id": "my_eval_case_7",
          "cost_usd": 0.004231,
          "tokens_input": 512,
          "tokens_output": 128,
          "tokens_cache_read": 0,
          "models": ["gemini-2.5-flash"],
          "call_count": 1,
          "cost_by_agent": {"root_agent": 0.004231}
        },
        ...
      ],
      "skipped": [
        {"invocation_id": "e-5678...", "reason": "cost not computed: ..."}
      ]
    }

**LL2 -- ``cost_by_agent`` (additive field, schema_version bumped 2->3):**
per-record breakdown of ``cost_usd`` by the ``agent_name`` that made each
priced call within that invocation (see ``_store.CapturedCall.agent_name``
and ``_adapter.AdaptResult.agent_names_by_turn``). Keyed by agent name,
summing every turn's ``total_usd`` for that agent; a turn with an empty
(unresolved) agent_name contributes to no key at all rather than being
attributed under a misleading `""` bucket. ``build_snapshot`` stays on
``store.get`` (raw, one invocation_id at a time, no rollup -- see its own
docstring below for why), so in the common AgentTool-delegation case this
dict has exactly one key (that invocation's one agent) and a delegated
sub-agent's cost lives in ITS OWN separate record, not folded into its
parent's ``cost_by_agent``. Nothing about this design assumes one-agent-
per-invocation, though: ADK's OTHER multi-agent mechanism, agent transfer/
handoff (`transfer_to_agent`), can hand control to a different agent
WITHOUT spawning a new invocation_id, which would make several calls in one
invocation carry different agent_name values -- because ``cost_by_agent``
is built from the PER-TURN ``agent_names_by_turn`` array, not a single
per-invocation value, that case is handled correctly too, with more than
one key in the same record. To see one agent's TOTAL cost across a whole
delegation tree, sum ``cost_by_agent.get(name, 0.0)`` across every record in
the snapshot (``Snapshot.costs_for_agent`` does exactly this).

One record per invocation that could be priced (same fail-closed pricing
path as ``CostEfficiencyEvaluator`` -- an invocation whose model doesn't
resolve, or whose streamed chunks fail the monotonicity check, or that
carries an unpriced token category, is never fabricated a cost; it is
recorded under ``skipped`` with the reason instead, and excluded from the
``records`` list ``adk-tracegauge check`` runs its statistics over). This keeps
a single unpriceable invocation from poisoning or silently dropping an
entire snapshot -- the caller can see exactly what was skipped and why.

``session_id`` (Phase 3 B4, additive field -- old schema_version=1 files
without it still read back fine, with ``session_id=None`` per record) is
the ADK ``session.id`` the invocation ran under, captured by
``TraceGaugeUsagePlugin.before_run_callback``/``after_model_callback`` via
``UsageStore.record_session``. B4 shipped this as the sole pairing key for
``adk-tracegauge check --mode paired``'s higher-power paired bootstrap (see
``_regression.py``'s module docstring for why pairing matters).

**Phase 4 R2 correction -- this was found to be broken for the primary
`adk eval` CLI path, for TWO independent reasons, not one:**

1. ``session_id`` itself is regenerated fresh and random on every `adk eval`
   run UNLESS the eval case's own ``session_input.session_id`` is explicitly
   authored in the .evalset.json file -- confirmed by reading google-adk's
   ``local_eval_service.py`` (``_perform_inference_single_eval_item``: a
   ``pinned_session_id`` is used only ``if initial_session`` and its
   ``session_id`` is set; otherwise a fresh ``uuid.uuid4()``-based one is
   generated via ``_get_session_id()``). Most .evalset.json files do not set
   this -- it is an opt-in mechanism, not the default.
2. Independently of (1): the ORIGINAL capture mechanism
   (``before_run_callback``) never fires at all during `adk eval`/
   ``AgentEvaluator.evaluate()`` -- both build their own bare ``Runner``
   with no ``App``/Plugin wiring (see ``_plugin.py``'s module docstring).
   So even a hand-authored, stable ``session_input.session_id`` was never
   actually being captured through the CLI path, regardless of (1). This is
   now fixed (session_id is also captured from ``after_model_callback``,
   which does fire through `adk eval`) -- but reason (1) means session_id
   still does not solve the default-case problem: an unauthored .evalset.json
   still gets a fresh random session_id every run.

**The fix: eval case id, not session_id, is now the PRIMARY pairing key.**
``EvalCase.eval_id`` (confirmed by reading google-adk's ``eval_case.py``) is
a required field read directly from the .evalset.json file -- authored,
never regenerated, stable across every run of the same file, with no opt-in
needed. The catch: it is genuinely UNREACHABLE from adk-tracegauge's live
capture path -- ``InvocationContext``/``Session``/``Context`` (=
``CallbackContext``) carry no eval_case_id field anywhere; it exists only in
``LocalEvalService``/``EvaluationGenerator``'s own external bookkeeping,
entirely outside the agent-execution callback surface this package hooks
into. The one place it IS available is ADK's own persisted eval-history file
(``<agents_dir>/<app_name>/.adk/eval_history/*.evalset_result.json``,
written by `adk eval` after every run), whose ``EvalCaseResult`` entries
carry BOTH ``eval_id`` and ``session_id`` per case -- so ``session_id``
(now capturable live, see above) is the JOIN KEY that recovers the true,
stable ``eval_id`` post-hoc. See ``_compat.load_eval_case_ids_by_session_id``
and ``_cli.py``'s ``adk-tracegauge snapshot --eval-history`` flag, the real
mechanism that builds and applies this join.

``eval_case_id`` (this field, additive, schema_version bumped 1->2 -- see
``SNAPSHOT_SCHEMA_VERSION``) is the resolved-if-available result: populated
by ``build_snapshot``'s ``eval_case_ids_by_session`` parameter when the
caller supplies the eval-history join map (i.e. `adk-tracegauge snapshot
--eval-history <path>` was used), else ``None``.

**The fallback chain `adk-tracegauge check --mode {auto,paired}` actually
uses** (see ``resolve_pairing`` below, and ``_cli.py``'s printed output,
which always names which key was actually used -- never silently chosen):

1. ``eval_case_id`` -- if any records in BOTH snapshots carry one (i.e.
   ``--eval-history`` was used for both the baseline and current
   `adk-tracegauge snapshot` runs). Works for the DEFAULT `adk eval` CLI flow,
   with no opt-in needed in the .evalset.json file at all.
2. ``session_id`` -- if eval_case_id has zero overlap but session_id does
   (a hand-rolled harness pinning ``runner.run_async(session_id=...)``
   directly, B4's original mechanism -- still fully supported, unchanged).
3. Neither -- falls back to (or fails closed to, for an explicit `--mode
   paired` request) plain two-sample comparison, the original, always-safe
   Phase 2 W4 method.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ._adapter import build_session_digest, price_digest
from ._pricing import load_gemini_prices
from ._store import UsageStore

SNAPSHOT_SCHEMA_VERSION = 3
"""Bumped 2->3 in LL2 for the new ``cost_by_agent`` field (see module
docstring). Same additive-field precedent as the 1->2 bump: ``read_snapshot``
accepts 1, 2, AND 3 -- a v1 or v2 file is structurally still perfectly valid
(``cost_by_agent`` just defaults to ``{}`` via ``SnapshotRecord(**r)``, since
neither older file's records carry that key at all) and remains fully usable
everywhere except ``adk-tracegauge check --agent <name>`` (LL2.3), which
correctly reports zero cost for every record on such a file rather than
crashing -- an old snapshot genuinely has no per-agent data to report,
that's not a bug to work around. The bump exists purely as accurate
provenance -- schema_version=3 means "this file COULD carry cost_by_agent",
not a promise every record's dict is non-empty (calls with an unresolved
agent_name contribute to no key at all, see module docstring) -- and to make
a genuinely-unknown future version (4+) fail loudly via the explicit version
check below rather than silently misparsing new fields this version of
adk-tracegauge doesn't know about."""
_READABLE_SCHEMA_VERSIONS = (1, 2, 3)


@dataclass(frozen=True)
class SnapshotRecord:
    """One priced invocation's cost and token breakdown, as persisted."""

    invocation_id: str
    cost_usd: float
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    models: list[str]
    call_count: int
    session_id: str | None = None
    """The ADK session.id this invocation ran under, if the plugin could
    capture one -- see module docstring. Defaults to None so a v1 snapshot
    JSON file (written before this field existed) still deserializes via
    ``SnapshotRecord(**r)`` with no KeyError."""
    eval_case_id: str | None = None
    """The stable, authored ``EvalCase.eval_id`` this invocation belongs to,
    if resolved -- see module docstring's Phase 4 R2 section. Populated only
    when ``build_snapshot``/``write_snapshot`` was given
    ``eval_case_ids_by_session`` (i.e. `adk-tracegauge snapshot --eval-history`
    was used) AND this record's ``session_id`` was found in that map;
    ``None`` otherwise (including for every pre-Phase-4-R2 snapshot file,
    schema_version 1 or 2)."""
    cost_by_agent: dict[str, float] = field(default_factory=dict)
    """LL2: this record's ``cost_usd`` broken down by the agent_name that
    made each priced call -- see module docstring's LL2 section for the
    full design (why this stays keyed within one record rather than rolled
    up across a delegation tree, and how it still handles the agent-transfer
    case where one invocation spans more than one agent). Defaults to an
    empty dict so a schema_version 1 or 2 snapshot file (written before this
    field existed) still deserializes via ``SnapshotRecord(**r)`` with no
    KeyError -- matching the exact precedent ``session_id``/``eval_case_id``
    already established for additive fields on this dataclass."""


@dataclass(frozen=True)
class SnapshotSkip:
    """An invocation this snapshot could not price, and why -- see module
    docstring. Never silently dropped without a reason.
    """

    invocation_id: str
    reason: str
    eval_case_id: str | None = None
    """Same resolution as ``SnapshotRecord.eval_case_id`` (populated only
    when ``--eval-history`` was used and this invocation's ``session_id``
    was found in that map) -- additive, defaults to ``None`` so a
    pre-completeness-check snapshot file still deserializes via
    ``SnapshotSkip(**s)`` with no KeyError. Needed so a SKIPPED-but-
    accounted-for invocation (unresolved model, streaming anomaly, ...)
    still counts toward its eval case's observed total for the
    completeness check below -- a skip is not the same claim as "this
    case never ran," and must not be double-counted as missing on top of
    already being counted as skipped."""


@dataclass
class Snapshot:
    """A baseline or current run's full priced-invocation distribution."""

    schema_version: int
    created_at: str
    records: list[SnapshotRecord] = field(default_factory=list)
    skipped: list[SnapshotSkip] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    """Expected ``eval_case_id``s (from ``--eval-set-file``, scoped to the
    requested case subset) that matched ZERO observed record or skip --
    i.e. that eval case produced no invocation at all. Populated only when
    ``build_snapshot``/``write_snapshot`` was given ``expected_case_sizes``
    (``adk-tracegauge snapshot --eval-set-file`` was used, alongside
    ``--eval-history`` -- see ``evaluate_completeness``'s docstring for why
    both are required together); ``[]`` otherwise, including for every
    pre-completeness-check snapshot file. This is NOT the same statement as
    a nonempty ``skipped`` list: a skip means "this invocation ran and was
    captured, but could not be priced"; a missing case means "this case
    produced no captured invocation, priced or not, at all.
    """

    def costs(self) -> list[float]:
        """The per-invocation cost_usd values -- the sample adk-tracegauge check's
        (two-sample mode) bootstrap gate runs its statistics over."""
        return [r.cost_usd for r in self.records]

    def costs_by_session_id(self, agent: str | None = None) -> dict[str, float]:
        """Per-``session_id`` TOTAL cost_usd, summing every record that
        shares a session_id (an eval case can span more than one invocation
        -- e.g. a multi-turn conversation -- so a "case's cost" is the sum
        across its invocations, not any single one). Records with
        ``session_id is None`` (no session was ever captured for them -- see
        ``SnapshotRecord.session_id``'s docstring) are excluded entirely,
        since ``None`` is not a real, comparable pairing key. Used by
        ``pair_costs_by_session_id`` for ``adk-tracegauge check --mode paired``.

        ``agent`` (LL2.3, optional): when given, sums
        ``record.cost_by_agent.get(agent, 0.0)`` instead of ``record.cost_usd``
        -- the per-session total for JUST that agent's own calls, so
        ``adk-tracegauge check --agent <name>`` gets the same paired-mode
        statistical power (Phase 4 R2/Phase 7 U1) as the unscoped gate,
        rather than being silently restricted to two-sample.
        """
        totals: dict[str, float] = {}
        for record in self.records:
            if record.session_id is None:
                continue
            amount = record.cost_by_agent.get(agent, 0.0) if agent is not None else record.cost_usd
            totals[record.session_id] = totals.get(record.session_id, 0.0) + amount
        return totals

    def costs_by_eval_case_id(self, agent: str | None = None) -> dict[str, float]:
        """Per-``eval_case_id`` TOTAL cost_usd, summing every record that
        shares an eval_case_id -- same summing rationale as
        ``costs_by_session_id`` (an eval case can span more than one
        invocation). Records with ``eval_case_id is None`` (not resolved --
        see ``SnapshotRecord.eval_case_id``'s docstring) are excluded
        entirely. Used by ``resolve_pairing`` for ``adk-tracegauge check --mode
        paired``'s PRIMARY pairing key as of Phase 4 R2.

        ``agent`` (LL2.3, optional): see ``costs_by_session_id``'s docstring
        -- identical per-agent scoping, keyed by eval_case_id instead.
        """
        totals: dict[str, float] = {}
        for record in self.records:
            if record.eval_case_id is None:
                continue
            amount = record.cost_by_agent.get(agent, 0.0) if agent is not None else record.cost_usd
            totals[record.eval_case_id] = totals.get(record.eval_case_id, 0.0) + amount
        return totals

    def costs_for_agent(self, agent_name: str) -> list[float]:
        """The per-invocation cost distribution for JUST the invocations
        ``agent_name`` actually participated in -- feeds ``adk-tracegauge check
        --agent <name>``'s (LL2.3) TWO-SAMPLE mode the same way ``costs()``
        feeds the unscoped case.

        Deliberately FILTERS to records where ``agent_name in
        record.cost_by_agent`` rather than zero-padding every other agent's
        (or an old schema_version 1/2 file's agent-less) record to ``0.0``:
        this is a raw list of individual observations, not a sum, and a
        two-sample bootstrap treats every list entry as one real data point
        -- padding in a fake ``0.0`` for each invocation ``agent_name`` had
        no part in would inflate the apparent sample size and pull the
        distribution's mean and CI toward zero, corrupting the exact
        statistics the gate is supposed to test. (Contrast
        ``costs_by_session_id(agent=...)``/``costs_by_eval_case_id(agent=...)``,
        used by PAIRED mode: those legitimately sum ``0.0`` contributions
        from other invocations in the same group into one session/eval-case
        TOTAL, which is a correct sum, not a padded sample.)
        """
        return [r.cost_by_agent[agent_name] for r in self.records if agent_name in r.cost_by_agent]


def build_snapshot(
    store: UsageStore,
    *,
    prices: dict[str, Any] | None = None,
    eval_case_ids_by_session: dict[str, str] | None = None,
    expected_case_sizes: dict[str, int] | None = None,
) -> Snapshot:
    """Builds a Snapshot from a live UsageStore's currently-captured calls.

    Uses the exact same pricing path as ``CostEfficiencyEvaluator``
    (``build_session_digest`` -> ``_adapter.price_digest``, with ``prices``
    required and never omitted -- see ``_adapter.price_digest``'s docstring
    for why omitting it is a real historical bug, not a style preference)
    so a per-call dollar figure always matches what the eval metric itself
    would have computed for the same captured calls.

    Deliberately one record per RAW ``invocation_id`` (``store.get``, not
    ``store.get_with_descendants``) -- not the parent-rolled-up total
    ``CostEfficiencyEvaluator`` reports for a top-level invocation with
    sub-agent delegation. Rolling descendants into their parent here would
    double-count: iterating every invocation_id in the store and rolling
    each one up separately prices a delegated sub-agent's calls twice (once
    under its own row, once again folded into its parent's row), and
    UsageStore does not publicly expose "is this id someone's recorded
    child" to filter them back out. A regression gate over raw per-call
    invocation costs is still a real, meaningful distribution to drift-test
    -- callers who specifically need parent-rolled-up totals should snapshot
    the pre-rollup figures from their own harness instead.

    ``eval_case_ids_by_session`` (Phase 4 R2, optional): a ``{session_id:
    eval_id}`` map -- typically from ``_compat.load_eval_case_ids_by_session_id``
    against ADK's own persisted eval-history file (see module docstring) --
    used to populate each record's ``eval_case_id`` by looking up its
    captured ``session_id``. A record whose ``session_id`` is ``None`` or
    absent from the map gets ``eval_case_id=None`` (fails soft here, not
    closed -- an invocation this package couldn't join is still a real,
    prices-correctly invocation; it just can't participate in eval-case-id
    paired mode, and session_id/two-sample fallback still cover it).

    ``expected_case_sizes`` (optional): a ``{eval_id: expected_invocation_count}``
    map -- from ``_compat.load_expected_case_sizes`` against the ORIGINAL
    eval-set file, already scoped by the caller to whichever case subset
    was actually requested for this run -- used to populate the returned
    ``Snapshot.missing``: every key with zero matching ``eval_case_id``
    among ``records``/``skipped`` combined. Requires
    ``eval_case_ids_by_session`` to also be given (an expected case can
    only be matched against an OBSERVED one via the same session_id join
    that resolves ``eval_case_id`` in the first place) -- see
    ``evaluate_completeness``'s docstring for the full reasoning and
    ``_cli.py``'s ``--eval-set-file``/``--eval-history`` flags, which
    enforce this pairing at the CLI boundary.
    """
    if prices is None:
        prices = load_gemini_prices()
    if eval_case_ids_by_session is None:
        eval_case_ids_by_session = {}

    records: list[SnapshotRecord] = []
    skipped: list[SnapshotSkip] = []

    for invocation_id in store.invocation_ids():
        calls = store.get(invocation_id)
        if not calls:
            continue

        adapted = build_session_digest(invocation_id, calls)
        if not adapted.ok:
            reason = (
                adapted.unresolved_model
                or adapted.streaming_anomaly
                or adapted.unpriced_component
                or "unknown adaptation failure"
            )
            skip_session_id = store.session_id(invocation_id)
            skipped.append(
                SnapshotSkip(
                    invocation_id=invocation_id,
                    reason=reason,
                    eval_case_id=(
                        eval_case_ids_by_session.get(skip_session_id)
                        if skip_session_id is not None
                        else None
                    ),
                )
            )
            continue

        digest = adapted.digest
        assert digest is not None  # adapted.ok guarantees this; narrows for mypy.
        session_cost = price_digest(digest, prices=prices)
        session_id = store.session_id(invocation_id)

        # LL2: group each turn's priced cost by the agent that made it.
        # turn_index is the stable join key between session_cost.turn_costs
        # (pure dollar arithmetic, _cost.py) and adapted.agent_names_by_turn
        # (parallel, same order/length as digest.turns -- see AdaptResult's
        # docstring for why agent attribution was kept out of _cost.py
        # itself). A turn whose agent_name is "" (unresolved -- see
        # CapturedCall.agent_name's docstring) contributes to no key, rather
        # than being attributed under a misleading "" bucket.
        cost_by_agent: dict[str, float] = {}
        for turn_cost in session_cost.turn_costs:
            agent_name = (
                adapted.agent_names_by_turn[turn_cost.turn_index]
                if turn_cost.turn_index < len(adapted.agent_names_by_turn)
                else ""
            )
            if not agent_name:
                continue
            cost_by_agent[agent_name] = cost_by_agent.get(agent_name, 0.0) + turn_cost.total_usd

        records.append(
            SnapshotRecord(
                invocation_id=invocation_id,
                cost_usd=session_cost.total_usd,
                tokens_input=sum(t.token_count_input for t in digest.turns),
                tokens_output=sum(t.token_count_output for t in digest.turns),
                tokens_cache_read=sum(t.cache_read for t in digest.turns),
                models=sorted({t.model for t in digest.turns}),
                call_count=len(digest.turns),
                session_id=session_id,
                eval_case_id=(
                    eval_case_ids_by_session.get(session_id) if session_id is not None else None
                ),
                cost_by_agent=cost_by_agent,
            )
        )

    missing: list[str] = []
    if expected_case_sizes is not None:
        observed_case_ids = {r.eval_case_id for r in records if r.eval_case_id is not None} | {
            s.eval_case_id for s in skipped if s.eval_case_id is not None
        }
        missing = sorted(
            eval_id for eval_id in expected_case_sizes if eval_id not in observed_case_ids
        )

    return Snapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        records=records,
        skipped=skipped,
        missing=missing,
    )


def write_snapshot(
    store: UsageStore,
    output_path: str | Path,
    *,
    prices: dict[str, Any] | None = None,
    eval_case_ids_by_session: dict[str, str] | None = None,
    expected_case_sizes: dict[str, int] | None = None,
) -> Snapshot:
    """Builds a Snapshot from ``store`` and writes it to ``output_path`` as JSON.

    Returns the built Snapshot as well, so a caller (e.g. the ``adk-tracegauge
    snapshot`` CLI subcommand) can report record/skip counts without
    re-reading the file it just wrote. ``eval_case_ids_by_session`` and
    ``expected_case_sizes`` are forwarded to ``build_snapshot`` unchanged --
    see its docstring.
    """
    snapshot = build_snapshot(
        store,
        prices=prices,
        eval_case_ids_by_session=eval_case_ids_by_session,
        expected_case_sizes=expected_case_sizes,
    )
    path = Path(output_path)
    path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")
    return snapshot


def read_snapshot(path: str | Path) -> Snapshot:
    """Reads a Snapshot previously written by ``write_snapshot``.

    Raises ``ValueError`` on a schema_version this version of adk-tracegauge
    doesn't know how to read, rather than silently misinterpreting a future
    (or malformed) file's fields. Accepts BOTH schema_version 1 and 2 (see
    ``SNAPSHOT_SCHEMA_VERSION``'s docstring for why 1 remains fully readable
    -- it just never carries ``eval_case_id``, which correctly falls through
    to session_id/two-sample fallback in ``resolve_pairing``).
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_version = raw.get("schema_version")
    if schema_version not in _READABLE_SCHEMA_VERSIONS:
        raise ValueError(
            f"{path}: unsupported snapshot schema_version {schema_version!r} "
            f"(this version of adk-tracegauge reads schema_version(s) "
            f"{_READABLE_SCHEMA_VERSIONS} only)"
        )
    return Snapshot(
        schema_version=schema_version,
        created_at=raw.get("created_at", "unknown"),
        records=[SnapshotRecord(**r) for r in raw.get("records", [])],
        skipped=[SnapshotSkip(**s) for s in raw.get("skipped", [])],
        missing=list(raw.get("missing", [])),
    )


CompletenessStatus = Literal["complete", "incomplete_capture", "wrong_eval_set"]


@dataclass(frozen=True)
class CompletenessResult:
    """Whether a snapshot's own sample is complete relative to what an
    eval-set file said should have run -- see ``evaluate_completeness``'s
    docstring for what this checks and, just as importantly, what it
    doesn't claim to check.
    """

    status: CompletenessStatus
    expected_case_count: int
    matched_case_count: int
    expected_invocation_count: int
    observed_invocation_count: int
    missing: list[str]

    def report(self) -> str:
        """A one-paragraph, human-readable summary -- printed by
        `adk-tracegauge snapshot` alongside the record/skip counts it
        already reports, and worth keeping consistent with that existing
        one-line-summary style rather than a separate multi-line block.
        """
        if self.status == "complete":
            return (
                f"adk-tracegauge completeness: {self.matched_case_count}/"
                f"{self.expected_case_count} expected eval case(s) accounted for, "
                f"{self.observed_invocation_count}/{self.expected_invocation_count} "
                "expected invocation(s) captured -- sample is complete; the "
                "regression gate's achieved-power figures reflect the full "
                "requested sample, not a silently shortened one."
            )
        if self.status == "wrong_eval_set":
            return (
                "adk-tracegauge completeness: WRONG_EVAL_SET -- 0/"
                f"{self.expected_case_count} expected eval case IDs from "
                "--eval-set-file matched ANY captured record or skip, though "
                f"{self.observed_invocation_count} invocation(s) were captured. "
                "This is not evidence of a dropped case -- it means "
                "--eval-set-file almost certainly does not describe the run "
                "that produced this snapshot (wrong file, or a stale file "
                "from a different eval set). A completeness verdict is not "
                "meaningful against the wrong ground truth; fix --eval-set-file "
                "and re-run before trusting either this snapshot's completeness "
                "status or any regression gate built on it."
            )
        # incomplete_capture
        missing_note = f" Missing entirely: {', '.join(self.missing)}." if self.missing else ""
        return (
            f"adk-tracegauge completeness: INCOMPLETE_CAPTURE -- "
            f"{self.observed_invocation_count}/{self.expected_invocation_count} expected "
            f"invocation(s) captured across {self.matched_case_count}/"
            f"{self.expected_case_count} expected eval case(s).{missing_note} This "
            "snapshot's sample is shorter than the eval set defines -- any "
            "regression gate run against it has less statistical power than "
            "its achieved-power figure would otherwise reflect, computed over "
            "an incomplete n with no signal that it's incomplete unless this "
            "check is run. This is not a claim about why the sample is short "
            "-- only that it is."
        )


def evaluate_completeness(
    snapshot: Snapshot,
    expected_case_sizes: dict[str, int],
    *,
    num_runs: int = 1,
) -> CompletenessResult:
    """Checks whether ``snapshot``'s own captured sample is COMPLETE relative
    to ``expected_case_sizes`` -- NOT a defect detector, a validity
    precondition on this package's own statistical output.

    ``adk-tracegauge check``'s achieved-power figure (see ``_regression.py``)
    is only a meaningful statement about what the gate could reliably detect
    if the sample it ran over is the sample it was supposed to run over.
    Nothing else in this package can currently tell a genuinely complete `n`
    apart from one that was silently shortened somewhere upstream -- a
    dropped eval case looks, from this package's own vantage point, exactly
    like an eval set that legitimately has fewer cases. This function is the
    one place that distinction gets made, by comparing against an
    independent source of "how many invocations were requested" -- the
    eval-set file itself, not anything this pipeline itself produced (see
    ``_compat.load_expected_case_sizes``'s docstring for why the pipeline's
    OWN result file cannot serve as that independent source: it would be
    checking the pipeline's output for completeness using the pipeline's own
    claim about what it did, which agrees with a silent drop as readily as
    with a complete run).

    This does NOT diagnose *why* a sample came up short -- a genuinely
    dropped case (the condition this exists to catch), a legitimate
    subset run whose subset wasn't correctly reflected in
    ``expected_case_sizes``, or a caller-side bug in how ``num_runs``/the
    requested case list were computed all produce the same
    ``incomplete_capture`` signal. It also does not claim ADK itself is
    defective -- only that THIS snapshot's own `n` is short of what was
    asked for, which is exactly the fact ``adk-tracegauge check`` needs and
    currently has no way to learn on its own.

    ``expected_case_sizes`` must already be scoped by the caller to
    whichever case subset was actually requested for this run (a
    `case1,case2`-style CLI subset must not be compared against the FULL
    eval-set file's case count -- that would flag every legitimate subset
    run as incomplete). Raises ``ValueError`` if empty -- an empty expected
    set is a caller misconfiguration (no cases were resolved as
    "requested" at all), not a meaningful zero to report a verdict against.

    Status, in priority order:

    - ``wrong_eval_set``: zero of the expected case IDs matched ANY
      observed record or skip, despite at least one invocation being
      captured. This is the WRONG-FILE guard -- ``--eval-set-file``
      introduces a failure mode ``--eval-history`` alone doesn't have: a
      wrong or stale file yields confident nonsense (every case looks
      "missing" even though the run was fine) rather than a clear error.
      Total non-overlap between "expected" and "observed" is the signal
      that the file itself, not the run, is the problem -- reported as
      its own distinct status precisely so it is never confused with a
      real dropped case. (An empty snapshot -- zero invocations captured
      at all -- does NOT trigger this: with no observed data whatsoever,
      there is no basis to conclude the file is wrong rather than the run
      having genuinely produced nothing; that case falls through to
      ``incomplete_capture`` instead, where it belongs.)
    - ``incomplete_capture``: observed invocation count is below the
      expected count, and it isn't a wrong-file situation. ``missing``
      names every expected case ID that matched zero observed record or
      skip -- the direct, catchable shape (a case silently dropped
      entirely). A case that partially under-produced (some but not all
      of its expected turns captured) still lowers the aggregate count
      that triggers this status, but is not itself named in ``missing``,
      which is case-existence, not per-case turn-completeness.
    - ``complete``: observed count meets or exceeds expected, and every
      expected case matched at least one observed record or skip.
    """
    if not expected_case_sizes:
        raise ValueError(
            "evaluate_completeness requires at least one expected eval case -- an "
            "empty expected_case_sizes means nothing was resolved as 'requested' for "
            "this run, which is a caller misconfiguration, not a meaningful zero to "
            "report a completeness verdict against."
        )

    expected_case_count = len(expected_case_sizes)
    expected_invocation_count = sum(expected_case_sizes.values()) * num_runs
    observed_invocation_count = len(snapshot.records) + len(snapshot.skipped)
    matched_case_count = expected_case_count - len(snapshot.missing)

    if expected_case_count > 0 and matched_case_count == 0 and observed_invocation_count > 0:
        status: CompletenessStatus = "wrong_eval_set"
    elif observed_invocation_count < expected_invocation_count:
        status = "incomplete_capture"
    else:
        status = "complete"

    return CompletenessResult(
        status=status,
        expected_case_count=expected_case_count,
        matched_case_count=matched_case_count,
        expected_invocation_count=expected_invocation_count,
        observed_invocation_count=observed_invocation_count,
        missing=list(snapshot.missing),
    )


def pair_costs_by_session_id(
    baseline: Snapshot, current: Snapshot, agent: str | None = None
) -> tuple[list[float], list[float], list[str]]:
    """Builds the ALIGNED (baseline_costs, current_costs) lists Phase 3 B4's
    ``adk-tracegauge check --mode paired`` needs, by matching each snapshot's
    ``costs_by_session_id()`` on the session_ids present in BOTH -- session
    ids present in only one snapshot are silently excluded (a case that ran
    in one snapshot but not the other has nothing to pair against; this is
    not an error, just not a pairable observation).

    Returns ``(baseline_costs, current_costs, matched_session_ids)``, all
    three the same length and index-aligned, sorted by session_id for
    determinism (so re-running against the identical two files always
    produces the identical ordering, independent of dict iteration order).

    ``agent`` (LL2.3, optional): forwarded to ``costs_by_session_id`` on both
    snapshots -- pairs each session's per-agent cost, not its total.
    """
    baseline_by_session = baseline.costs_by_session_id(agent=agent)
    current_by_session = current.costs_by_session_id(agent=agent)
    matched = sorted(set(baseline_by_session) & set(current_by_session))
    baseline_costs = [baseline_by_session[k] for k in matched]
    current_costs = [current_by_session[k] for k in matched]
    return baseline_costs, current_costs, matched


def pair_costs_by_eval_case_id(
    baseline: Snapshot, current: Snapshot, agent: str | None = None
) -> tuple[list[float], list[float], list[str]]:
    """Same alignment as ``pair_costs_by_session_id``, keyed on
    ``eval_case_id`` instead -- Phase 4 R2's PRIMARY pairing key. See
    ``Snapshot.costs_by_eval_case_id`` and the module docstring's fallback
    chain. ``agent`` (LL2.3, optional): see ``pair_costs_by_session_id``."""
    baseline_by_case = baseline.costs_by_eval_case_id(agent=agent)
    current_by_case = current.costs_by_eval_case_id(agent=agent)
    matched = sorted(set(baseline_by_case) & set(current_by_case))
    baseline_costs = [baseline_by_case[k] for k in matched]
    current_costs = [current_by_case[k] for k in matched]
    return baseline_costs, current_costs, matched


PairingKey = Literal["eval_case_id", "session_id", "none"]


def resolve_pairing(
    baseline: Snapshot, current: Snapshot, agent: str | None = None
) -> tuple[list[float], list[float], list[str], PairingKey]:
    """Implements Phase 4 R2's fallback chain -- the SINGLE place that
    decides which pairing key ``adk-tracegauge check --mode {auto,paired}``
    actually uses, so the decision is made once and always reported (never
    silently chosen -- see ``_cli.py``, which prints the returned
    ``PairingKey`` on every paired-mode run).

    1. ``eval_case_id`` -- used whenever it has ANY overlap between the two
       snapshots (even a single matched case), since it is the more stable,
       more trustworthy key when available at all (authored in the
       .evalset.json, never regenerated -- see module docstring).
    2. ``session_id`` -- used when eval_case_id has zero overlap but
       session_id does (B4's original mechanism, unchanged).
    3. ``"none"`` -- neither key has any overlap; returns empty lists. The
       caller (``_cli.py``) is responsible for falling back to two-sample
       (``auto`` mode) or failing closed with an actionable error (explicit
       ``--mode paired``).

    Returns ``(baseline_costs, current_costs, matched_keys, resolved_key)``,
    all index-aligned and sorted by key for determinism, exactly like
    ``pair_costs_by_session_id``/``pair_costs_by_eval_case_id`` individually.

    ``agent`` (LL2.3, optional): forwarded to both pairing functions --
    matched keys/overlap counts are computed on session_id/eval_case_id
    presence exactly as before (agent-scoping never changes WHICH cases
    pair, only the dollar figure paired for each), only the dollar values
    paired change to that agent's own cost.
    """
    ec_baseline, ec_current, ec_matched = pair_costs_by_eval_case_id(baseline, current, agent=agent)
    if ec_matched:
        return ec_baseline, ec_current, ec_matched, "eval_case_id"

    s_baseline, s_current, s_matched = pair_costs_by_session_id(baseline, current, agent=agent)
    if s_matched:
        return s_baseline, s_current, s_matched, "session_id"

    return [], [], [], "none"


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "CompletenessResult",
    "CompletenessStatus",
    "PairingKey",
    "Snapshot",
    "SnapshotRecord",
    "SnapshotSkip",
    "build_snapshot",
    "evaluate_completeness",
    "pair_costs_by_eval_case_id",
    "pair_costs_by_session_id",
    "read_snapshot",
    "resolve_pairing",
    "write_snapshot",
]
