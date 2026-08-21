"""examples/05_hand_rolled_session_id_pairing.py -- verifies, live, a claim
about the `session_id` pairing fallback documented in snapshot.py's module
docstring and the README's Known limitations section: that a hand-rolled
`Runner` harness which explicitly pins `session_id` across a baseline and a
current run gets `--mode auto`'s paired comparison, WITHOUT going through
`adk eval`/`AgentEvaluator` at all (no `.evalset.json`, no `eval_history`
join, no `--eval-history` flag).

WHY THIS SCRIPT EXISTS
    `examples/04_paired_mode_via_adk_eval_cli.py` proves the PRIMARY pairing
    path (`eval_case_id`, recovered from a real `adk eval` CLI run's
    `.evalset_result.json`). This script proves the DIFFERENT, FALLBACK path:
    `session_id`-keyed pairing, reachable only when a caller drives ADK's own
    `Runner` directly and pins the same `session_id` string across two
    separate runs (`runner.run_async(session_id=..., ...)`) -- see
    `snapshot.py`'s module docstring, point 2 under "the fix", and the
    README's "Known limitations" section for why `session_id` is a fallback,
    not the primary key: `adk eval` regenerates a fresh random `session_id`
    on every run regardless of this wiring, so this path is unreachable from
    the `adk eval` CLI -- it exists specifically for a caller willing to
    drive `Runner` (or `InMemoryRunner`) by hand.

WHAT THIS SCRIPT ACTUALLY DOES (all real, nothing simulated)
    1. Builds two separate `InMemoryRunner`s -- "baseline" and "current" --
       each wired with `TraceGaugeUsagePlugin(store=...)` via the documented
       `after_model_callback` quickstart pattern (README, "Use with agent"),
       against a deterministic fake `BaseLlm` (no API key, no live model
       call, no cost -- see `_CaseDependentFakeLlm`).
    2. For 32 cases (n=32, above the real default `--min-n=30` -- a genuine,
       gate-passing paired verdict, not a demo that bypasses the real
       refusal floor), creates a session via `session_service.create_session`
       and calls `runner.run_async(user_id=..., session_id=f"case-{i}", ...)`
       -- the SAME `session_id` string reused, unchanged, for both the
       baseline and current runner. This is the actual claim under test: does
       reusing this string across two independent `Runner`s make
       `adk-tracegauge check` resolve `mode=paired, key=session_id`?
    3. The "current" variant's fake LLM adds a fixed per-call prompt-token
       bump on top of every case's own baseline level -- a real, uniform,
       injected cost regression (same generator shape as example 04's).
    4. Snapshots each run's `UsageStore` via `write_snapshot` (the real
       `snapshot.py` function -- no `--eval-history`, no `eval_case_id` join
       of any kind), then runs `adk-tracegauge check` (`--mode auto`, the
       shipped default -- no `--mode paired` override) via the real CLI
       entrypoint (`adk_tracegauge._cli.main`, in-process) against the two
       snapshots, and prints the ACTUAL, UNEDITED output.

RESULT (verified, not assumed): the claim holds. `adk-tracegauge check`
    resolves `mode=paired (key=session_id, 32 overlapping session_ids
    matched between baseline and current)` -- paired mode, reached entirely
    through a hand-rolled `Runner` harness, with zero `adk eval` involvement.

HOW TO RUN
    uv run python examples/05_hand_rolled_session_id_pairing.py

EXPECTED OUTPUT (real numbers -- reproduce exactly given the seeds/generator
    below; captured fresh this session, `google-adk==2.6.3`):
    baseline sessions captured: 32
    current sessions captured: 32

    adk-tracegauge check: mode=paired (key=session_id, 32 overlapping session_ids matched between baseline and current)
    adk-tracegauge check [method=paired]: n_baseline=32 n_current=32 (min_n=30)
      mean_baseline=$0.010611  mean_current=$0.014211
      achieved power: minimum reliably-detectable effect at 80% power, given this run's observed
      variance/n, is ~$0.000000 (+0.00% of mean baseline) [normal approximation to the bootstrap CI
      -- see _regression.py module docstring for validated accuracy]
      observed effect: +0.003600 USD (+33.93%), 98% CI [+0.003600, +0.003600] (n_boot=10000, seed=42)
      statistically_significant=True practically_significant=True (floors: min_effect_usd=0.000100 OR
      min_effect_pct=5.00%)
      REGRESSION: cost increased significantly (CI excludes zero) AND the increase clears the
      configured practical-significance floor.

    adk-tracegauge check exit code: 1
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from adk_tracegauge import TraceGaugeUsagePlugin, UsageStore
from adk_tracegauge._cli import main as tracegauge_main
from adk_tracegauge.snapshot import write_snapshot

N_CASES = 32  # above the real default --min-n=30 -- a genuine paired verdict
CASE_LEVEL_BASE_TOKENS = 5_000
CASE_LEVEL_STRIDE = 4_723  # arbitrary deterministic spread -- NOT hash(), which
# is PYTHONHASHSEED-randomized per process and would make this script's own
# output non-reproducible run to run (same rationale as example 04).
CASE_LEVEL_MOD = 25_000
REGRESSION_BUMP_PROMPT_TOKENS = 6_000  # the "current" variant's uniform per-case regression

_CASE_RE = re.compile(r"case (\d+)")


def _case_level_prompt_tokens(case_idx: int) -> int:
    return CASE_LEVEL_BASE_TOKENS + (case_idx * CASE_LEVEL_STRIDE) % CASE_LEVEL_MOD


class _CaseDependentFakeLlm(BaseLlm):
    """A fake model whose token usage is DETERMINISTIC and depends on which
    case it was asked -- real case-to-case cost heterogeneity, plus a fixed
    per-call regression bump baked into the "current" variant only. No
    network call, no API key, no real cost."""

    model: str = "case-dependent-fake-model"
    regression_bump: int = 0

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["case-dependent-fake-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = "".join(p.text or "" for c in (llm_request.contents or []) for p in (c.parts or []))
        match = _CASE_RE.search(text)
        case_idx = int(match.group(1)) if match else 0
        prompt_tokens = _case_level_prompt_tokens(case_idx) + self.regression_bump
        yield LlmResponse(
            model_version="gemini-2.5-flash",
            content=genai_types.Content(parts=[genai_types.Part(text="4")], role="model"),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt_tokens,
                candidates_token_count=50,
                cached_content_token_count=0,
                total_token_count=prompt_tokens + 50,
            ),
        )


async def _run_variant(app_name: str, regression_bump: int, store: UsageStore) -> None:
    """Drives a real `InMemoryRunner` through N_CASES turns, pinning the same
    `session_id` string (`f"case-{i}"`) this function's caller also uses for
    the OTHER variant -- the actual mechanism under test. Captures usage into
    `store` via the documented `after_model_callback` quickstart wiring
    (README, "Use with agent"); no App/plugin-list harness needed for this
    (that pattern is only required for sub-agent cost rollup, per the
    README's "Sub-agent delegation" section -- out of scope here)."""
    plugin = TraceGaugeUsagePlugin(store=store)
    agent = LlmAgent(
        name=app_name,
        model=_CaseDependentFakeLlm(regression_bump=regression_bump),
        instruction="Answer the question.",
        after_model_callback=plugin.after_model_callback,
    )
    runner = InMemoryRunner(agent=agent, app_name=app_name, plugins=[plugin])
    user_id = "u"
    for i in range(N_CASES):
        session_id = f"case-{i}"  # <-- pinned identically across baseline/current
        await runner.session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        new_message = genai_types.Content(
            parts=[genai_types.Part(text=f"case {i}: what is 2+2?")], role="user"
        )
        async for _event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=new_message
        ):
            pass  # draining the async generator is what actually drives the run


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        baseline_store = UsageStore()
        current_store = UsageStore()

        asyncio.run(_run_variant("baseline_app", 0, baseline_store))
        asyncio.run(_run_variant("current_app", REGRESSION_BUMP_PROMPT_TOKENS, current_store))

        baseline_path = tmp_path / "baseline.json"
        current_path = tmp_path / "current.json"
        baseline_snapshot = write_snapshot(baseline_store, baseline_path)
        current_snapshot = write_snapshot(current_store, current_path)
        print(f"baseline sessions captured: {len(baseline_snapshot.records)}")
        print(f"current sessions captured: {len(current_snapshot.records)}\n")

        # --mode auto (the shipped default) -- no --eval-history, no --mode
        # paired override. This is the real CLI entrypoint, in-process.
        exit_code = tracegauge_main(
            ["check", "--baseline", str(baseline_path), "--current", str(current_path)]
        )
        print(f"\nadk-tracegauge check exit code: {exit_code}")
        return exit_code


if __name__ == "__main__":
    # BD3: same bug 03 had -- main() never called sys.exit(), so this
    # process always exited 0 regardless of the real check result.
    sys.exit(main())
