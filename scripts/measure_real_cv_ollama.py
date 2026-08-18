"""scripts/measure_real_cv_ollama.py — AD2: measure REAL per-invocation
cost coefficient of variation (CV) and skewness from an actual agent run,
zero-cost, via local Ollama.

AD1 established the published power table can no longer stand on an
assumed CV (0.15 from the original generator, 0.6 from AC1's skew probe --
both flagged as assumptions, neither measured). This script replaces
assumption with measurement: runs a real `google-adk` `LlmAgent` (a real
model, real tokenization, real output-length variance) against a 36-case
evalset spanning three genuinely different complexity tiers
(`reports/ad2_evalset.json`), captures REAL per-invocation token counts via
the shipped `TraceGaugeUsagePlugin` + `UsageStore` + `snapshot` pipeline
(the exact mechanism a real user's CI gate uses -- not a bespoke measurement
path), and computes empirical CV and skewness over the resulting costs.

**Zero-cost**: the model is `ollama_chat/qwen2.5:7b`, run locally -- no
network call, no API key. Real dollars are undefined for a local model
(marginal API cost is genuinely $0), so this script registers a SYNTHETIC
non-zero price table entry (mirrors gemini-2.5-flash-lite's published
rate: $0.10/$0.40 per Mtok in/out -- chosen only so `cost_usd` has a
representative-magnitude figure to compute CV/skew over; the dollar VALUE
is notional) via `ADK_TRACEGAUGE_PRICE_TABLE`. The important, real
quantity this produces is the TOKEN COUNT variance/skew, not the notional
dollar figure itself -- see AD2.2's own framing ("real token variance even
if the dollars are notional").

**Domain of validity (AD2.4)**: one evalset (36 cases, hand-authored by
this script's author, not sampled from a real production workload), one
local model (`qwen2.5:7b`, 7B-parameter class). This is NOT a general claim
about "real per-invocation ADK cost variance" — it is a measurement of
THIS evalset against THIS model, reported as such. A different evalset, a
different (especially: hosted, RLHF-tuned-for-conciseness, or much
larger/smaller) model could show a materially different CV/skew. If this
local measurement is later found not representative of a real hosted-model
run (AD2.3), that gap is named explicitly, not glossed over.

Run: ``uv run python scripts/measure_real_cv_ollama.py``
Requires: local Ollama running, ``ollama_chat/qwen2.5:7b`` pulled.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

OLLAMA_MODEL = "ollama_chat/qwen2.5:7b"
SYNTHETIC_INPUT_USD_PER_MTOK = 0.10
SYNTHETIC_OUTPUT_USD_PER_MTOK = 0.40
"""Mirrors gemini-2.5-flash-lite's published rate -- chosen only to give
cost_usd a representative-magnitude figure; the dollar value is notional,
the token counts underneath it are real. See module docstring."""

EVALSET_PATH = Path(__file__).resolve().parent.parent / "reports" / "ad2_evalset.json"


def _build_synthetic_price_table(bundled_path: Path, out_path: Path) -> None:
    """Copies the bundled Gemini/Claude/GPT price table verbatim, then
    overrides ONLY the `__local_zero_cost__` entry's rates to the
    synthetic, non-zero values above -- every other entry stays real, in
    case a future run wants to compare against a hosted model too."""
    table = json.loads(bundled_path.read_text(encoding="utf-8"))
    table["models"]["__local_zero_cost__"] = {
        "input_usd_per_mtok": SYNTHETIC_INPUT_USD_PER_MTOK,
        "output_usd_per_mtok": SYNTHETIC_OUTPUT_USD_PER_MTOK,
        "source_url": "n/a -- AD2 synthetic override for local-model CV/skew measurement",
        "fetched_on": "2026-08-17",
        "note": (
            "AD2: overridden from the bundled $0.00/$0.00 entry to a synthetic "
            "gemini-2.5-flash-lite-shaped rate, so a local Ollama run produces a "
            "representative-magnitude notional cost_usd. The real measured quantity is "
            "the TOKEN COUNT distribution, not this dollar figure -- see "
            "scripts/measure_real_cv_ollama.py's module docstring."
        ),
    }
    out_path.write_text(json.dumps(table, indent=2), encoding="utf-8")


# Price-table env vars must be set before any adk_tracegauge pricing import
# triggers load_gemini_prices()'s process-wide cache.
_bundled = _SRC / "adk_tracegauge" / "data" / "gemini_prices.json"
_synthetic_table_path = Path(tempfile.gettempdir()) / "ad2_synthetic_prices.json"
_build_synthetic_price_table(_bundled, _synthetic_table_path)
os.environ["ADK_TRACEGAUGE_PRICE_TABLE"] = str(_synthetic_table_path)
os.environ["ADK_TRACEGAUGE_ASSUME_LOCAL"] = "ollama_chat/"

from google.adk.agents.llm_agent import LlmAgent  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from adk_tracegauge._plugin import TraceGaugeUsagePlugin  # noqa: E402
from adk_tracegauge._store import UsageStore  # noqa: E402
from adk_tracegauge.snapshot import read_snapshot, write_snapshot  # noqa: E402


def _skewness(values: list[float]) -> float:
    """Sample skewness (Fisher-Pearson standardized third moment,
    bias-adjusted -- the same definition ``scipy.stats.skew(bias=False)``
    reports), stdlib-only."""
    n = len(values)
    if n < 3:
        return float("nan")
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    if sd == 0.0:
        return 0.0
    m3 = sum((x - mean) ** 3 for x in values) / n
    g1 = m3 / (sd**3)
    # Bias adjustment (matches scipy's bias=False / Fisher-Pearson standardized
    # moment coefficient with small-sample correction).
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
    app_name = "ad2_real_cv_probe"
    agent = LlmAgent(
        name=app_name,
        model=LiteLlm(model=OLLAMA_MODEL),
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
    print(f"Running {len(cases)} real cases against {OLLAMA_MODEL} (local, zero-cost)...")

    store = asyncio.run(_run_evalset(cases))

    tmp_path = Path(tempfile.gettempdir()) / "ad2_real_cv_snapshot.json"
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

    print(f"\n=== Measured (n={len(costs)}, model={OLLAMA_MODEL}, evalset={EVALSET_PATH.name}) ===")
    print(
        f"cost_usd (synthetic $, real tokens):  mean=${mean_cost:.6f}  sd=${sd_cost:.6f}  CV={cv_cost:.4f}  skew={skew_cost:.4f}"
    )
    print(
        f"tokens_input:                          mean={mean_tok_in:.1f}  sd={sd_tok_in:.1f}  CV={cv_tok_in:.4f}  skew={skew_tok_in:.4f}"
    )
    print(
        f"tokens_output:                         mean={mean_tok_out:.1f}  sd={sd_tok_out:.1f}  CV={cv_tok_out:.4f}  skew={skew_tok_out:.4f}"
    )

    out_path = Path(__file__).resolve().parent.parent / "reports" / "ad2_real_cv_measurement.json"
    out_path.write_text(
        json.dumps(
            {
                "model": OLLAMA_MODEL,
                "evalset_path": str(EVALSET_PATH),
                "n_cases": len(cases),
                "n_priced_records": len(costs),
                "n_skipped": len(snapshot.skipped),
                "skipped_reasons": [s["reason"] for s in snapshot.skipped],
                "synthetic_price_table": {
                    "input_usd_per_mtok": SYNTHETIC_INPUT_USD_PER_MTOK,
                    "output_usd_per_mtok": SYNTHETIC_OUTPUT_USD_PER_MTOK,
                    "note": "notional dollars, real token counts -- see module docstring",
                },
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
                    "One evalset (36 hand-authored cases, 3 complexity tiers), one local "
                    "model (qwen2.5:7b, 7B-parameter class). Not a general claim about real "
                    "per-invocation ADK cost variance -- see module docstring."
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
