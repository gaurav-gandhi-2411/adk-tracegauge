"""scripts/measure_real_cv_gemini.py — AD2.3: representativeness check for
measure_real_cv_ollama.py's local-model CV/skew measurement against a real
hosted call.

AD2 (measure_real_cv_ollama.py) measured per-invocation cost CV/skew from a
real `google-adk` agent run against `ollama_chat/qwen2.5:7b`, local and
zero-cost, but flagged (AD2.3) that a local 7B model's output-length
distribution is not established to resemble a real hosted model's -- the
measured CV could overstate, understate, or roughly match real hosted-call
variance, and that script alone cannot distinguish between those.

This script is the AD2.3-prescribed check: the exact same evalset
(`reports/ad2_evalset.json`), the exact same capture pipeline
(`TraceGaugeUsagePlugin` + `UsageStore` + `snapshot`), the exact same
CV/skew computation -- with ONLY the model swapped from local Ollama to a
real hosted call (`gemini-2.5-flash-lite`), per AD2.3 step 1. No synthetic
price table is needed here (unlike the Ollama script): `gemini-2.5-flash-lite`
is already a real, correctly-priced entry in the bundled
`gemini_prices.json` ($0.10/$0.40 per Mtok), so this uses the real bundled
table unmodified -- both the token counts AND the resulting dollar figures
are real for this run, not notional.

**Costs real money.** AD2.3's own sizing estimate: ~$0.0065 total for 36
cases at this model's real published rate. Requires `GOOGLE_API_KEY` (or
`GEMINI_API_KEY`) in the environment. Run only with explicit authorization
-- this is exactly the run AD2.3 routed to GG rather than deciding
unilaterally.

Run: ``uv run python scripts/measure_real_cv_gemini.py``
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

GEMINI_MODEL = "gemini-3.5-flash-lite"
"""gemini-2.5-flash-lite (AD2.3's originally suggested model) returned a 404
-- retired for new API keys as of this run (2026-08-21); Google's own error
message names gemini-3.5-flash-lite as the replacement. Real priced entry
already in the bundled table ($0.30/$2.50 per Mtok, vs 2.5-flash-lite's
$0.10/$0.40) -- confirmed with GG before switching, since it changes both
the model under test and the cost estimate (~6x higher, still <$0.04 total
for the 36-case evalset)."""

EVALSET_PATH = Path(__file__).resolve().parent.parent / "reports" / "ad2_evalset.json"

# Deliberately NOT setting ADK_TRACEGAUGE_PRICE_TABLE (real bundled table
# already has a correct gemini-2.5-flash-lite entry) or
# ADK_TRACEGAUGE_ASSUME_LOCAL (this is a real hosted call, not local).

from google.adk.agents.llm_agent import LlmAgent  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from adk_tracegauge._plugin import TraceGaugeUsagePlugin  # noqa: E402
from adk_tracegauge._store import UsageStore  # noqa: E402
from adk_tracegauge.snapshot import read_snapshot, write_snapshot  # noqa: E402


def _skewness(values: list[float]) -> float:
    """Sample skewness (Fisher-Pearson standardized third moment,
    bias-adjusted -- the same definition ``scipy.stats.skew(bias=False)``
    reports), stdlib-only. Identical to measure_real_cv_ollama.py's."""
    n = len(values)
    if n < 3:
        return float("nan")
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    if sd == 0.0:
        return 0.0
    m3 = sum((x - mean) ** 3 for x in values) / n
    g1 = m3 / (sd**3)
    return math.sqrt(n * (n - 1)) / (n - 2) * g1


async def _run_case(runner: InMemoryRunner, app_name: str, case_id: str, prompt: str) -> None:
    user_id = "u"
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=case_id
    )
    new_message = genai_types.Content(parts=[genai_types.Part(text=prompt)], role="user")
    async for _event in runner.run_async(
        user_id=user_id, session_id=case_id, new_message=new_message
    ):
        pass


async def _run_evalset(cases: list[dict]) -> UsageStore:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    app_name = "ad2_real_cv_probe_gemini"
    agent = LlmAgent(
        name=app_name,
        model=GEMINI_MODEL,
        instruction="Answer the user's question directly and completely.",
        after_model_callback=plugin.after_model_callback,  # type: ignore[arg-type]
    )
    runner = InMemoryRunner(agent=agent, app_name=app_name, plugins=[plugin])
    for i, case in enumerate(cases):
        print(f"  [{i + 1}/{len(cases)}] {case['id']} ({case['tier']})...", flush=True)
        await _run_case(runner, app_name, case["id"], case["prompt"])
    return store


def main() -> int:
    evalset = json.loads(EVALSET_PATH.read_text(encoding="utf-8"))
    cases = evalset["cases"]
    print(f"Running {len(cases)} real cases against {GEMINI_MODEL} (REAL hosted call, costs money)...")

    store = asyncio.run(_run_evalset(cases))

    tmp_path = Path(tempfile.gettempdir()) / "ad2_real_cv_snapshot_gemini.json"
    write_snapshot(store, tmp_path)
    snapshot = read_snapshot(tmp_path)

    print(f"\n{len(snapshot.records)} priced record(s), {len(snapshot.skipped)} skipped.")
    for s in snapshot.skipped:
        print(f"  SKIPPED {s['invocation_id']}: {s['reason']}")

    costs = [r.cost_usd for r in snapshot.records]
    tokens_in = [r.tokens_input for r in snapshot.records]
    tokens_out = [r.tokens_output for r in snapshot.records]
    case_ids = [r.session_id for r in snapshot.records]

    if len(costs) < 3:
        print("Fewer than 3 priced records -- cannot compute CV/skew meaningfully. Stopping.")
        return 1

    mean_cost = statistics.fmean(costs)
    sd_cost = statistics.stdev(costs)
    cv_cost = sd_cost / mean_cost if mean_cost != 0.0 else float("nan")
    skew_cost = _skewness(costs)

    mean_tok_in = statistics.fmean(tokens_in)
    sd_tok_in = statistics.stdev(tokens_in)
    cv_tok_in = sd_tok_in / mean_tok_in if mean_tok_in != 0.0 else float("nan")
    skew_tok_in = _skewness([float(t) for t in tokens_in])

    mean_tok_out = statistics.fmean(tokens_out)
    sd_tok_out = statistics.stdev(tokens_out)
    cv_tok_out = sd_tok_out / mean_tok_out if mean_tok_out != 0.0 else float("nan")
    skew_tok_out = _skewness([float(t) for t in tokens_out])

    total_cost = sum(costs)

    print(f"\n=== Measured (n={len(costs)}, model={GEMINI_MODEL}, evalset={EVALSET_PATH.name}) ===")
    print(f"TOTAL REAL SPEND: ${total_cost:.6f}")
    print(
        f"cost_usd (real $, real tokens):        mean=${mean_cost:.6f}  sd=${sd_cost:.6f}  CV={cv_cost:.4f}  skew={skew_cost:.4f}"
    )
    print(
        f"tokens_input:                          mean={mean_tok_in:.1f}  sd={sd_tok_in:.1f}  CV={cv_tok_in:.4f}  skew={skew_tok_in:.4f}"
    )
    print(
        f"tokens_output:                         mean={mean_tok_out:.1f}  sd={sd_tok_out:.1f}  CV={cv_tok_out:.4f}  skew={skew_tok_out:.4f}"
    )

    out_path = Path(__file__).resolve().parent.parent / "reports" / "ad2_real_cv_measurement_gemini.json"
    out_path.write_text(
        json.dumps(
            {
                "model": GEMINI_MODEL,
                "evalset_path": str(EVALSET_PATH),
                "n_cases": len(cases),
                "n_priced_records": len(costs),
                "n_skipped": len(snapshot.skipped),
                "skipped_reasons": [s["reason"] for s in snapshot.skipped],
                "price_table": "real bundled gemini_prices.json (no override) -- real dollars",
                "total_real_spend_usd": total_cost,
                "cost_usd": {
                    "mean": mean_cost,
                    "sd": sd_cost,
                    "cv": cv_cost,
                    "skewness": skew_cost,
                    "values": costs,
                },
                "tokens_input": {
                    "mean": mean_tok_in,
                    "sd": sd_tok_in,
                    "cv": cv_tok_in,
                    "skewness": skew_tok_in,
                    "values": tokens_in,
                },
                "tokens_output": {
                    "mean": mean_tok_out,
                    "sd": sd_tok_out,
                    "cv": cv_tok_out,
                    "skewness": skew_tok_out,
                    "values": tokens_out,
                },
                "case_ids": case_ids,
                "domain_of_validity": (
                    "One evalset (36 hand-authored cases, 3 complexity tiers), one real "
                    "hosted model (gemini-2.5-flash-lite). AD2.3's representativeness check "
                    "against measure_real_cv_ollama.py's qwen2.5:7b measurement."
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
