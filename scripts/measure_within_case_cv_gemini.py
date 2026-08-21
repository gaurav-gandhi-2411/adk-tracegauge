"""scripts/measure_within_case_cv_gemini.py — AD2.3/AL2.3: within-case CV
representativeness check for measure_within_case_cv_ollama.py's Q1
measurement, which the shipped paired-mode default's power depends on.

Identical design to measure_within_case_cv_ollama.py (same evalset, same
duplicate-measurement pooled-repeatability estimator, same A/B session_id
scheme) with only the model swapped from local Ollama to a real hosted
call. See that script's module docstring for the full statistical design
rationale (Var(Delta_i) = 2*sigma_i^2 for independent draws, pooled CV,
why skewness is not estimable from 2 repeats).

Model: gemini-3.5-flash-lite, not gemini-2.5-flash-lite (AD2.3/AL2's
originally named model) -- 2.5-flash-lite returned a 404 ("no longer
available to new users") when directly re-checked in this session,
identical to the earlier AD2.3 across-case run this session. Same
already-authorized substitute, for consistency with that existing
across-case measurement (reports/ad2_real_cv_measurement_gemini.json) --
this script fills in the missing within-case half using the same model.

**Costs real money** -- 72 real hosted calls (36 cases x 2 passes). Real
bundled price table used (gemini-3.5-flash-lite: $0.30/$2.50 per Mtok),
no synthetic override needed.

Run: ``uv run python scripts/measure_within_case_cv_gemini.py``
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

# Free-tier quota for gemini-3.5-flash-lite is 15 requests/minute (discovered
# live via a 429 RESOURCE_EXHAUSTED mid-run). Throttle proactively to stay
# under it, plus a reactive retry-with-backoff honoring the server's own
# suggested retryDelay if a 429 still slips through (rule 108: every
# outbound call gets a timeout, retry with backoff, and a defined failure
# behavior -- this was the gap the mid-run crash exposed).
MIN_SECONDS_BETWEEN_CALLS = 4.5
MAX_RETRIES = 5

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

GEMINI_MODEL = "gemini-3.5-flash-lite"
EVALSET_PATH = Path(__file__).resolve().parent.parent / "reports" / "ad2_evalset.json"

from google.adk.agents.llm_agent import LlmAgent  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from adk_tracegauge._plugin import TraceGaugeUsagePlugin  # noqa: E402
from adk_tracegauge._store import UsageStore  # noqa: E402
from adk_tracegauge.snapshot import read_snapshot, write_snapshot  # noqa: E402

_last_call_ts: float = 0.0


async def _throttle() -> None:
    global _last_call_ts
    now = time.monotonic()
    wait = MIN_SECONDS_BETWEEN_CALLS - (now - _last_call_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call_ts = time.monotonic()


async def _run_case(runner: InMemoryRunner, app_name: str, session_id: str, prompt: str) -> None:
    user_id = "u"
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    new_message = genai_types.Content(parts=[genai_types.Part(text=prompt)], role="user")
    for attempt in range(MAX_RETRIES):
        await _throttle()
        try:
            async for _event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=new_message
            ):
                pass
            return
        except Exception as e:  # noqa: BLE001 -- real 429s surface as google.genai.errors.ClientError
            is_last = attempt == MAX_RETRIES - 1
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                backoff = 60.0 * (attempt + 1)
                print(
                    f"    429 rate-limited on {session_id}, attempt {attempt + 1}/{MAX_RETRIES}, sleeping {backoff:.0f}s...",
                    flush=True,
                )
                if is_last:
                    raise
                await asyncio.sleep(backoff)
                continue
            raise


async def _run_pass(cases: list[dict], pass_label: str) -> UsageStore:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    app_name = f"al2_within_case_probe_gemini_{pass_label}"
    agent = LlmAgent(
        name=app_name,
        model=GEMINI_MODEL,
        instruction="Answer the user's question directly and completely.",
        after_model_callback=plugin.after_model_callback,  # type: ignore[arg-type]
    )
    runner = InMemoryRunner(agent=agent, app_name=app_name, plugins=[plugin])
    for i, case in enumerate(cases):
        session_id = f"{case['id']}-{pass_label}"
        print(f"  [{pass_label} {i + 1}/{len(cases)}] {case['id']} ({case['tier']})...", flush=True)
        await _run_case(runner, app_name, session_id, case["prompt"])
    return store


def _costs_by_case(store: UsageStore, pass_label: str) -> tuple[dict[str, float], list[dict]]:
    tmp = Path(tempfile.gettempdir()) / f"al2_gemini_snapshot_{pass_label}.json"
    write_snapshot(store, tmp)
    snap = read_snapshot(tmp)
    out: dict[str, float] = {}
    for r in snap.records:
        assert r.session_id is not None
        case_id = r.session_id.rsplit(f"-{pass_label}", 1)[0]
        out[case_id] = r.cost_usd
    return out, [{"invocation_id": s["invocation_id"], "reason": s["reason"]} for s in snap.skipped]


def main() -> int:
    evalset = json.loads(EVALSET_PATH.read_text(encoding="utf-8"))
    cases = evalset["cases"]
    print(
        f"Running {len(cases)} cases TWICE (runA, runB) against {GEMINI_MODEL} (REAL hosted call, costs money)..."
    )

    store_a = asyncio.run(_run_pass(cases, "runA"))
    costs_a, skipped_a = _costs_by_case(store_a, "runA")
    store_b = asyncio.run(_run_pass(cases, "runB"))
    costs_b, skipped_b = _costs_by_case(store_b, "runB")

    case_ids = [c["id"] for c in cases]
    missing = [cid for cid in case_ids if cid not in costs_a or cid not in costs_b]
    if missing:
        print(f"WARNING: {len(missing)} case(s) missing a priced record in one pass: {missing}")
    paired_ids = [cid for cid in case_ids if cid in costs_a and cid in costs_b]

    a_vals = [costs_a[cid] for cid in paired_ids]
    b_vals = [costs_b[cid] for cid in paired_ids]
    deltas = [b - a for a, b in zip(a_vals, b_vals, strict=True)]
    all_vals = a_vals + b_vals
    total_spend = sum(all_vals)

    n = len(paired_ids)
    mean_delta = statistics.fmean(deltas)
    mean_cost = statistics.fmean(all_vals)

    mean_sq_delta = statistics.fmean([d**2 for d in deltas])
    pooled_within_case_var = mean_sq_delta / 2.0
    pooled_within_case_sd = pooled_within_case_var**0.5
    within_case_cv = pooled_within_case_sd / mean_cost if mean_cost != 0.0 else float("nan")

    delta_sd = statistics.stdev(deltas) if n > 1 else float("nan")
    bias_t_stat = (
        (mean_delta / (delta_sd / (n**0.5))) if n > 1 and delta_sd != 0.0 else float("nan")
    )

    print(f"\n=== Within-case measurement (n={n} matched cases, model={GEMINI_MODEL}) ===")
    print(f"TOTAL REAL SPEND: ${total_spend:.6f}")
    print(f"mean(cost) across both runs: ${mean_cost:.6f}")
    print(
        f"mean(delta) = mean(runB - runA): ${mean_delta:.6f}  (bias check t-stat: {bias_t_stat:.3f})"
    )
    print(f"pooled within-case sd (duplicate-measurement estimator): ${pooled_within_case_sd:.6f}")
    print(f"within-case CV: {within_case_cv:.4f}")
    print("within-case skewness: NOT ESTIMABLE from 2 repeats/case -- see module docstring.")

    out_path = Path(__file__).resolve().parent.parent / "reports" / "al2_within_case_cv_gemini.json"
    out_path.write_text(
        json.dumps(
            {
                "model": GEMINI_MODEL,
                "evalset_path": str(EVALSET_PATH),
                "n_cases_total": len(cases),
                "n_matched": n,
                "missing_cases": missing,
                "skipped_runA": skipped_a,
                "skipped_runB": skipped_b,
                "total_real_spend_usd": total_spend,
                "runA_costs": costs_a,
                "runB_costs": costs_b,
                "deltas": dict(zip(paired_ids, deltas, strict=True)),
                "mean_cost": mean_cost,
                "mean_delta": mean_delta,
                "bias_check_t_stat": bias_t_stat,
                "pooled_within_case_sd": pooled_within_case_sd,
                "within_case_cv": within_case_cv,
                "within_case_skewness": None,
                "within_case_skewness_note": (
                    "Not estimable from 2 repeats/case -- A-B is symmetric by construction "
                    "regardless of the underlying distribution's skewness."
                ),
                "domain_of_validity": (
                    "One evalset (36 hand-authored cases), one real hosted model "
                    "(gemini-3.5-flash-lite, substituted for the originally-named "
                    "gemini-2.5-flash-lite -- retired for new API keys), two repeats per case."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote measurement to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
