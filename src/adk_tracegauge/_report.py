"""adk_tracegauge/_report.py -- rendering for ``adk-tracegauge report``: turns a ``Snapshot`` into
the per-invocation cost table people in google/adk-python Discussion #97 asked for ("how do I
turn tokens into dollars").

Pure functions over an already-built ``Snapshot`` -- no I/O, no pricing of its own. Every dollar
figure here was priced by ``snapshot.build_snapshot`` through the same fail-closed path as the
eval metric, so this module can only *display* a cost, never invent one.

The one rule that shapes the layout: **an invocation this package could not price is shown as
unknown, never omitted and never given a guessed number** (``Snapshot.skipped`` carries each with
its reason). The total is therefore labelled a *priced* total and says out loud when it excludes
unknowns -- a total that silently left an invocation out would read as complete when it is not.
"""

from __future__ import annotations

from typing import Any

from .snapshot import Snapshot


def _usd(value: float) -> str:
    return f"${value:.6f}"


def _short_id(invocation_id: str, width: int = 14) -> str:
    """Invocation ids are long ``e-<uuid>`` strings; the table needs a stable, readable prefix.
    The untruncated id is always in ``--json``."""
    return invocation_id if len(invocation_id) <= width else invocation_id[:width]


def _describe_reason(reason: str) -> str:
    """``SnapshotSkip.reason`` is either a bare model id (``AdaptResult.unresolved_model``) or an
    already-worded sentence (streaming anomaly / unpriced token category). A bare id on its own
    reads as noise in a cost table, so say what it means; sentences pass through untouched. The
    "no whitespace" test is the discriminator because a model id never contains any."""
    if reason and not any(ch.isspace() for ch in reason):
        return f"model {reason!r} is not in the price table (see README, 'Pricing')"
    return reason


def priced_total_usd(snapshot: Snapshot) -> float:
    return sum(r.cost_usd for r in snapshot.records)


def to_json_dict(snapshot: Snapshot, source: str) -> dict[str, Any]:
    """Machine-readable form. ``total_usd_priced`` is the sum over priced invocations only;
    ``total_is_complete`` is False whenever any invocation is unknown, so a consumer cannot
    mistake the sum for the true total."""
    invocations: list[dict[str, Any]] = []
    for r in snapshot.records:
        invocations.append(
            {
                "status": "priced",
                "invocation_id": r.invocation_id,
                "cost_usd": r.cost_usd,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "tokens_cache_read": r.tokens_cache_read,
                "models": list(r.models),
                "call_count": r.call_count,
                "session_id": r.session_id,
                "eval_case_id": r.eval_case_id,
                "cost_by_agent": dict(r.cost_by_agent),
            }
        )
    for s in snapshot.skipped:
        invocations.append(
            {
                "status": "unknown",
                "invocation_id": s.invocation_id,
                "cost_usd": None,
                "reason": s.reason,
                "eval_case_id": s.eval_case_id,
            }
        )
    return {
        "source": source,
        "schema_version": snapshot.schema_version,
        "created_at": snapshot.created_at,
        "n_invocations": len(invocations),
        "n_priced": len(snapshot.records),
        "n_unknown": len(snapshot.skipped),
        "total_usd_priced": priced_total_usd(snapshot),
        "total_is_complete": not snapshot.skipped,
        "tokens_input_priced": sum(r.tokens_input for r in snapshot.records),
        "tokens_output_priced": sum(r.tokens_output for r in snapshot.records),
        "missing_eval_cases": list(snapshot.missing),
        "invocations": invocations,
    }


def render_text(snapshot: Snapshot, source: str) -> str:
    n_priced, n_unknown = len(snapshot.records), len(snapshot.skipped)
    lines = [
        f"adk-tracegauge report: {n_priced + n_unknown} invocation(s) "
        f"({n_priced} priced, {n_unknown} unknown) -- {source}"
    ]

    show_cache = any(r.tokens_cache_read for r in snapshot.records)
    header = ["invocation", "model(s)", "calls", "tokens in", "tokens out"]
    if show_cache:
        header.append("cache read")
    header.append("cost (USD)")

    rows: list[list[str]] = []
    for r in snapshot.records:
        row = [
            _short_id(r.invocation_id),
            ",".join(r.models),
            str(r.call_count),
            f"{r.tokens_input:,}",
            f"{r.tokens_output:,}",
        ]
        if show_cache:
            row.append(f"{r.tokens_cache_read:,}")
        row.append(_usd(r.cost_usd))
        rows.append(row)
    unknown_notes: list[str] = []
    for s in snapshot.skipped:
        row = [_short_id(s.invocation_id), "UNKNOWN", "-", "-", "-"]
        if show_cache:
            row.append("-")
        row.append("unknown")
        rows.append(row)
        unknown_notes.append(f"  {_short_id(s.invocation_id)}: {_describe_reason(s.reason)}")

    widths = [max(len(header[i]), *(len(row[i]) for row in rows)) for i in range(len(header))]
    # Left-align the text columns, right-align the numeric ones.
    numeric_from = 2

    def fmt(cells: list[str]) -> str:
        parts = [
            cell.ljust(widths[i]) if i < numeric_from else cell.rjust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  " + "  ".join(parts)

    lines.append(fmt(header))
    lines.append("  " + "  ".join("-" * w for w in widths))
    lines.extend(fmt(row) for row in rows)
    lines.append("")

    total = priced_total_usd(snapshot)
    tok_in = sum(r.tokens_input for r in snapshot.records)
    tok_out = sum(r.tokens_output for r in snapshot.records)
    if n_unknown:
        lines.append(
            f"  priced total: {_usd(total)} across {n_priced} invocation(s) -- EXCLUDES "
            f"{n_unknown} unknown invocation(s), so the true total is at least this"
        )
    else:
        lines.append(f"  total: {_usd(total)} across {n_priced} invocation(s)")
    lines.append(f"  tokens (priced invocations): {tok_in:,} in / {tok_out:,} out")
    if unknown_notes:
        lines.append("")
        lines.append("  Unknown invocations were NOT priced (no guessed rate is ever used):")
        lines.extend(unknown_notes)
    if snapshot.missing:
        lines.append("")
        lines.append(f"  eval case(s) expected but never captured: {', '.join(snapshot.missing)}")
    return "\n".join(lines)


EMPTY_MESSAGE = (
    "adk-tracegauge report: 0 invocations in {source} -- nothing was captured. The usual cause "
    "is that TraceGaugeUsagePlugin is not wired into the agent that ran (exactly once: "
    "`plugins=[...]` on the runner/App, or `after_model_callback=` on the agent -- see README, "
    '"See what your ADK agent costs").'
)
"""Printed instead of an empty table: an empty capture is a setup problem, and the table would
just be a header over nothing."""


__all__ = ["EMPTY_MESSAGE", "priced_total_usd", "render_text", "to_json_dict"]
