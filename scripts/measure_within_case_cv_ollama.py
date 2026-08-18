"""scripts/measure_within_case_cv_ollama.py — Q1: the shipped default
(paired mode) depends on WITHIN-CASE cost CV (see AD1's README section and
AD1's own generator-confound finding), and AD2 only measured ACROSS-case
CV (one run per case). This script closes that gap: runs the identical
36-case evalset (`reports/ad2_evalset.json`) TWICE against local Ollama,
same model, matches each case across both runs by case_id, and computes
the pooled within-case standard deviation/CV from the duplicate-
measurement design (a standard repeatability estimator, not a novel one).

**Design**: for each case i, two independent draws A_i, B_i (same prompt,
same model, two separate `run_async` calls -- distinct session_ids so
InMemorySessionService doesn't collide, `-runA`/`-runB` suffixes stripped
before grouping). Assuming A_i and B_i are iid draws from the same
within-case distribution (mean d_i, variance sigma_i^2), the delta
Delta_i = B_i - A_i has Var(Delta_i) = 2*sigma_i^2 (independent draws),
so a pooled repeatability variance estimate across all n cases is
mean(Delta_i^2) / 2 (unbiased for the average sigma_i^2 if E[Delta_i]=0 --
checked explicitly below, not assumed). Pooled CV = pooled_sd / mean(cost).

**Skewness is NOT reported for the within-case distribution.** A/B
difference of two iid draws is symmetric around zero BY CONSTRUCTION
regardless of the underlying distribution's own skewness (a textbook
fact, not a limitation of this specific run) -- the sign/magnitude of
Delta_i tells you nothing about whether the underlying per-case cost
distribution itself is symmetric or skewed. Estimating within-case
skewness honestly requires >=3 repeats per case (a real third-moment
estimate), which this script does not do (Q1.2 asked for two runs) --
flagged here rather than computing a number that would look precise but
measure nothing real.

**Sampling settings (Q1.4)**: no `generate_content_config` override is
passed anywhere in this pipeline (`LlmAgent.generate_content_config`
defaults to `None` -- confirmed by reading the field default directly),
so every request reaches Ollama with no request-level temperature/top_p/
top_k override. `ollama show qwen2.5:7b --modelfile` has zero `PARAMETER`
lines (confirmed live) -- the model carries no Modelfile-level override
either. This means Ollama's own server default applies: temperature=0.8
(non-zero, real sampling noise), NOT a deterministic/greedy decode. This
is the opposite of the understating-variance case Q1.4 warned about --
a deterministic (temperature=0) setup WOULD have understated real
run-to-run variance by suppressing sampling noise entirely; this run does
not have that failure mode. It may still not match a real hosted model's
own default temperature/sampling config, which is a separate,
representativeness question already flagged UNVERIFIED in AD2.3.

Run: ``uv run python scripts/measure_within_case_cv_ollama.py``
Requires: local Ollama running, ``ollama_chat/qwen2.5:7b`` pulled.
"""

from __future__ import annotations

import asyncio
import json
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
EVALSET_PATH = Path(__file__).resolve().parent.parent / "reports" / "ad2_evalset.json"


def _build_synthetic_price_table(bundled_path: Path, out_path: Path) -> None:
    table = json.loads(bundled_path.read_text(encoding="utf-8"))
    table["models"]["__local_zero_cost__"] = {
        "input_usd_per_mtok": SYNTHETIC_INPUT_USD_PER_MTOK,
        "output_usd_per_mtok": SYNTHETIC_OUTPUT_USD_PER_MTOK,
        "source_url": "n/a -- Q1 synthetic override, same as AD2",
        "fetched_on": "2026-08-18",
        "note": "Q1: identical synthetic override to AD2's, for a directly comparable cost figure.",
    }
    out_path.write_text(json.dumps(table, indent=2), encoding="utf-8")


_bundled = _SRC / "adk_tracegauge" / "data" / "gemini_prices.json"
_synthetic_table_path = Path(tempfile.gettempdir()) / "q1_synthetic_prices.json"
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


async def _run_case(runner: InMemoryRunner, app_name: str, session_id: str, prompt: str) -> None:
    user_id = "u"
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    new_message = genai_types.Content(parts=[genai_types.Part(text=prompt)], role="user")
    async for _event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        pass


async def _run_pass(cases: list[dict], pass_label: str) -> UsageStore:
    store = UsageStore()
    plugin = TraceGaugeUsagePlugin(store=store)
    app_name = f"q1_within_case_probe_{pass_label}"
    agent = LlmAgent(
        name=app_name,
        model=LiteLlm(model=OLLAMA_MODEL),
        instruction="Answer the user's question directly and completely.",
        after_model_callback=plugin.after_model_callback,  # type: ignore[arg-type]
    )
    runner = InMemoryRunner(agent=agent, app_name=app_name, plugins=[plugin])
    for i, case in enumerate(cases):
        session_id = f"{case['id']}-{pass_label}"
        print(f"  [{pass_label} {i + 1}/{len(cases)}] {case['id']} ({case['tier']})...", flush=True)
        await _run_case(runner, app_name, session_id, case["prompt"])
    return store


def _costs_by_case(store: UsageStore, pass_label: str) -> dict[str, float]:
    tmp = Path(tempfile.gettempdir()) / f"q1_snapshot_{pass_label}.json"
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
    print(f"Running {len(cases)} cases TWICE (runA, runB) against {OLLAMA_MODEL}...")

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

    n = len(paired_ids)
    mean_delta = statistics.fmean(deltas)
    mean_cost = statistics.fmean(all_vals)

    # Pooled repeatability variance from the duplicate-measurement design:
    # Var(Delta_i) = 2*sigma^2 (independent draws) -> sigma^2 = mean(Delta_i^2)/2.
    mean_sq_delta = statistics.fmean([d**2 for d in deltas])
    pooled_within_case_var = mean_sq_delta / 2.0
    pooled_within_case_sd = pooled_within_case_var**0.5
    within_case_cv = pooled_within_case_sd / mean_cost if mean_cost != 0.0 else float("nan")

    # Bias check: E[Delta_i] should be ~0 if runA/runB are exchangeable (no
    # systematic drift, e.g. Ollama warm-up/caching effects between passes).
    delta_sd = statistics.stdev(deltas) if n > 1 else float("nan")
    bias_t_stat = (
        (mean_delta / (delta_sd / (n**0.5))) if n > 1 and delta_sd != 0.0 else float("nan")
    )

    print(f"\n=== Within-case measurement (n={n} matched cases, model={OLLAMA_MODEL}) ===")
    print(f"mean(cost) across both runs: ${mean_cost:.6f}")
    print(
        f"mean(delta) = mean(runB - runA): ${mean_delta:.6f}  (bias check t-stat: {bias_t_stat:.3f})"
    )
    print(f"pooled within-case sd (duplicate-measurement estimator): ${pooled_within_case_sd:.6f}")
    print(f"within-case CV: {within_case_cv:.4f}")
    print("within-case skewness: NOT ESTIMABLE from 2 repeats/case -- see module docstring.")

    out_path = Path(__file__).resolve().parent.parent / "reports" / "q1_within_case_cv.json"
    out_path.write_text(
        json.dumps(
            {
                "model": OLLAMA_MODEL,
                "evalset_path": str(EVALSET_PATH),
                "n_cases_total": len(cases),
                "n_matched": n,
                "missing_cases": missing,
                "skipped_runA": skipped_a,
                "skipped_runB": skipped_b,
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
                    "regardless of the underlying distribution's skewness. Needs >=3 repeats "
                    "per case for a real third-moment estimate."
                ),
                "sampling_settings": {
                    "generate_content_config_override": None,
                    "ollama_modelfile_parameter_overrides": [],
                    "ollama_server_default_temperature": 0.8,
                    "note": (
                        "No temperature/top_p/top_k override anywhere in this pipeline -- "
                        "Ollama's own non-zero server default (temperature=0.8) applies. "
                        "NOT a deterministic/greedy decode -- see module docstring."
                    ),
                },
                "domain_of_validity": (
                    "One evalset (36 hand-authored cases), one local model (qwen2.5:7b), "
                    "two repeats per case. Not a general claim -- see AD2's own domain-of-"
                    "validity note, which applies identically here."
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
