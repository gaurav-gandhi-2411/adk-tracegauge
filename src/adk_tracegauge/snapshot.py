"""adk_tracegauge/snapshot.py — Persist a UsageStore's per-invocation cost
distribution to disk, and read it back.

Nothing else in this package persists a ``UsageStore``'s captured data --
it's process-only, built up live during an eval/agent run. The Phase 2 W4
CI regression gate (``tracegauge check``, see ``_cli.py``) needs to compare
TWO such distributions (a saved baseline, and the current run) across
separate process invocations (a baseline captured on a past CI run or
committed to the repo; the current run's own fresh process) -- so this
module defines the on-disk format and the two functions that cross that
boundary: ``write_snapshot`` (in-process UsageStore -> JSON file, run at the
end of an eval script) and ``read_snapshot`` (JSON file -> Snapshot, run by
the ``check`` CLI subcommand, which never needs a live UsageStore at all).

Snapshot JSON schema (schema_version=1)::

    {
      "schema_version": 1,
      "created_at": "2026-08-14T12:00:00+00:00",
      "records": [
        {
          "invocation_id": "e-1234...",
          "cost_usd": 0.004231,
          "tokens_input": 512,
          "tokens_output": 128,
          "tokens_cache_read": 0,
          "models": ["gemini-2.5-flash"],
          "call_count": 1
        },
        ...
      ],
      "skipped": [
        {"invocation_id": "e-5678...", "reason": "cost not computed: ..."}
      ]
    }

One record per invocation that could be priced (same fail-closed pricing
path as ``CostEfficiencyEvaluator`` -- an invocation whose model doesn't
resolve, or whose streamed chunks fail the monotonicity check, or that
carries an unpriced token category, is never fabricated a cost; it is
recorded under ``skipped`` with the reason instead, and excluded from the
``records`` list ``tracegauge check`` runs its statistics over). This keeps
a single unpriceable invocation from poisoning or silently dropping an
entire snapshot -- the caller can see exactly what was skipped and why.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._adapter import build_session_digest, price_digest
from ._pricing import load_gemini_prices
from ._store import UsageStore

SNAPSHOT_SCHEMA_VERSION = 1


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


@dataclass(frozen=True)
class SnapshotSkip:
    """An invocation this snapshot could not price, and why -- see module
    docstring. Never silently dropped without a reason.
    """

    invocation_id: str
    reason: str


@dataclass
class Snapshot:
    """A baseline or current run's full priced-invocation distribution."""

    schema_version: int
    created_at: str
    records: list[SnapshotRecord] = field(default_factory=list)
    skipped: list[SnapshotSkip] = field(default_factory=list)

    def costs(self) -> list[float]:
        """The per-invocation cost_usd values -- the sample tracegauge check's
        bootstrap gate runs its statistics over."""
        return [r.cost_usd for r in self.records]


def build_snapshot(store: UsageStore, *, prices: dict[str, Any] | None = None) -> Snapshot:
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
    """
    if prices is None:
        prices = load_gemini_prices()

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
            skipped.append(SnapshotSkip(invocation_id=invocation_id, reason=reason))
            continue

        digest = adapted.digest
        assert digest is not None  # adapted.ok guarantees this; narrows for mypy.
        session_cost = price_digest(digest, prices=prices)

        records.append(
            SnapshotRecord(
                invocation_id=invocation_id,
                cost_usd=session_cost.total_usd,
                tokens_input=sum(t.token_count_input for t in digest.turns),
                tokens_output=sum(t.token_count_output for t in digest.turns),
                tokens_cache_read=sum(t.cache_read for t in digest.turns),
                models=sorted({t.model for t in digest.turns}),
                call_count=len(digest.turns),
            )
        )

    return Snapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        created_at=datetime.now(UTC).isoformat(),
        records=records,
        skipped=skipped,
    )


def write_snapshot(
    store: UsageStore, output_path: str | Path, *, prices: dict[str, Any] | None = None
) -> Snapshot:
    """Builds a Snapshot from ``store`` and writes it to ``output_path`` as JSON.

    Returns the built Snapshot as well, so a caller (e.g. the ``tracegauge
    snapshot`` CLI subcommand) can report record/skip counts without
    re-reading the file it just wrote.
    """
    snapshot = build_snapshot(store, prices=prices)
    path = Path(output_path)
    path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")
    return snapshot


def read_snapshot(path: str | Path) -> Snapshot:
    """Reads a Snapshot previously written by ``write_snapshot``.

    Raises ``ValueError`` on a schema_version this version of adk-tracegauge
    doesn't know how to read, rather than silently misinterpreting a future
    (or malformed) file's fields.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_version = raw.get("schema_version")
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unsupported snapshot schema_version {schema_version!r} "
            f"(this version of adk-tracegauge reads schema_version "
            f"{SNAPSHOT_SCHEMA_VERSION} only)"
        )
    return Snapshot(
        schema_version=schema_version,
        created_at=raw.get("created_at", "unknown"),
        records=[SnapshotRecord(**r) for r in raw.get("records", [])],
        skipped=[SnapshotSkip(**s) for s in raw.get("skipped", [])],
    )


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "Snapshot",
    "SnapshotRecord",
    "SnapshotSkip",
    "build_snapshot",
    "read_snapshot",
    "write_snapshot",
]
